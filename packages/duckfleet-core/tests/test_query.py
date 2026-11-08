"""Query layer (plan v0.2 Phase 1): sandbox, limits, timeout, export, preview."""

from __future__ import annotations

import _thread
import io
import os
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from duckfleet_core import query
from duckfleet_core.query import (
    QueryConfig,
    QueryError,
    classify_error,
    connection_config,
    export_query,
    open_readonly,
    preview_table,
    run_query,
    table_columns,
)
from duckfleet_core.tempdir import file_temp_dir

CROSS_JOIN = "select sum(a.i * b.i) from range(200000) a(i), range(200000) b(i)"
WEIRD_SCHEMA = 'we"ird é'
WEIRD_TABLE = 't;x "q" ü\n'


@pytest.fixture(scope="module")
def qdb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("query") / "q é.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE t AS SELECT
                TIMESTAMP '2026-01-01 00:00:00' + to_seconds(i) AS ts,
                TIMESTAMPTZ '2026-01-01 00:00:00+00' + to_seconds(i) AS tstz,
                sin(i) AS x,
                'row ' || i::VARCHAR AS name,
                (i % 1000)::DECIMAL(18,3) AS amt,
                [i::INTEGER, (i + 1)::INTEGER] AS arr
            FROM range(10000) r(i)
            """
        )
        con.execute("CREATE VIEW v AS SELECT ts, x FROM t WHERE x > 0.5")
        con.execute(f"CREATE SCHEMA {query.quote_ident(WEIRD_SCHEMA)}")
        con.execute(
            f"CREATE TABLE {query.qualified_name(WEIRD_SCHEMA, WEIRD_TABLE)} "
            '("a b" INTEGER, "é.ü" VARCHAR, "*" INTEGER, "--c" INTEGER)'
        )
        con.execute(
            f"INSERT INTO {query.qualified_name(WEIRD_SCHEMA, WEIRD_TABLE)} "
            "SELECT i, 'é' || i::VARCHAR, -i, i * 2 FROM range(5) r(i)"
        )
    finally:
        con.close()
    return path


@pytest.fixture
def cfg() -> QueryConfig:
    return QueryConfig()


# ---------------------------------------------------------------- config


def test_defaults_match_the_plan() -> None:
    config = QueryConfig()
    assert (config.threads, config.memory_limit_mb) == (None, None)
    assert config.max_rows == 10_000 and config.max_download_rows == 1_000_000
    assert config.timeout_s == 60.0 and config.preview_rows == 1_000
    assert config.preview_max_columns == 50
    assert config.stats_row_threshold == 20_000_000 and config.stats_sample_rows == 1_000_000


def test_threads_and_memory_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 24)
    assert QueryConfig().resolved_threads() == 4
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    assert QueryConfig().resolved_threads() == 2
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert QueryConfig().resolved_threads() == 1
    assert QueryConfig(threads=3).resolved_threads() == 3

    class FakeMemory:
        total = 64 * 1024**3

    monkeypatch.setattr(query.psutil, "virtual_memory", lambda: FakeMemory)
    assert QueryConfig().resolved_memory_limit_mb() == 4096  # RAM / 4 capped
    FakeMemory.total = 8 * 1024**3
    assert QueryConfig().resolved_memory_limit_mb() == 2048
    FakeMemory.total = 1 * 1024**3
    assert QueryConfig().resolved_memory_limit_mb() == 512  # floor
    assert QueryConfig(memory_limit_mb=700).resolved_memory_limit_mb() == 700


def test_connection_config_is_the_sandbox(tmp_path: Path) -> None:
    root = tmp_path / "cache" / "tmp" / "7"
    db = tmp_path / "some.duckdb"
    config = connection_config(
        QueryConfig(threads=2, memory_limit_mb=600, temp_dir=str(root)), db
    )
    temp = file_temp_dir(root, db)  # W-01: the leaf of this file under the process folder
    assert config == {
        "threads": 2,
        "memory_limit": "600MB",
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "enable_external_access": False,
        "temp_directory": str(temp),
        "lock_configuration": True,
    }
    assert temp.is_dir()  # created before connecting
    # DuckDB refuses the temp directory once external access is off: the order matters.
    keys = list(config)
    assert keys.index("temp_directory") > keys.index("enable_external_access")


def test_the_temp_directory_defaults_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(query, "default_cache_dir", lambda: tmp_path / "default-cache")
    expected = tmp_path / "default-cache" / "tmp" / str(os.getpid())
    assert QueryConfig().resolved_temp_dir() == expected
    assert QueryConfig(temp_dir=str(tmp_path / "x")).resolved_temp_dir() == tmp_path / "x"


def test_the_temp_directory_takes_effect(qdb: Path, tmp_path: Path) -> None:
    root = tmp_path / "cache" / "tmp" / "1"
    with open_readonly(qdb, QueryConfig(temp_dir=str(root))) as con:
        (setting,) = con.execute("select current_setting('temp_directory')").fetchone()
    assert Path(setting) == file_temp_dir(root, qdb)


def test_a_spilling_query_writes_nothing_beside_the_scanned_file(tmp_path: Path) -> None:
    """V-01: DuckDB's default temp directory is ``<database path>.tmp``, beside the scanned file."""
    scanned = tmp_path / "scanned"
    scanned.mkdir()
    db = scanned / "spill.duckdb"
    duckdb.connect(str(db)).close()
    root = tmp_path / "cache" / "tmp" / "99"
    temp = file_temp_dir(root, db)  # W-01: the spill files go into the file's own leaf
    beside: set[str] = set()
    in_temp: set[str] = set()
    stop = threading.Event()

    def watch() -> None:
        while not stop.is_set():
            beside.update(entry.name for entry in scanned.iterdir())
            try:
                in_temp.update(entry.name for entry in temp.iterdir())
            except OSError:  # not created yet
                pass
            time.sleep(0.005)

    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        config = QueryConfig(memory_limit_mb=64, threads=2, temp_dir=str(root), timeout_s=120)
        sql = "select count(*) from (select hash(i) h from range(30000000) t(i) order by h)"
        assert run_query(db, sql, config).rows == [(30_000_000,)]
    finally:
        stop.set()
        watcher.join()
    assert beside == {"spill.duckdb"}
    assert any(name.startswith("duckdb_temp_storage") for name in in_temp), in_temp


# ---------------------------------------------------------------- run_query


def test_result_columns_types_and_rows(qdb: Path, cfg: QueryConfig) -> None:
    result = run_query(qdb, "select * from t order by ts", cfg)
    assert result.columns == ["ts", "tstz", "x", "name", "amt", "arr"]
    assert result.types == [
        "TIMESTAMP",
        "TIMESTAMP WITH TIME ZONE",
        "DOUBLE",
        "VARCHAR",
        "DECIMAL(18,3)",
        "INTEGER[]",
    ]
    assert len(result.rows) == 10_000 and not result.truncated  # exactly the cap
    assert result.statements == 1 and result.elapsed_s >= 0
    first = result.rows[0]
    assert first[0] == datetime(2026, 1, 1)
    assert first[1] == datetime(2026, 1, 1, tzinfo=UTC)  # needs pytz (D-022)
    assert first[3] == "row 0" and first[4] == Decimal("0.000") and first[5] == [0, 1]


def test_max_rows_truncates(qdb: Path, cfg: QueryConfig) -> None:
    result = run_query(qdb, "select i from range(100) r(i)", cfg, max_rows=5)
    assert len(result.rows) == 5 and result.truncated
    assert run_query(qdb, "select i from range(5) r(i)", cfg, max_rows=5).truncated is False
    assert run_query(qdb, "select i from range(4) r(i)", cfg, max_rows=5).truncated is False
    small = QueryConfig(max_rows=3)
    assert len(run_query(qdb, "select i from range(10) r(i)", small).rows) == 3


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE new_table (i INTEGER)",
        "INSERT INTO t VALUES (NULL, NULL, 1.0, 'x', 1, [1])",
        "UPDATE t SET x = 0",
        "DELETE FROM t",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN z INTEGER",
        "CREATE VIEW w AS SELECT 1",
    ],
)
def test_write_statements_are_read_only_errors(qdb: Path, cfg: QueryConfig, sql: str) -> None:
    with pytest.raises(QueryError) as caught:
        run_query(qdb, sql, cfg)
    assert caught.value.kind == "read_only"
    assert "read-only mode" in caught.value.message
    assert run_query(qdb, "select count(*) from t", cfg).rows == [(10_000,)]  # nothing changed


@pytest.mark.parametrize(
    "sql",
    [
        "COPY t TO 'duckfleet_should_not_exist.csv'",
        "ATTACH 'duckfleet_other.db' AS other",
        "SELECT * FROM read_csv('duckfleet_nothing.csv')",
        "SELECT * FROM read_parquet('duckfleet_nothing.parquet')",
        "LOAD httpfs",
        "INSTALL httpfs",
        "SET threads = 8",
        "SET enable_external_access = true",
        "SET memory_limit = '99GB'",
        "SET lock_configuration = false",
        "EXPORT DATABASE 'duckfleet_export_dir'",
    ],
)
def test_file_access_extensions_and_settings_are_blocked(
    qdb: Path, cfg: QueryConfig, sql: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(QueryError) as caught:
        run_query(qdb, sql, cfg)
    assert caught.value.kind in ("blocked", "read_only"), caught.value
    if sql.startswith(("COPY", "ATTACH", "SELECT * FROM read", "LOAD", "INSTALL", "SET")):
        assert caught.value.kind == "blocked"
    assert list(tmp_path.iterdir()) == []  # nothing was written
    assert run_query(qdb, "select 1", cfg).rows == [(1,)]  # a fresh connection still works


def test_sql_errors(qdb: Path, cfg: QueryConfig) -> None:
    for sql in (
        "selec 1",
        "select * from no_such_table",
        "select no_such_column from t",
        "select 'abc'::INTEGER",
        "select * from t where",
    ):
        with pytest.raises(QueryError) as caught:
            run_query(qdb, sql, cfg)
        assert caught.value.kind == "sql", (sql, caught.value)
        assert caught.value.message


def test_an_empty_statement_is_an_sql_error(qdb: Path, cfg: QueryConfig) -> None:
    for sql in ("", "   ", "-- only a comment", ";"):
        with pytest.raises(QueryError) as caught:
            run_query(qdb, sql, cfg)
        assert caught.value.kind == "sql"


def test_the_message_is_cut_at_1000_characters(qdb: Path, cfg: QueryConfig) -> None:
    with pytest.raises(QueryError) as caught:
        run_query(qdb, "select " + "x" * 3000, cfg)
    assert len(caught.value.message) <= 1000


def test_temp_tables_live_and_die_with_the_connection(qdb: Path, cfg: QueryConfig) -> None:
    both = run_query(qdb, "create temp table z as select 41 + 1 as a; select a from z", cfg)
    assert both.statements == 2 and both.rows == [(42,)] and both.columns == ["a"]
    with pytest.raises(QueryError) as caught:
        run_query(qdb, "select * from z", cfg)
    assert caught.value.kind == "sql"  # the temp table is gone with its connection


def test_a_statement_without_a_result_set(qdb: Path, cfg: QueryConfig) -> None:
    result = run_query(qdb, "create temp table y (i integer)", cfg)
    assert result.rows == [] and not result.truncated  # DuckDB reports a 'Count' column, no rows


def test_describe_show_summarize_and_explain_work(qdb: Path, cfg: QueryConfig) -> None:
    assert ("t",) in run_query(qdb, "show tables", cfg).rows
    assert run_query(qdb, "describe t", cfg).rows[0][0] == "ts"
    assert len(run_query(qdb, "summarize t", cfg).rows) == 6
    assert run_query(qdb, "explain select * from t", cfg).rows
    assert run_query(qdb, "pragma database_size", cfg).rows


def test_timeout_interrupts_and_the_next_query_works(qdb: Path) -> None:
    config = QueryConfig(timeout_s=0.5)
    started = time.perf_counter()
    with pytest.raises(QueryError) as caught:
        run_query(qdb, CROSS_JOIN, config)
    assert caught.value.kind == "timeout"
    assert "0.5 s" in caught.value.message
    assert time.perf_counter() - started < 15
    assert run_query(qdb, "select count(*) from t", config).rows == [(10_000,)]


def test_timeout_also_covers_fetching_a_streaming_result(qdb: Path) -> None:
    config = QueryConfig(timeout_s=0.5)
    with pytest.raises(QueryError) as caught:
        run_query(qdb, "select a.i, b.i from range(100000) a(i), range(100000) b(i)", config,
                  max_rows=5_000_000_000)  # fmt: skip
    assert caught.value.kind == "timeout"


def test_a_timeout_while_fetching_text_and_lists_is_a_timeout(qdb: Path) -> None:
    """V-02: an interrupt during the fetch of VARCHAR or list columns is not an ``sql`` error."""
    sql = (
        "select a.i, 'row ' || b.i::VARCHAR, [a.i, b.i] "
        "from range(100000) a(i), range(100000) b(i)"
    )
    for _ in range(5):
        with pytest.raises(QueryError) as caught:
            run_query(qdb, sql, QueryConfig(timeout_s=0.3), max_rows=5_000_000_000)
        assert caught.value.kind == "timeout", caught.value


def test_ctrl_c_interrupts_the_query_instead_of_waiting_for_it(qdb: Path) -> None:
    """V-04: a KeyboardInterrupt during the wait must interrupt the running query."""
    started = time.perf_counter()
    timer = threading.Timer(0.5, _thread.interrupt_main)
    with open_readonly(qdb, QueryConfig()) as con:
        timer.start()
        try:
            with pytest.raises(KeyboardInterrupt):
                query._with_timeout(con, lambda: con.execute(CROSS_JOIN).fetchall(), 120)
        finally:
            timer.cancel()
        assert con.execute("select 1").fetchone() == (1,)  # the connection is still usable
    assert time.perf_counter() - started < 20


@pytest.mark.parametrize(
    "echoed",
    ["read-only mode", "no such file", "configuration has been locked",
     "disabled by configuration", "interrupt error"],
)
def test_text_echoed_from_the_sql_cannot_set_the_kind(
    qdb: Path, cfg: QueryConfig, echoed: str
) -> None:
    """V-06: the exception type decides before the wording."""
    with pytest.raises(QueryError) as caught:
        run_query(qdb, f"select '{echoed}'::INTEGER", cfg)
    assert caught.value.kind == "sql", caught.value


def test_the_connection_is_closed_after_an_error(qdb: Path, cfg: QueryConfig) -> None:
    """A different config for the same file only opens when no connection is left (D-021)."""
    with pytest.raises(QueryError):
        run_query(qdb, "selec 1", cfg)
    other = QueryConfig(threads=1, memory_limit_mb=700)
    assert run_query(qdb, "select 1", other).rows == [(1,)]
    with pytest.raises(QueryError):
        run_query(qdb, CROSS_JOIN, QueryConfig(timeout_s=0.3, threads=2))
    assert run_query(qdb, "select 1", cfg).rows == [(1,)]


def test_one_config_per_process_for_open_connections(qdb: Path, cfg: QueryConfig) -> None:
    with open_readonly(qdb, cfg) as first, open_readonly(qdb, cfg) as second:
        assert first.execute("select 1").fetchone() == (1,)  # identical config: fine
        assert second.execute("select 2").fetchone() == (2,)
        with pytest.raises(duckdb.ConnectionException):
            with open_readonly(qdb, QueryConfig(threads=1, memory_limit_mb=700)):
                pass


def test_a_missing_file_is_a_query_error(tmp_path: Path, cfg: QueryConfig) -> None:
    with pytest.raises(QueryError) as caught:
        run_query(tmp_path / "gone.duckdb", "select 1", cfg)
    assert caught.value.kind == "other" and caught.value.message


def test_classify_error() -> None:
    cases = [
        (duckdb.InterruptException("INTERRUPT Error: Interrupted!"), "timeout"),
        (duckdb.InvalidInputException("attached in read-only mode!"), "read_only"),
        (duckdb.PermissionException("file system operations are disabled by configuration"),
         "blocked"),
        (duckdb.InvalidInputException("the configuration has been locked"), "blocked"),
        (duckdb.PermissionException("Loading external extensions is disabled through "
                                    "configuration"), "blocked"),
        (duckdb.ParserException("syntax error"), "sql"),
        (duckdb.BinderException("no column"), "sql"),
        (duckdb.CatalogException("no table"), "sql"),
        (duckdb.ConversionException("cannot cast"), "sql"),
        (duckdb.InvalidInputException("bad value"), "sql"),
        (duckdb.IOException("disk"), "other"),
        (RuntimeError("boom"), "other"),
        # V-02: an interrupted fetch can arrive wrapped in another exception type
        (duckdb.InvalidInputException("Invalid Input Error: x INTERRUPT Error: Interrupted!"),
         "timeout"),
        # V-06: the type decides before the wording
        (duckdb.ConversionException("Could not convert string 'read-only mode' to INT32"), "sql"),
        # W-07: "interrupt error" counts for an InvalidInputException only
        (duckdb.ConversionException("Could not convert string 'interrupt error' to INT32"), "sql"),
        (duckdb.BinderException("Referenced column \"interrupt error\" not found"), "sql"),
        (duckdb.InvalidInputException(
            'Invalid Input Error: Cannot execute statement of type "INSERT" on database "a" '
            "which is attached in read-only mode!"), "read_only"),
        (duckdb.InvalidInputException(
            'Invalid Input Error: Cannot change configuration option "threads" - the '
            "configuration has been locked"), "blocked"),
        (duckdb.IOException('IO Error: Cannot open database "x" in read-only mode: database '
                            "does not exist"), "other"),
        (duckdb.InvalidInputException('Cannot open database "x" in read-only mode: database '
                                      "does not exist"), "other"),
    ]  # fmt: skip
    for exc, kind in cases:
        assert classify_error(exc).kind == kind, (exc, kind)
    existing = QueryError("timeout", "x")
    assert classify_error(existing) is existing
    assert str(QueryError("sql", "msg")) == "sql: msg"


# ---------------------------------------------------------------- export_query


def test_export_parquet_round_trip(qdb: Path, cfg: QueryConfig) -> None:
    pq = pytest.importorskip("pyarrow.parquet")
    data, rows, truncated = export_query(qdb, "select * from t order by ts", "parquet", cfg)
    assert rows == 10_000 and not truncated and len(data) > 0
    table = pq.read_table(io.BytesIO(data))
    assert table.num_rows == 10_000
    assert table.column_names == ["ts", "tstz", "x", "name", "amt", "arr"]
    assert table.column("name")[2].as_py() == "row 2"
    assert table.column("arr")[1].as_py() == [1, 2]
    assert table.column("amt")[3].as_py() == Decimal("3.000")


def test_export_csv_round_trip(qdb: Path, cfg: QueryConfig) -> None:
    pa_csv = pytest.importorskip("pyarrow.csv")
    data, rows, truncated = export_query(
        qdb, "select ts, x, name, amt from t order by ts", "csv", cfg
    )
    assert rows == 10_000 and not truncated
    assert data.startswith(b'"ts","x","name","amt"')
    table = pa_csv.read_csv(io.BytesIO(data))
    assert table.num_rows == 10_000 and table.column("name")[1].as_py() == "row 1"


def test_export_is_cut_at_the_cap(qdb: Path) -> None:
    pytest.importorskip("pyarrow")
    config = QueryConfig(max_download_rows=100)
    _data, rows, truncated = export_query(qdb, "select * from t", "parquet", config)
    assert (rows, truncated) == (100, True)
    _data, rows, truncated = export_query(qdb, "select i from range(7) r(i)", "csv", config)
    assert (rows, truncated) == (7, False)
    _data, rows, truncated = export_query(
        qdb, "select i from range(100) r(i)", "csv", config
    )
    assert (rows, truncated) == (100, False)  # exactly the cap is not truncated
    _data, rows, truncated = export_query(
        qdb, "select i from range(100) r(i)", "csv", config, max_rows=10
    )
    assert (rows, truncated) == (10, True)


def test_export_runs_the_sql_as_given(qdb: Path, cfg: QueryConfig) -> None:
    pytest.importorskip("pyarrow")
    data, rows, _ = export_query(
        qdb, "create temp table e as select 1 as a union all select 2; select * from e order by a",
        "csv", cfg,
    )  # fmt: skip
    assert rows == 2 and data == b'"a"\n1\n2\n'


def test_export_errors(qdb: Path, cfg: QueryConfig) -> None:
    pytest.importorskip("pyarrow")
    with pytest.raises(QueryError) as caught:
        export_query(qdb, "create temp table q (i integer)", "csv", cfg)
    assert caught.value.kind == "sql" and "no rows" in caught.value.message
    with pytest.raises(QueryError) as caught:
        export_query(qdb, "insert into t default values", "parquet", cfg)
    assert caught.value.kind == "read_only"
    with pytest.raises(QueryError) as caught:
        export_query(qdb, "selec", "csv", cfg)
    assert caught.value.kind == "sql"
    with pytest.raises(ValueError):
        export_query(qdb, "select 1", "xlsx", cfg)  # type: ignore[arg-type]


def test_export_csv_of_a_nested_column_says_to_use_parquet(qdb: Path, cfg: QueryConfig) -> None:
    pytest.importorskip("pyarrow")
    with pytest.raises(QueryError) as caught:
        export_query(qdb, "select arr from t", "csv", cfg)
    assert caught.value.kind == "other" and "Parquet" in caught.value.message
    _data, rows, _ = export_query(qdb, "select arr from t", "parquet", cfg)
    assert rows == 10_000


def test_export_times_out(qdb: Path) -> None:
    pytest.importorskip("pyarrow")
    with pytest.raises(QueryError) as caught:
        export_query(qdb, CROSS_JOIN, "parquet", QueryConfig(timeout_s=0.5))
    assert caught.value.kind == "timeout"


def test_export_without_pyarrow_says_how_to_install_it(
    qdb: Path, cfg: QueryConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins

    real_import = builtins.__import__

    def no_pyarrow(name, *args, **kwargs):
        if name.startswith("pyarrow"):
            raise ImportError("No module named 'pyarrow'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyarrow)
    with pytest.raises(query.MissingDependencyError, match=r"duckfleet-core\[export\]"):
        export_query(qdb, "select 1", "csv", cfg)


# ---------------------------------------------------------------- preview and columns


def test_table_columns(qdb: Path, cfg: QueryConfig) -> None:
    assert table_columns(qdb, "main", "t", cfg) == [
        ("ts", "TIMESTAMP"),
        ("tstz", "TIMESTAMP WITH TIME ZONE"),
        ("x", "DOUBLE"),
        ("name", "VARCHAR"),
        ("amt", "DECIMAL(18,3)"),
        ("arr", "INTEGER[]"),
    ]
    assert table_columns(qdb, "main", "v", cfg) == [("ts", "TIMESTAMP"), ("x", "DOUBLE")]
    assert table_columns(qdb, "main", "nope", cfg) == []


def test_preview_paging_and_truncation(qdb: Path, cfg: QueryConfig) -> None:
    page = preview_table(qdb, "main", "t", cfg, limit=10)
    assert len(page.rows) == 10 and page.truncated
    assert page.columns == ["ts", "tstz", "x", "name", "amt", "arr"]
    second = preview_table(qdb, "main", "t", cfg, limit=10, offset=10, order_by="ts")
    assert [r[3] for r in second.rows] == [f"row {i}" for i in range(10, 20)]
    last = preview_table(qdb, "main", "t", cfg, limit=10, offset=9_995, order_by="ts")
    assert len(last.rows) == 5 and not last.truncated
    beyond = preview_table(qdb, "main", "t", cfg, limit=10, offset=20_000)
    assert beyond.rows == [] and not beyond.truncated
    default = preview_table(qdb, "main", "t", QueryConfig(preview_rows=7))
    assert len(default.rows) == 7 and default.truncated


def test_preview_ordering_and_column_subset(qdb: Path, cfg: QueryConfig) -> None:
    desc = preview_table(
        qdb, "main", "t", cfg, columns=["name", "ts"], order_by="ts", descending=True, limit=3
    )
    assert desc.columns == ["name", "ts"]
    assert [r[0] for r in desc.rows] == ["row 9999", "row 9998", "row 9997"]
    asc = preview_table(qdb, "main", "t", cfg, columns=["ts", "name"], order_by="ts", limit=2)
    assert [r[1] for r in asc.rows] == ["row 0", "row 1"]
    assert asc.types == ["TIMESTAMP", "VARCHAR"]
    view = preview_table(qdb, "main", "v", cfg, order_by="x", descending=True, limit=2)
    assert view.rows[0][1] >= view.rows[1][1] > 0.5


def test_preview_rejects_names_that_are_not_in_the_table(qdb: Path, cfg: QueryConfig) -> None:
    for kwargs in (
        {"columns": ["nope"]},
        {"order_by": "nope"},
        {"columns": ["ts", 'ts"; drop table t; --']},
        {"order_by": "ts; drop table t"},
        {"columns": []},
    ):
        with pytest.raises(QueryError) as caught:
            preview_table(qdb, "main", "t", cfg, **kwargs)
        assert caught.value.kind == "sql", kwargs
    with pytest.raises(QueryError) as caught:
        preview_table(qdb, "main", "no_such_table", cfg)
    assert caught.value.kind == "sql" and "unknown table" in caught.value.message
    with pytest.raises(QueryError):
        preview_table(qdb, "no_such_schema", "t", cfg)
    with pytest.raises(ValueError):
        preview_table(qdb, "main", "t", cfg, offset=-1)
    assert run_query(qdb, "select count(*) from t", cfg).rows == [(10_000,)]


def test_preview_of_odd_names(qdb: Path, cfg: QueryConfig) -> None:
    page = preview_table(
        qdb, WEIRD_SCHEMA, WEIRD_TABLE, cfg, columns=["é.ü", "*", "--c"], order_by="*", limit=3
    )
    assert page.columns == ["é.ü", "*", "--c"]
    assert page.rows == [("é4", -4, 8), ("é3", -3, 6), ("é2", -2, 4)]
    assert page.truncated
    everything = preview_table(qdb, WEIRD_SCHEMA, WEIRD_TABLE, cfg)
    assert everything.columns == ["a b", "é.ü", "*", "--c"] and len(everything.rows) == 5
    assert table_columns(qdb, WEIRD_SCHEMA, WEIRD_TABLE, cfg)[1] == ("é.ü", "VARCHAR")


def test_preview_times_out(qdb: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The preview runs under the same timeout; here the generated SQL is made slow."""
    original = query.execute_limited

    def slow(con, sql, max_rows, timeout_s):
        return original(con, CROSS_JOIN, max_rows, timeout_s)

    monkeypatch.setattr(query, "execute_limited", slow)
    with pytest.raises(QueryError) as caught:
        preview_table(qdb, "main", "t", QueryConfig(timeout_s=0.5))
    assert caught.value.kind == "timeout"


def test_bare_configs_never_touch_the_real_user_cache(qdb: Path) -> None:
    """W-09: the conftest points ``default_cache_dir`` at the run's folder; the real one stays."""
    import platformdirs

    real = Path(platformdirs.user_cache_dir("duckfleet", appauthor=False))
    mine = real / "tmp" / str(os.getpid())

    def snapshot() -> list[str]:
        return sorted(str(p) for p in mine.rglob("*")) if mine.exists() else []

    before = snapshot()
    bare = QueryConfig()
    assert real not in bare.resolved_temp_dir().parents
    assert run_query(qdb, "select count(*) from t", bare).rows == [(10_000,)]
    from duckfleet_core.plotdata import PlotSpec, fetch_plot_data

    spec = PlotSpec(schema_name="main", table_name="t", kind="line", x="ts", y=["x"])
    assert fetch_plot_data(qdb, spec, bare).rows
    from duckfleet_core.config import ScanConfig

    assert real not in ScanConfig().resolved_cache_dir().parents
    assert snapshot() == before
