"""The flock connection (plan v0.4 Phase 1): aliases, attach, union views, lockdown, failures."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from duckflock_core.flock import (
    FALLBACK_LABEL_COLUMN,
    MAX_MEMBERS,
    FlockQueryError,
    FlockSelection,
    alias_base,
    export_flock_query,
    flock_tables,
    open_flock,
    run_flock_query,
)
from duckflock_core.query import QueryConfig, QueryError

EVENTS_FILES = [
    "exp_01/run_01/metrics.duckdb", "exp_01/run_02/metrics.duckdb", "exp_01/run_03/metrics.duckdb",
    "exp_02/run_01/metrics.duckdb", "exp_02/run_02/metrics.duckdb", "exp_02/run_03/metrics.duckdb",
    "exp_03/run_01/metrics.duckdb", "exp_03/run_02/metrics.duckdb",
]  # fmt: skip
F1 = EVENTS_FILES[:3]
ENCRYPTED = "secure/encrypted.duckdb"
CORRUPT = "broken/corrupt.duckdb"
HIGHBIT = "broken/highbit.duckdb"
LEGACY = "legacy/old.db"
CROSS_JOIN = "select sum(a.i * b.i) from range(200000) a(i), range(200000) b(i)"


@pytest.fixture
def config(cache_dir: Path) -> QueryConfig:
    """A config whose temp root is below the test's cache folder."""
    return QueryConfig(temp_dir=str(cache_dir / "tmp" / str(os.getpid())), threads=2)


def paths_of(root: Path, *rel_paths: str) -> dict[str, str]:
    return {rel: str(root / rel) for rel in rel_paths}


def select(*rel_paths: str) -> FlockSelection:
    return FlockSelection(rel_paths=list(rel_paths))


def run(root: Path, config: QueryConfig, rel_paths: list[str], sql: str, **kwargs):
    return run_flock_query(
        FlockSelection(rel_paths=rel_paths), paths_of(root, *rel_paths), sql, config, **kwargs
    )


def make_db(path: Path, *statements: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()
    return path


# ---------------------------------------------------------------- aliases


def test_the_alias_rule_on_the_example_of_the_plan() -> None:
    assert alias_base("exp_01/run_03/metrics.duckdb") == "exp_01_run_03_metrics"
    sel = select(*F1)
    assert sel.aliases() == {
        "exp_01_run_01_metrics": F1[0],
        "exp_01_run_02_metrics": F1[1],
        "exp_01_run_03_metrics": F1[2],
    }  # alias -> rel_path, in the given order


@pytest.mark.parametrize(
    ("rel_path", "alias"),
    [
        ("2026/Run 7/Data.DB", "d_2026_run_7_data"),  # leading digit, lower case, runs become _
        ("a--b__c//d.duckdb", "a_b_c_d"),
        ("é/日本.duckdb", "_"),  # non-ASCII characters are outside [a-z0-9]
        ("x" * 60 + ".duckdb", "x" * 48),  # cut to 48
        ("1" + "y" * 60 + ".duckdb", ("d_1" + "y" * 60)[:48]),  # cut after the prefix
        ("noext", "noext"),
        ("legacy/old.db", "legacy_old"),
    ],
)
def test_alias_base_cases(rel_path: str, alias: str) -> None:
    assert alias_base(rel_path) == alias


def test_alias_collisions_get_numbers_in_rel_path_order() -> None:
    rels = ["b_c/x.duckdb", "b-c/x.duckdb", "b c/x.duckdb", "other.duckdb"]
    aliases = FlockSelection(rel_paths=rels).aliases()
    # '-' < ' ' < '_'? no: sorted order of the rel_paths decides ('b c' < 'b-c' < 'b_c')
    assert {rel: alias for alias, rel in aliases.items()} == {
        "b c/x.duckdb": "b_c_x",
        "b-c/x.duckdb": "b_c_x_2",
        "b_c/x.duckdb": "b_c_x_3",
        "other.duckdb": "other",
    }
    # the same files get the same aliases whatever order they were chosen in
    again = FlockSelection(rel_paths=list(reversed(rels))).aliases()
    assert {rel: alias for alias, rel in again.items()} == {
        rel: alias for alias, rel in aliases.items()
    }
    assert list(again.values()) == list(reversed(rels))  # but the dict follows the given order


def test_reserved_names_and_existing_aliases_are_never_reused() -> None:
    aliases = FlockSelection(
        rel_paths=["memory.duckdb", "system.db", "temp/x.db", "temp_x.db", "a.db", "a_2.db"]
    ).aliases()
    by_rel = {rel: alias for alias, rel in aliases.items()}
    assert by_rel["memory.duckdb"] == "memory_2" and by_rel["system.db"] == "system_2"
    assert by_rel["temp/x.db"] == "temp_x" and by_rel["temp_x.db"] == "temp_x_2"
    assert by_rel["a.db"] == "a" and by_rel["a_2.db"] == "a_2"
    assert len(set(aliases)) == len(aliases)


def test_a_selection_has_one_to_fifty_distinct_members() -> None:
    assert (
        len(FlockSelection(rel_paths=[f"d{i}.duckdb" for i in range(MAX_MEMBERS)]).rel_paths) == 50
    )
    with pytest.raises(ValidationError, match="at most 50 databases"):
        FlockSelection(rel_paths=[f"d{i}.duckdb" for i in range(MAX_MEMBERS + 1)])
    with pytest.raises(ValidationError):
        FlockSelection(rel_paths=[])
    with pytest.raises(ValidationError, match="more than once: a.duckdb"):
        FlockSelection(rel_paths=["a.duckdb", "b.duckdb", "a.duckdb"])


# ---------------------------------------------------------------- the union views


def test_eight_files_attach_and_events_is_one_view(demo_flock: Path, config: QueryConfig) -> None:
    sel = select(*EVENTS_FILES)
    with open_flock(sel, paths_of(demo_flock, *EVENTS_FILES), config) as flock:
        assert list(flock.aliases.values()) == EVENTS_FILES and not flock.failures
        assert flock.con is not None
        (total,) = flock.con.execute("select count(*) from events").fetchone()
        assert total == 8_000
        labels = flock.con.execute(
            "select database, count(*) from events group by 1 order by 1"
        ).fetchall()
        assert labels == [(rel, 1000) for rel in sorted(EVENTS_FILES)]  # the label is the rel_path
        # quality exists in exp_03 only: NULL elsewhere (columns filled by name)
        quality = dict(
            flock.con.execute("select database, count(quality) from events group by 1").fetchall()
        )
        assert sorted(rel for rel, n in quality.items() if n) == EVENTS_FILES[6:]
        view = flock.view("main", "events")
        assert view is not None and view.members == EVENTS_FILES and view.casts == []
        assert view.columns[0] == ("database", "VARCHAR") and view.label_column == "database"
        assert {name for name, _t in view.columns} == {
            "database",
            "ts",
            "value",
            "run_id",
            "quality",
        }
        assert dict(view.columns)["value"] in ("DOUBLE", "FLOAT")  # DOUBLE and FLOAT unify
        config_view = flock.view("main", "config")
        assert config_view is not None and len(config_view.members) == 8
        notes = flock.view("main", "notes")
        assert notes is not None and notes.members == EVENTS_FILES[6:]
        names = {(v.schema_name, v.table_name) for v in flock.views}
        assert names == {("main", "events"), ("main", "config"), ("main", "notes")}


def test_the_per_file_aliases_stay_available(demo_flock: Path, config: QueryConfig) -> None:
    result, summary = run(
        demo_flock, config, F1,
        "select count(*) from exp_01_run_02_metrics.main.events",
    )  # fmt: skip
    assert result.rows == [(1000,)]
    assert set(summary.aliases.values()) == set(F1) and summary.failures == []


def test_flock_tables_lists_the_members_of_every_table(
    demo_flock: Path, config: QueryConfig
) -> None:
    rels = [*EVENTS_FILES, LEGACY]
    with open_flock(select(*rels), paths_of(demo_flock, *rels), config) as flock:
        assert flock.con is not None
        tables = flock_tables(flock.con, flock.aliases)
    assert list(tables) == [
        ("main", "config"), ("main", "events"), ("main", "notes"), ("main", "samples"),
    ]  # fmt: skip
    assert tables[("main", "events")] == EVENTS_FILES
    assert tables[("main", "notes")] == EVENTS_FILES[6:]
    assert tables[("main", "samples")] == [LEGACY]


def test_a_query_across_files_returns_one_group_per_file(
    demo_flock: Path, config: QueryConfig
) -> None:
    result, _summary = run(
        demo_flock, config, F1,
        "select database, count(*) n from events group by 1 order by 1",
    )  # fmt: skip
    assert result.rows == [(rel, 1000) for rel in F1]
    assert result.columns == ["database", "n"]


def test_views_false_attaches_only(demo_flock: Path, config: QueryConfig) -> None:
    with open_flock(select(*F1), paths_of(demo_flock, *F1), config, views=False) as flock:
        assert flock.views == [] and flock.con is not None
        with pytest.raises(duckdb.CatalogException):
            flock.con.execute("select * from events")
        assert flock.con.execute(
            "select count(*) from exp_01_run_01_metrics.main.events"
        ).fetchone() == (1000,)


# ---------------------------------------------------------------- members that fail


def test_a_member_held_by_a_writer_is_a_locked_failure_and_the_rest_work(
    demo_flock: Path, config: QueryConfig, tmp_path: Path, hold_open
) -> None:
    held = tmp_path / "held" / "metrics.duckdb"
    held.parent.mkdir()
    shutil.copy(demo_flock / F1[0], held)
    paths = {F1[1]: str(demo_flock / F1[1]), "held/metrics.duckdb": str(held)}
    with hold_open(held):
        result, summary = run_flock_query(
            FlockSelection(rel_paths=["held/metrics.duckdb", F1[1]]),
            paths,
            "select database, count(*) from events group by 1",
            config,
        )
    assert result.rows == [(F1[1], 1000)]
    (failure,) = summary.failures
    assert (failure.rel_path, failure.alias, failure.kind) == (
        "held/metrics.duckdb", "held_metrics", "locked",
    )  # fmt: skip
    assert failure.message
    assert list(summary.aliases.values()) == [F1[1]]


def test_unreadable_members_are_classified_and_skipped(
    demo_flock: Path, config: QueryConfig, tmp_path: Path
) -> None:
    rels = [F1[0], ENCRYPTED, CORRUPT, HIGHBIT, "gone/missing.duckdb"]
    paths = paths_of(demo_flock, F1[0], ENCRYPTED, CORRUPT, HIGHBIT)
    paths["gone/missing.duckdb"] = str(tmp_path / "gone" / "missing.duckdb")
    result, summary = run_flock_query(
        FlockSelection(rel_paths=rels), paths, "select count(*) from events", config
    )
    assert result.rows == [(1000,)]
    kinds = {f.rel_path: f.kind for f in summary.failures}
    assert kinds == {
        ENCRYPTED: "encrypted",
        CORRUPT: "corrupt",
        HIGHBIT: "corrupt",
        "gone/missing.duckdb": "missing",
    }  # fmt: skip
    assert [f.rel_path for f in summary.failures] == [
        ENCRYPTED,
        CORRUPT,
        HIGHBIT,
        "gone/missing.duckdb",
    ]
    assert not (tmp_path / "gone").exists()  # a missing file is not created by ATTACH


def test_no_attachable_member_is_an_error(demo_flock: Path, config: QueryConfig) -> None:
    with pytest.raises(QueryError) as caught:
        run(demo_flock, config, [ENCRYPTED, CORRUPT], "select 1")
    assert caught.value.kind == "other"
    assert "none of the databases could be attached" in caught.value.message
    assert "encrypted" in caught.value.message


def test_an_unknown_member_is_a_sql_error(demo_flock: Path, config: QueryConfig) -> None:
    with pytest.raises(QueryError) as caught:
        run_flock_query(
            select("nope.duckdb", F1[0]), paths_of(demo_flock, F1[0]), "select 1", config
        )
    assert caught.value.kind == "sql" and "nope.duckdb" in caught.value.message


# ---------------------------------------------------------------- the lockdown


@pytest.mark.parametrize(
    "sql",
    [
        "COPY events TO 'duckflock_should_not_exist.csv'",
        "ATTACH 'duckflock_other.duckdb' AS other",
        "SELECT * FROM read_csv('duckflock_nothing.csv')",
        "SELECT * FROM read_parquet('duckflock_nothing.parquet')",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SET threads = 8",
        "SET enable_external_access = true",
        "SET memory_limit = '99GB'",
        "SET lock_configuration = false",
        "EXPORT DATABASE 'duckflock_export_dir'",
        "IMPORT DATABASE 'duckflock_import_dir'",
        "SELECT * FROM glob('*')",
        "SELECT * FROM read_text('duckflock_nothing.txt')",
        "CREATE SECRET duckflock_secret (TYPE S3, KEY_ID 'a', SECRET 'b')",
    ],
)
def test_after_the_lockdown_files_extensions_and_settings_are_blocked(
    demo_flock: Path, config: QueryConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql: str
) -> None:
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    with pytest.raises(QueryError) as caught:
        run(demo_flock, config, F1, sql)
    assert caught.value.kind in ("blocked", "read_only"), caught.value
    if sql.startswith(
        ("COPY", "ATTACH", "SELECT * FROM read", "SELECT * FROM glob", "LOAD", "INSTALL", "SET")
    ):
        assert caught.value.kind == "blocked", caught.value
    assert list(work.iterdir()) == []  # nothing was written


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO exp_01_run_01_metrics.main.events VALUES (NULL, 1.0, 1)",
        "UPDATE exp_01_run_01_metrics.main.events SET value = 0",
        "DELETE FROM exp_01_run_01_metrics.main.events",
        "DROP TABLE exp_01_run_01_metrics.main.events",
        "CREATE TABLE exp_01_run_01_metrics.main.fresh (i INTEGER)",
        "INSERT INTO events VALUES ('x', NULL, 1.0, 1, NULL)",
        "UPDATE events SET value = 0",
        "DELETE FROM events",
    ],
)
def test_writes_are_read_only_errors_and_nothing_changes(
    demo_flock: Path, config: QueryConfig, sql: str
) -> None:
    before = {p: p.stat().st_mtime_ns for p in demo_flock.rglob("*") if p.is_file()}
    with pytest.raises(QueryError) as caught:
        run(demo_flock, config, F1, sql)
    assert caught.value.kind == "read_only", caught.value
    assert {p: p.stat().st_mtime_ns for p in demo_flock.rglob("*") if p.is_file()} == before
    assert run(demo_flock, config, F1, "select count(*) from events")[0].rows == [(3000,)]


def test_a_temp_table_lives_and_dies_with_the_connection(
    demo_flock: Path, config: QueryConfig
) -> None:
    both = run(
        demo_flock, config, F1, "create temp table z as select 41 + 1 as a; select a from z"
    )[0]
    assert both.rows == [(42,)]
    with pytest.raises(QueryError) as caught:
        run(demo_flock, config, F1, "select a from z")
    assert caught.value.kind == "sql"


def test_nothing_is_written_beside_any_member(demo_flock: Path, config: QueryConfig) -> None:
    """Spill files of a flock query go to the flock's own temp folder, not beside the files."""

    def listing() -> list[str]:
        return sorted(str(p) for p in demo_flock.rglob("*"))

    before = listing()
    small = config.model_copy(update={"memory_limit_mb": 64, "timeout_s": 120})
    sql = "select count(*) from (select hash(i) h from range(20000000) t(i) order by h)"
    assert run(demo_flock, small, F1, sql)[0].rows == [(20_000_000,)]
    assert listing() == before


def test_a_spilling_flock_query_leaves_the_working_directory_empty(
    demo_flock: Path, config: QueryConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default temp directory of an in-memory DuckDB is ``.tmp`` in the working directory.

    The lockdown tests list the working directory without spilling; this one spills (a temp
    table of 15 M rows under 64 MB), shows from inside the connection that the spill files live
    in the flock's own folder, then lists the working directory.
    """
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    small = config.model_copy(update={"memory_limit_mb": 64, "timeout_s": 120})
    sql = (
        "create temp table big as select i, hash(i) h from range(15000000) t(i); "
        "select count(*), string_agg(path, ';') from duckdb_temporary_files()"
    )
    ((files, listed),) = run(demo_flock, small, F1, sql)[0].rows
    assert files >= 1  # it did spill
    root = Path(config.resolved_temp_dir())
    assert all(Path(p).parent.parent == root for p in listed.split(";"))  # <root>/flock-<8 hex>/
    assert all(Path(p).parent.name.startswith("flock-") for p in listed.split(";"))
    assert list(work.iterdir()) == []
    assert not list(root.glob("flock-*"))  # and the flock's folder is gone with the connection


# ---------------------------------------------------------------- the connection itself


def test_every_flock_connection_has_its_own_temp_folder_that_is_removed(
    demo_flock: Path, config: QueryConfig
) -> None:
    root = Path(config.resolved_temp_dir())
    sel, paths = select(*F1), paths_of(demo_flock, *F1)
    with open_flock(sel, paths, config) as one, open_flock(sel, paths, config) as two:
        assert one.con is not None and two.con is not None
        folders = []
        for flock in (one, two):
            (setting,) = flock.con.execute("select current_setting('temp_directory')").fetchone()
            folders.append(Path(setting))
        assert folders[0] != folders[1] and all(f.is_dir() for f in folders)
        assert all(f.parent == root and f.name.startswith("flock-") for f in folders)
    assert not any(f.exists() for f in folders)  # removed when the connection closed


def test_the_connection_is_closed_after_an_error_and_the_next_call_works(
    demo_flock: Path, config: QueryConfig
) -> None:
    root = Path(config.resolved_temp_dir())
    with pytest.raises(QueryError) as caught:
        run(demo_flock, config, F1, "selec 1")
    assert caught.value.kind == "sql"
    assert not list(root.glob("flock-*"))
    assert run(demo_flock, config, F1, "select 1")[0].rows == [(1,)]
    # a member can be opened on its own with another config afterwards: no connection is left
    other = QueryConfig(threads=1, memory_limit_mb=700, temp_dir=config.temp_dir)
    assert run(demo_flock, other, [F1[0]], "select count(*) from events")[0].rows == [(1000,)]


def test_the_timeout_applies_and_the_next_query_works(
    demo_flock: Path, config: QueryConfig
) -> None:
    quick = config.model_copy(update={"timeout_s": 0.5})
    with pytest.raises(QueryError) as caught:
        run(demo_flock, quick, F1, CROSS_JOIN)
    assert caught.value.kind == "timeout"
    assert run(demo_flock, config, F1, "select 7")[0].rows == [(7,)]


def test_max_rows_truncates(demo_flock: Path, config: QueryConfig) -> None:
    result, _summary = run(demo_flock, config, F1, "select * from events", max_rows=5)
    assert len(result.rows) == 5 and result.truncated
    assert not run(demo_flock, config, F1, "select * from events", max_rows=3000)[0].truncated


def test_a_member_open_elsewhere_in_the_process_can_be_attached(
    demo_flock: Path, config: QueryConfig
) -> None:
    from duckflock_core.query import open_readonly

    with open_readonly(demo_flock / F1[0], config) as direct:
        assert direct.execute("select count(*) from events").fetchone() == (1000,)
        result, summary = run(demo_flock, config, F1[:2], "select count(*) from events")
    assert result.rows == [(2000,)] and not summary.failures


def test_two_flocks_spilling_at_once_in_one_process_survive(tmp_path: Path) -> None:
    """Flock connections are DuckDB instances too: each needs a temp folder of its own (W-01)."""
    script = Path(__file__).with_name("_spill_threads.py")
    done = subprocess.run(
        [sys.executable, str(script), str(tmp_path), "flock"],
        capture_output=True,
        text=True,
        timeout=280,
    )
    detail = (done.returncode, done.stdout, done.stderr)
    assert done.returncode == 0 and done.stdout.strip() == "ok", detail


# ---------------------------------------------------------------- type conflicts and names


def test_columns_with_types_that_cannot_be_unified_are_cast_to_text(
    tmp_path: Path, config: QueryConfig
) -> None:
    """The view is created whatever the types; reading it would fail (D-044), so cast up front."""
    one = make_db(
        tmp_path / "f" / "one.duckdb",
        "CREATE TABLE m AS SELECT 1 AS value, 7 AS n, DATE '2026-01-01' AS d, 'a' AS only_one",
    )
    two = make_db(
        tmp_path / "f" / "two.duckdb",
        "CREATE TABLE m AS SELECT 'x' AS value, 2.5::DOUBLE AS n, "
        "TIMESTAMP '2026-01-02 03:04:05' AS d",
    )
    rels = ["f/one.duckdb", "f/two.duckdb"]
    paths = {"f/one.duckdb": str(one), "f/two.duckdb": str(two)}
    with open_flock(FlockSelection(rel_paths=rels), paths, config) as flock:
        view = flock.view("main", "m")
        assert view is not None
        assert view.casts == ["value"]  # INTEGER vs VARCHAR; numeric and temporal pairs unify
        types = dict(view.columns)
        assert types["value"] == "VARCHAR" and types["n"] == "DOUBLE" and types["d"] == "TIMESTAMP"
        assert flock.con is not None
        rows = flock.con.execute(
            "select database, value, n, d, only_one from m order by 1"
        ).fetchall()
    assert rows[0][:2] == ("f/one.duckdb", "1") and rows[1][:2] == ("f/two.duckdb", "x")
    assert rows[0][2] == 7.0 and rows[1][2] == 2.5 and rows[0][4] == "a" and rows[1][4] is None


@pytest.mark.parametrize(
    ("first", "second", "cast"),
    [
        ("1", "'a'", True),
        ("1", "[1, 2]", True),
        ("[1]", "['a']", True),
        ("{'a': 1}", "{'a': 'x'}", True),
        ("true", "1", True),
        ("1", "2::BIGINT", False),
        ("1", "1.5::FLOAT", False),
        ("1::HUGEINT", "1.25::DECIMAL(10,2)", False),
        ("DATE '2026-01-01'", "TIMESTAMP '2026-01-01 00:00:00'", False),
        ("TIMESTAMP '2026-01-01 00:00:00'", "TIMESTAMPTZ '2026-01-01 00:00:00+00'", False),
        ("'a'", "'b'", False),
        ("[1]", "[2]", False),
    ],
)
def test_the_cast_rule_matches_what_duckdb_can_read(
    tmp_path: Path, config: QueryConfig, first: str, second: str, cast: bool
) -> None:
    a = make_db(tmp_path / "a.duckdb", f"CREATE TABLE t AS SELECT {first} AS v")
    b = make_db(tmp_path / "b.duckdb", f"CREATE TABLE t AS SELECT {second} AS v")
    rels = ["a.duckdb", "b.duckdb"]
    with open_flock(
        FlockSelection(rel_paths=rels), {"a.duckdb": str(a), "b.duckdb": str(b)}, config
    ) as flock:
        view = flock.view("main", "t")
        assert view is not None and (view.casts == ["v"]) is cast
        assert flock.con is not None
        assert len(flock.con.execute("select * from t").fetchall()) == 2  # readable either way


def test_a_member_column_named_database_moves_the_label_column(
    tmp_path: Path, config: QueryConfig
) -> None:
    """The union binder refuses two columns of one name, so that view labels with another name."""
    a = make_db(tmp_path / "a.duckdb", "CREATE TABLE t AS SELECT 1 AS \"Database\", 2 AS v",
                "CREATE TABLE u AS SELECT 3 AS w")  # fmt: skip
    b = make_db(tmp_path / "b.duckdb", "CREATE TABLE t AS SELECT 4 AS v",
                "CREATE TABLE u AS SELECT 5 AS w")  # fmt: skip
    paths = {"a.duckdb": str(a), "b.duckdb": str(b)}
    with open_flock(FlockSelection(rel_paths=list(paths)), paths, config) as flock:
        t, u = flock.view("main", "t"), flock.view("main", "u")
        assert t is not None and u is not None
        assert t.label_column == FALLBACK_LABEL_COLUMN and u.label_column == "database"
        assert [name for name, _t in t.columns][:3] == [FALLBACK_LABEL_COLUMN, "Database", "v"]
        assert flock.con is not None
        rows = flock.con.execute(f"select {FALLBACK_LABEL_COLUMN}, v from t order by 1").fetchall()
        assert rows == [("a.duckdb", 2), ("b.duckdb", 4)]
        assert flock.con.execute("select database from u order by 1").fetchall() == [
            ("a.duckdb",), ("b.duckdb",),
        ]  # fmt: skip


def test_other_schemas_and_member_views_become_union_views(
    tmp_path: Path, config: QueryConfig
) -> None:
    a = make_db(
        tmp_path / "a.duckdb",
        "CREATE SCHEMA s", "CREATE TABLE s.t AS SELECT 1 AS v", "CREATE TABLE t AS SELECT 10 AS v",
        "CREATE VIEW vv AS SELECT v + 1 AS w FROM t",
    )  # fmt: skip
    b = make_db(
        tmp_path / "b.duckdb",
        "CREATE SCHEMA s", "CREATE TABLE s.t AS SELECT 2 AS v", "CREATE VIEW vv AS SELECT 5 AS w",
    )  # fmt: skip
    paths = {"a.duckdb": str(a), "b.duckdb": str(b)}
    with open_flock(FlockSelection(rel_paths=list(paths)), paths, config) as flock:
        names = {(v.schema_name, v.table_name): v.members for v in flock.views}
        assert names == {
            ("s", "t"): ["a.duckdb", "b.duckdb"],
            ("main", "t"): ["a.duckdb"],
            ("main", "vv"): ["a.duckdb", "b.duckdb"],
        }
        assert flock.con is not None
        assert flock.con.execute("select database, v from s.t order by 1").fetchall() == [
            ("a.duckdb", 1), ("b.duckdb", 2),
        ]  # fmt: skip
        assert flock.con.execute("select database, w from vv order by 1").fetchall() == [
            ("a.duckdb", 11), ("b.duckdb", 5),
        ]  # fmt: skip


def test_hostile_names_are_quoted(tmp_path: Path, config: QueryConfig) -> None:
    weird_schema, weird_table = 'we"ird s', 't;x "q" ü'
    a = make_db(
        tmp_path / "it's.duckdb",
        'CREATE SCHEMA "we""ird s"',
        'CREATE TABLE "we""ird s"."t;x ""q"" ü" AS SELECT 1 AS "a b", 2 AS "é"',
    )
    b = make_db(
        tmp_path / "né.duckdb",
        'CREATE SCHEMA "we""ird s"',
        'CREATE TABLE "we""ird s"."t;x ""q"" ü" AS SELECT 3 AS "a b"',
    )
    paths = {"it's.duckdb": str(a), "né.duckdb": str(b)}
    with open_flock(FlockSelection(rel_paths=list(paths)), paths, config) as flock:
        view = flock.view(weird_schema, weird_table)
        assert view is not None and view.members == ["it's.duckdb", "né.duckdb"]
        assert flock.con is not None
        rows = flock.con.execute(
            'select database, "a b", "é" from "we""ird s"."t;x ""q"" ü" order by 1'
        ).fetchall()
    assert rows == [("it's.duckdb", 1, 2), ("né.duckdb", 3, None)]


def test_export_flock_query_builds_csv_and_parquet(demo_flock: Path, config: QueryConfig) -> None:
    pytest.importorskip("pyarrow")
    sel, paths = select(*F1), paths_of(demo_flock, *F1)
    data, rows, truncated, summary = export_flock_query(
        sel, paths, "select database, count(*) n from events group by 1 order by 1", "csv", config
    )
    assert rows == 3 and not truncated and summary.failures == []
    assert data.decode().splitlines()[0] == '"database","n"'
    data, rows, truncated, _summary = export_flock_query(
        sel, paths, "select * from events", "parquet", config, max_rows=10
    )
    assert rows == 10 and truncated and data[:4] == b"PAR1"
    with pytest.raises(ValueError):
        export_flock_query(sel, paths, "select 1", "xlsx", config)  # type: ignore[arg-type]
    with pytest.raises(QueryError) as caught:
        export_flock_query(sel, paths, "selec 1", "csv", config)
    assert caught.value.kind == "sql"


# ---------------------------------------------------------------- review X-02: reserved aliases


@pytest.mark.parametrize("name", ["main", "system", "temp", "memory"])
def test_a_file_named_like_a_reserved_catalog_gets_a_suffixed_alias_and_attaches(
    tmp_path: Path, config: QueryConfig, name: str
) -> None:
    """X-02: ``main.duckdb`` got the alias ``main``, which DuckDB refuses, in every selection."""
    rels = [f"{name}.duckdb", "other.duckdb"]
    for rel in rels:
        make_db(tmp_path / rel, "CREATE TABLE t AS SELECT 7 AS v")
    for case in (name, name.upper()):  # the base is lower-cased, so MAIN.duckdb is the same
        assert select(f"{case}.duckdb").aliases() == {f"{name}_2": f"{case}.duckdb"}
    with open_flock(select(*rels), paths_of(tmp_path, *rels), config) as flock:
        assert flock.failures == [] and flock.con is not None
        assert flock.aliases == {f"{name}_2": rels[0], "other": rels[1]}
        assert flock.con.execute("select count(*) from t").fetchone() == (2,)
        assert flock.con.execute(f'select v from "{name}_2".main.t').fetchone() == (7,)


# ---------------------------------------------------------------- review X-03: orphaned views


def orphan_flock(root: Path) -> list[str]:
    """Three files with ``vw AS SELECT v FROM base``; ``base`` is gone in the third."""
    rels = ["a.duckdb", "b.duckdb", "c.duckdb"]
    for index, rel in enumerate(rels, start=1):
        make_db(
            root / rel,
            f"CREATE TABLE base AS SELECT {index} AS v",
            "CREATE VIEW vw AS SELECT v FROM base",
        )
    con = duckdb.connect(str(root / "c.duckdb"))
    try:
        con.execute("DROP TABLE base")  # the view stays and no longer binds
    finally:
        con.close()
    return rels


def test_a_member_view_without_its_base_table_is_left_out_of_the_union(
    tmp_path: Path, config: QueryConfig
) -> None:
    """X-03: the view of ``c`` used to bind ``base`` to the in-memory union view of that name and
    return the rows of ``a`` and ``b`` under its own label, with no error."""
    rels = orphan_flock(tmp_path)
    with open_flock(select(*rels), paths_of(tmp_path, *rels), config) as flock:
        assert flock.con is not None and flock.failures == []
        assert flock.con.execute("select database, v from vw order by 1").fetchall() == [
            ("a.duckdb", 1), ("b.duckdb", 2),
        ]  # fmt: skip  (nothing under c.duckdb)
        assert flock.con.execute("select database, v from base order by 1").fetchall() == [
            ("a.duckdb", 1), ("b.duckdb", 2),
        ]  # fmt: skip
        view = flock.view("main", "vw")
        assert view is not None and view.members == ["a.duckdb", "b.duckdb"]
        ((name, message),) = flock.view_failures
        assert name == "main.vw" and message.startswith("c.duckdb: ")
        assert "base" in message


def test_a_view_that_fails_in_every_member_has_no_union_and_one_failure_each(
    tmp_path: Path, config: QueryConfig
) -> None:
    rels = ["a.duckdb", "b.duckdb"]
    for rel in rels:
        make_db(
            tmp_path / rel,
            "CREATE TABLE base AS SELECT 1 AS v",
            "CREATE VIEW vw AS SELECT v FROM base",
            "CREATE VIEW upper_vw AS SELECT v FROM vw",
            "DROP TABLE base",
        )
    make_db(tmp_path / "c.duckdb", "CREATE TABLE ok AS SELECT 5 AS v")
    rels.append("c.duckdb")
    with open_flock(select(*rels), paths_of(tmp_path, *rels), config) as flock:
        assert [(v.schema_name, v.table_name) for v in flock.views] == [("main", "ok")]
        # the view and the view built on it both fail, once for each member that holds them
        assert sorted((name, message.split(":")[0]) for name, message in flock.view_failures) == [
            ("main.upper_vw", "a.duckdb"), ("main.upper_vw", "b.duckdb"),
            ("main.vw", "a.duckdb"), ("main.vw", "b.duckdb"),
        ]  # fmt: skip
        assert flock.con is not None
        with pytest.raises(duckdb.Error):
            flock.con.execute("select * from vw")


def test_healthy_member_views_stay_in_the_union(tmp_path: Path, config: QueryConfig) -> None:
    rels = ["a.duckdb", "b.duckdb"]
    for index, rel in enumerate(rels, start=1):
        make_db(
            tmp_path / rel,
            f"CREATE TABLE base AS SELECT {index} AS v",
            "CREATE VIEW vw AS SELECT v * 10 AS w FROM base",
        )
    result, summary = run(tmp_path, config, rels, "select database, w from vw order by 1")
    assert result.rows == [("a.duckdb", 10), ("b.duckdb", 20)]
    assert summary.view_failures == []


# ---------------------------------------------------------------- review X-05: alias and schema


def schema_flock(root: Path) -> list[str]:
    """``s.duckdb`` and ``u.duckdb`` both hold schema ``s`` (also the alias of the first)."""
    make_db(root / "first.duckdb", "CREATE SCHEMA s", "CREATE TABLE s.t AS SELECT 1 AS a")
    shutil.copy(root / "first.duckdb", root / "s.duckdb")  # a file named like its schema
    (root / "first.duckdb").unlink()
    make_db(root / "u.duckdb", "CREATE SCHEMA s", "CREATE TABLE s.t AS SELECT 2 AS a")
    return ["s.duckdb", "u.duckdb"]


def test_an_alias_equal_to_a_schema_of_the_flock_is_suffixed_before_the_lockdown(
    tmp_path: Path, config: QueryConfig
) -> None:
    """X-05: ``CREATE VIEW "s"."t"`` and the user's ``SELECT * FROM s.t`` were ambiguous."""
    rels = schema_flock(tmp_path)
    assert list(select(*rels).aliases()) == ["s", "u"]  # the rule alone gives `s`
    with open_flock(select(*rels), paths_of(tmp_path, *rels), config) as flock:
        assert flock.failures == [] and flock.con is not None
        assert flock.aliases == {"s_2": "s.duckdb", "u": "u.duckdb"}  # same order as the selection
        assert flock.con.execute("select database, a from s.t order by 1").fetchall() == [
            ("s.duckdb", 1), ("u.duckdb", 2),
        ]  # fmt: skip
        assert flock.con.execute("select a from s_2.s.t").fetchall() == [(1,)]
        view = flock.view("s", "t")
        assert view is not None and view.members == rels and not flock.view_failures
        assert flock.con.execute(
            "select schema_name from duckdb_views() where database_name = 'memory' and "
            "view_name = 't'"
        ).fetchall() == [("s",)]


def test_the_suffix_skips_names_that_are_taken_by_aliases_or_schemas(
    tmp_path: Path, config: QueryConfig
) -> None:
    rels = schema_flock(tmp_path)
    make_db(tmp_path / "w.duckdb", "CREATE SCHEMA s_2", "CREATE TABLE s_2.t AS SELECT 3 AS a")
    rels.append("w.duckdb")
    with open_flock(select(*rels), paths_of(tmp_path, *rels), config) as flock:
        assert flock.aliases == {"s_3": "s.duckdb", "u": "u.duckdb", "w": "w.duckdb"}
        assert flock.con is not None
        assert flock.con.execute("select count(*) from s_2.t").fetchone() == (1,)  # the union view


def test_a_schema_named_like_an_alias_in_another_case_counts_as_a_clash(
    tmp_path: Path, config: QueryConfig
) -> None:
    make_db(tmp_path / "x.duckdb", "CREATE SCHEMA Beta", "CREATE TABLE Beta.t AS SELECT 1 AS a")
    make_db(tmp_path / "beta.duckdb", "CREATE TABLE t AS SELECT 2 AS a")
    rels = ["x.duckdb", "beta.duckdb"]
    with open_flock(select(*rels), paths_of(tmp_path, *rels), config) as flock:
        assert flock.aliases == {"x": "x.duckdb", "beta_2": "beta.duckdb"}


def test_nothing_is_renamed_without_a_clash(demo_flock: Path, config: QueryConfig) -> None:
    with open_flock(select(*F1), paths_of(demo_flock, *F1), config) as flock:
        assert flock.aliases == select(*F1).aliases()


# ---------------------------------------------------------------- review X-06: summary on error


def test_a_failing_query_carries_the_flock_summary_with_the_attach_failures(
    demo_flock: Path, config: QueryConfig
) -> None:
    rels = [F1[0], ENCRYPTED, CORRUPT]
    with pytest.raises(FlockQueryError) as caught:
        run(demo_flock, config, rels, "selec 1")
    assert caught.value.kind == "sql"
    assert isinstance(caught.value, QueryError)
    summary = caught.value.summary
    assert [f.rel_path for f in summary.failures] == [ENCRYPTED, CORRUPT]
    assert list(summary.aliases.values()) == [F1[0]]
    assert [v.table_name for v in summary.views] == ["config", "events"]
