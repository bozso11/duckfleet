"""Table summaries and the summary store (plan v0.2 Phase 2)."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest

from duckflock_core import store as store_module
from duckflock_core import summary as summary_module
from duckflock_core.query import QueryConfig
from duckflock_core.store import ProfileStore, SummaryStore, file_key, table_key
from duckflock_core.summary import SUMMARY_SCHEMA_VERSION, TableSummary, summarize_table

REL = "exp_01/run_01/metrics.duckdb"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "flock" / "s.duckdb"
    path.parent.mkdir()
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE k AS SELECT
                i AS a,
                'v' || (i % 5)::VARCHAR AS b,
                TIMESTAMP '2026-01-01 00:00:00' + to_hours(i::INTEGER) AS c,
                CASE WHEN i % 4 = 0 THEN NULL ELSE i / 2.0 END AS d,
                TIMESTAMPTZ '2026-01-01 00:00:00+00' + to_hours(i::INTEGER) AS e
            FROM range(1, 101) r(i)
            """
        )
        con.execute("CREATE TABLE empty_t (a INTEGER, b VARCHAR)")
        con.execute("CREATE VIEW kv AS SELECT a, b FROM k WHERE a <= 10")
    finally:
        con.close()
    return path


def summarize(db: Path, table: str, config: QueryConfig | None = None, **kwargs) -> TableSummary:
    stat = db.stat()
    options = {"row_count": None, "rel_path": REL, "size_bytes": stat.st_size,
               "mtime_ns": stat.st_mtime_ns} | kwargs  # fmt: skip
    return summarize_table(db, "main", table, config or QueryConfig(), **options)


def test_columns_and_values_of_a_known_table(db: Path) -> None:
    s = summarize(db, "k", row_count=100)
    assert s.error is None and not s.sampled and s.sample_rows is None
    assert s.summary_schema_version == SUMMARY_SCHEMA_VERSION == 1
    assert (s.rel_path, s.schema_name, s.table_name, s.row_count) == (REL, "main", "k", 100)
    assert s.size_bytes == db.stat().st_size and s.mtime_ns == db.stat().st_mtime_ns
    assert s.duckdb_version == duckdb.__version__ and s.computed_at.endswith("+00:00")
    assert s.elapsed_s >= 0
    assert [c.column_name for c in s.columns] == ["a", "b", "c", "d", "e"]
    a, b, c, d, e = s.columns
    assert (a.column_type, a.min, a.max) == ("BIGINT", "1", "100")
    assert (a.count, a.null_percentage) == (100, 0.0)
    assert a.avg == "50.5" and a.std is not None
    assert 95 <= a.approx_unique <= 105
    assert 15 <= float(a.q25) <= 35 and 40 <= float(a.q50) <= 60 and 65 <= float(a.q75) <= 85
    assert (b.column_type, b.min, b.max, b.avg, b.std) == ("VARCHAR", "v0", "v4", None, None)
    assert b.approx_unique == 5
    assert c.column_type == "TIMESTAMP" and c.min.startswith("2026-01-01 01:00:00")
    assert c.max.startswith("2026-01-05 04:00:00")
    assert d.null_percentage == 25.0 and d.count == 100
    assert e.column_type == "TIMESTAMP WITH TIME ZONE" and e.min and e.max


def test_sampled_above_the_threshold(db: Path) -> None:
    config = QueryConfig(stats_row_threshold=50, stats_sample_rows=20)
    s = summarize(db, "k", config, row_count=100)
    assert s.error is None and s.sampled and s.sample_rows == 20
    assert s.columns[0].count == 20  # the statistics describe the sample
    at_threshold = summarize(db, "k", config, row_count=50)
    assert not at_threshold.sampled and at_threshold.columns[0].count == 100
    unknown = summarize(db, "k", config, row_count=None)
    assert not unknown.sampled and unknown.row_count is None


def test_an_empty_table_and_a_view(db: Path) -> None:
    empty = summarize(db, "empty_t", row_count=0)
    assert empty.error is None and [c.column_name for c in empty.columns] == ["a", "b"]
    assert [c.count for c in empty.columns] == [0, 0]
    view = summarize(db, "kv")
    assert view.error is None and view.columns[0].max == "10" and view.columns[0].count == 10


def test_a_failure_becomes_a_summary_with_an_error(db: Path) -> None:
    s = summarize(db, "no_such_table")
    assert s.error and s.error.startswith("sql:") and s.columns == []
    assert not s.sampled
    gone = summarize_table(
        db.parent / "gone.duckdb", "main", "k", QueryConfig(),
        row_count=1, rel_path=REL, size_bytes=1, mtime_ns=1,
    )  # fmt: skip
    assert gone.error and gone.columns == []


def test_a_timeout_is_an_error_summary(db: Path) -> None:
    config = QueryConfig(timeout_s=0.5)
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE big AS SELECT i FROM range(200000) r(i)")
    con.execute("CREATE VIEW slow AS SELECT sum(a.i * b.i) AS s FROM big a, big b")
    con.close()
    s = summarize(db, "slow", config)
    assert s.error and s.error.startswith("timeout:")


# ---------------------------------------------------------------- the store


def make_store(tmp_path: Path) -> SummaryStore:
    return SummaryStore(tmp_path / "cache", tmp_path / "flock")


def test_store_layout_and_keys(tmp_path: Path, db: Path) -> None:
    summaries = make_store(tmp_path)
    summaries.get_or_compute(db, REL, "main", "k", QueryConfig(), row_count=100)
    path = summaries.path(REL, "main", "k")
    assert path.is_file()
    assert path.parent.name == file_key(REL) and path.name == f"{table_key('main', 'k')}.json"
    root_dir = ProfileStore(tmp_path / "cache", tmp_path / "flock").root_dir
    assert path.parent.parent == root_dir / "summaries"
    assert len(table_key("main", "k")) == 16 and table_key("main", "k") != table_key("main", "kv")


def test_second_call_is_a_cache_hit_that_does_not_open_the_database(
    tmp_path: Path, db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summaries = make_store(tmp_path)
    first = summaries.get_or_compute(db, REL, "main", "k", QueryConfig(), row_count=100)

    def refuse(*args, **kwargs):
        raise AssertionError("the database must not be opened on a cache hit")

    monkeypatch.setattr(summary_module, "open_readonly", refuse)
    second = summaries.get_or_compute(db, REL, "main", "k", QueryConfig(), row_count=100)
    assert second == first and second.computed_at == first.computed_at
    assert summaries.load(REL, "main", "k", size_bytes=db.stat().st_size,
                          mtime_ns=db.stat().st_mtime_ns) == first  # fmt: skip


def test_refresh_recomputes(tmp_path: Path, db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    summaries = make_store(tmp_path)
    summaries.get_or_compute(db, REL, "main", "k", QueryConfig(), row_count=100)
    calls: list[str] = []
    real = store_module.summarize_table

    def counting(*args, **kwargs):
        calls.append("computed")
        return real(*args, **kwargs)

    monkeypatch.setattr(store_module, "summarize_table", counting)
    summaries.get_or_compute(db, REL, "main", "k", QueryConfig(), row_count=100)
    assert calls == []
    summaries.get_or_compute(db, REL, "main", "k", QueryConfig(), row_count=100, refresh=True)
    assert calls == ["computed"]


def test_a_changed_file_invalidates_the_summary(tmp_path: Path, db: Path) -> None:
    summaries = make_store(tmp_path)
    first = summaries.get_or_compute(db, REL, "main", "k", QueryConfig(), row_count=100)
    stat = db.stat()
    os.utime(db, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    assert summaries.load(REL, "main", "k", size_bytes=stat.st_size,
                          mtime_ns=db.stat().st_mtime_ns) is None  # fmt: skip
    second = summaries.get_or_compute(db, REL, "main", "k", QueryConfig(), row_count=100)
    assert second.mtime_ns == db.stat().st_mtime_ns != first.mtime_ns
    # a size change counts too
    assert summaries.load(REL, "main", "k", size_bytes=stat.st_size + 1,
                          mtime_ns=db.stat().st_mtime_ns) is None  # fmt: skip


def test_other_versions_invalidate_the_summary(tmp_path: Path, db: Path) -> None:
    summaries = make_store(tmp_path)
    good = summaries.get_or_compute(db, REL, "main", "k", QueryConfig(), row_count=100)
    key = {"size_bytes": good.size_bytes, "mtime_ns": good.mtime_ns}
    for field, value in (("duckdb_version", "0.9.0"), ("summary_schema_version", 99)):
        summaries.save(good.model_copy(update={field: value}))
        assert summaries.load(REL, "main", "k", **key) is None, field
    summaries.save(good)
    assert summaries.load(REL, "main", "k", **key) == good


def test_an_error_summary_is_stored_but_recomputed_next_time(
    tmp_path: Path, db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summaries = make_store(tmp_path)
    failed = summaries.get_or_compute(db, REL, "main", "later", QueryConfig(), row_count=1)
    assert failed.error and summaries.path(REL, "main", "later").is_file()
    assert summaries.load(REL, "main", "later", size_bytes=failed.size_bytes,
                          mtime_ns=failed.mtime_ns) is None  # fmt: skip
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE later (i INTEGER)")
    con.close()  # the file changed, but even an unchanged file would be retried
    ok = summaries.get_or_compute(db, REL, "main", "later", QueryConfig(), row_count=0)
    assert ok.error is None and ok.columns[0].column_name == "i"
    again = summaries.get_or_compute(db, REL, "main", "later", QueryConfig(), row_count=0)
    assert again == ok


def test_a_missing_database_file_gives_an_error_summary(tmp_path: Path) -> None:
    summaries = make_store(tmp_path)
    s = summaries.get_or_compute(tmp_path / "gone.duckdb", REL, "main", "k", QueryConfig(),
                                 row_count=1)  # fmt: skip
    assert s.error and s.columns == []


def test_corrupt_or_foreign_sidecars_are_ignored(tmp_path: Path, db: Path) -> None:
    summaries = make_store(tmp_path)
    good = summaries.get_or_compute(db, REL, "main", "k", QueryConfig(), row_count=100)
    path = summaries.path(REL, "main", "k")
    key = {"size_bytes": good.size_bytes, "mtime_ns": good.mtime_ns}
    path.write_text("{not json", encoding="utf-8")
    assert summaries.load(REL, "main", "k", **key) is None
    # key collision guard: "a.b"."c" and "a"."b.c" share a table_key
    assert table_key("a.b", "c") == table_key("a", "b.c")
    summaries.save(good.model_copy(update={"schema_name": "a.b", "table_name": "c"}))
    assert summaries.load(REL, "a", "b.c", **key) is None


def test_prune_removes_the_summaries_of_pruned_profiles(tmp_path: Path, db: Path) -> None:
    summaries = make_store(tmp_path)
    for rel in ("keep/a.duckdb", "gone/b.duckdb"):
        summaries.get_or_compute(db, rel, "main", "k", QueryConfig(), row_count=100)
    profiles = summaries.profiles
    assert (summaries.folder / file_key("gone/b.duckdb")).is_dir()
    assert profiles.prune({"keep/a.duckdb"}) == 0  # no profile files existed
    assert (summaries.folder / file_key("keep/a.duckdb")).is_dir()
    assert not (summaries.folder / file_key("gone/b.duckdb")).exists()
    profiles.prune(set())
    assert not (summaries.folder / file_key("keep/a.duckdb")).exists()


# ---------------------------------------------------------------- odd tables (review test gap 6)


@pytest.fixture
def odd_db(tmp_path: Path) -> Path:
    path = tmp_path / "odd.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE nested AS SELECT [i, i + 1] AS l, {'a': i, 'b': 'x'} AS s, "
            "map {'k': i} AS m FROM range(5) r(i)"
        )
        con.execute("CREATE TABLE bin AS SELECT encode('v' || i::VARCHAR) AS b FROM range(5) r(i)")
        con.execute("CREATE TABLE base AS SELECT i, 'x' AS v FROM range(5) r(i)")
        con.execute("CREATE VIEW orphan AS SELECT * FROM base")
    finally:
        con.close()
    con = duckdb.connect(str(path))
    try:
        con.execute("DROP TABLE base")  # DuckDB lets a base table go; the view stays and breaks
    finally:
        con.close()
    return path


def test_a_table_with_only_nested_columns_is_summarised(odd_db: Path) -> None:
    s = summarize(odd_db, "nested", row_count=5)
    assert s.error is None
    assert [c.column_name for c in s.columns] == ["l", "s", "m"]
    kinds = [c.column_type.split("(")[0].split("[")[0] for c in s.columns]
    assert kinds == ["BIGINT", "STRUCT", "MAP"]
    assert all(c.count == 5 and c.null_percentage == 0.0 and c.avg is None for c in s.columns)


def test_a_table_with_only_a_binary_column_is_summarised(odd_db: Path) -> None:
    s = summarize(odd_db, "bin", row_count=5)
    assert s.error is None and [c.column_type for c in s.columns] == ["BLOB"]
    assert (s.columns[0].min, s.columns[0].max, s.columns[0].avg) == ("v0", "v4", None)


def test_a_view_whose_base_table_was_dropped_gives_an_error_summary(odd_db: Path) -> None:
    s = summarize(odd_db, "orphan", row_count=None)
    assert s.columns == [] and not s.sampled
    assert s.error is not None and s.error.startswith("sql: ") and "base" in s.error


def test_the_summary_store_keeps_the_error_of_an_orphaned_view(
    tmp_path: Path, odd_db: Path
) -> None:
    summaries = make_store(tmp_path)
    s = summaries.get_or_compute(odd_db, REL, "main", "orphan", QueryConfig(), row_count=None)
    assert s.error and summaries.path(REL, "main", "orphan").is_file()
    stat = odd_db.stat()
    assert summaries.load(REL, "main", "orphan", size_bytes=stat.st_size,
                          mtime_ns=stat.st_mtime_ns) is None  # fmt: skip
