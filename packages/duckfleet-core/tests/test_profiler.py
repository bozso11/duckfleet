from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from duckfleet_core import profiler
from duckfleet_core.config import ScanConfig
from duckfleet_core.models import DatabaseProfile, ProfileError
from duckfleet_core.profiler import classify_error, profile_database

RUN1 = "exp_01/run_01/metrics.duckdb"


def make_db(path: Path, *statements: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()
    return path


def profile(path: Path, rel_path: str | None = None, **config) -> DatabaseProfile:
    cfg = ScanConfig(**config).model_dump(mode="json")
    result = profile_database(str(path), rel_path or path.name, cfg)
    assert isinstance(result, dict)
    return DatabaseProfile.model_validate(result)


def table_of(p: DatabaseProfile, name: str):
    return next(t for t in p.tables if t.name == name)


def test_profile_demo_file(demo_fleet: Path) -> None:
    p = profile(demo_fleet / RUN1, RUN1)
    assert p.status == "ok" and p.error is None
    assert p.rel_path == RUN1 and p.file_name == "metrics.duckdb"
    assert p.size_bytes > 0 and p.mtime_ns > 0
    assert p.has_wal and p.health == ["wal_present"]
    assert isinstance(p.storage_version, int) and p.storage_version > 0
    assert p.duckdb_version == duckdb.__version__
    assert p.tags == {"exp": "01", "run": "01"}
    assert p.schemas == ["main"]
    assert [t.name for t in p.tables] == ["config", "events"]
    assert p.total_rows == 1000 + 3
    assert p.fp_strict and p.fp_loose and p.fp_strict != p.fp_loose
    assert p.profile_seconds >= 0
    assert p.profiled_at.endswith("+00:00")

    events = table_of(p, "events")
    assert events.kind == "table" and events.row_count == 1000
    assert events.estimated_size is not None
    assert [(c.name, c.data_type, c.ordinal) for c in events.columns] == [
        ("ts", "TIMESTAMP", 1),
        ("value", "DOUBLE", 2),
        ("run_id", "INTEGER", 3),
    ]
    ts, value, run_id = events.columns
    assert ts.is_temporal and not ts.is_numeric
    assert value.is_numeric and not value.is_temporal
    assert ts.stats is not None and ts.stats.min == "2026-01-05T00:00:00"
    assert ts.stats.max is not None and ts.stats.max.startswith("2026-01-11T")
    assert ts.stats.non_null_count == 1000 and not ts.stats.sampled
    assert value.stats is not None and float(value.stats.min) < float(value.stats.max)
    assert run_id.stats is not None and (run_id.stats.min, run_id.stats.max) == ("1", "1")
    assert run_id.stats.approx_distinct == 1
    assert events.time_min == ts.stats.min and events.time_max == ts.stats.max
    assert p.time_min == events.time_min and p.time_max == events.time_max

    config = table_of(p, "config")
    key = config.columns[0]
    assert config.row_count == 3 and key.stats is not None
    assert key.stats.min is None and key.stats.max is None  # VARCHAR: no min/max
    assert key.stats.approx_distinct == 3
    assert config.time_min is None


def test_profile_roundtrips_through_json(demo_fleet: Path) -> None:
    p = profile(demo_fleet / RUN1, RUN1)
    again = DatabaseProfile.model_validate_json(p.model_dump_json())
    assert again == p


def test_fingerprints_across_demo_experiments(demo_fleet: Path) -> None:
    one = profile(demo_fleet / RUN1, RUN1)
    two = profile(demo_fleet / "exp_02/run_01/metrics.duckdb")
    three = profile(demo_fleet / "exp_03/run_01/metrics.duckdb")
    assert one.fp_strict != two.fp_strict
    assert one.fp_loose == two.fp_loose
    assert one.fp_loose != three.fp_loose
    assert table_of(one, "config").fp_strict == table_of(two, "config").fp_strict
    assert table_of(one, "events").fp_strict != table_of(two, "events").fp_strict
    assert table_of(one, "events").fp_loose == table_of(two, "events").fp_loose
    assert [t.name for t in three.tables] == ["config", "events", "notes"]


def test_wide_table_with_200_columns(tmp_path: Path) -> None:
    columns = ", ".join(f"c{i} INTEGER" for i in range(200))
    values = ", ".join(str(i) for i in range(200))
    path = make_db(
        tmp_path / "wide.duckdb",
        f"CREATE TABLE wide ({columns})",
        f"INSERT INTO wide VALUES ({values}), ({values})",
    )
    p = profile(path)
    assert p.status == "ok"
    wide = table_of(p, "wide")
    assert wide.error is None and wide.row_count == 2 and len(wide.columns) == 200
    last = wide.columns[199]
    assert last.stats is not None and last.stats.min == "199" and last.stats.non_null_count == 2
    assert "no_temporal_columns" in p.health


def test_table_above_threshold_is_sampled(demo_fleet: Path) -> None:
    exact = profile(demo_fleet / RUN1, RUN1)
    sampled = profile(demo_fleet / RUN1, RUN1, stats_row_threshold=100, stats_sample_rows=50)
    events = table_of(sampled, "events")
    ts, value, run_id = events.columns
    assert events.row_count == 1000  # the row count itself is always exact
    assert all(c.stats is not None and c.stats.sampled for c in events.columns)
    assert value.stats is not None and run_id.stats is not None
    assert 0 < (value.stats.non_null_count or 0) <= 50
    # temporal min/max are exact even when the rest is sampled, so time windows stay right
    assert ts.stats is not None
    assert ts.stats.min == table_of(exact, "events").columns[0].stats.min
    assert ts.stats.max == table_of(exact, "events").columns[0].stats.max
    assert sampled.time_min == exact.time_min and sampled.time_max == exact.time_max
    # the small table stays below the threshold of 100 rows
    config = table_of(sampled, "config")
    assert all(c.stats is not None and not c.stats.sampled for c in config.columns)


def test_empty_database(demo_fleet: Path) -> None:
    p = profile(demo_fleet / "empty" / "empty.duckdb", "empty/empty.duckdb")
    assert p.status == "ok"
    assert p.tables == [] and p.schemas == []
    assert p.health == ["empty_database"]
    assert p.fp_strict is None and p.fp_loose is None
    assert p.total_rows == 0 and p.time_min is None


def test_corrupt_file(demo_fleet: Path) -> None:
    p = profile(demo_fleet / "broken" / "corrupt.duckdb", "broken/corrupt.duckdb")
    assert p.status == "error" and p.error is not None
    assert p.error.type == "corrupt", p.error
    assert p.error.exception and 0 < len(p.error.message) <= 500
    assert p.tables == [] and p.fp_strict is None
    assert p.size_bytes == 4096 + 8192


def test_non_duckdb_files_never_reach_duckdb(demo_fleet: Path) -> None:
    for rel in ("legacy/sqlite_lookalike.db", "broken/truncated.duckdb"):
        p = profile(demo_fleet / rel, rel)
        assert p.status == "error" and p.error is not None
        assert p.error.type == "not_duckdb", p.error


def test_missing_file(tmp_path: Path) -> None:
    p = profile(tmp_path / "gone.duckdb")
    assert p.status == "error" and p.error is not None and p.error.type == "missing"
    assert p.size_bytes == 0


def test_never_raises_with_a_bad_config(demo_fleet: Path) -> None:
    result = profile_database(str(demo_fleet / RUN1), RUN1, {"max_files": "not a number"})
    assert DatabaseProfile.model_validate(result).status == "ok"


def test_views_are_listed_but_not_fingerprinted_by_default(tmp_path: Path) -> None:
    path = make_db(
        tmp_path / "v.duckdb",
        "CREATE TABLE t (id INTEGER, ts TIMESTAMP)",
        "INSERT INTO t VALUES (1, '2026-01-01 00:00:00'), (2, '2026-01-02 00:00:00')",
        "CREATE VIEW v AS SELECT id FROM t",
    )
    default = profile(path)
    view = table_of(default, "v")
    assert view.kind == "view" and view.row_count is None and view.estimated_size is None
    assert view.fp_strict and view.columns[0].stats is None
    assert default.total_rows == 2

    counted = profile(path, count_view_rows=True)
    assert table_of(counted, "v").row_count == 2
    assert counted.total_rows == 2  # views never add to the total

    with_views = profile(path, include_views_in_fingerprint=True)
    assert with_views.fp_strict != default.fp_strict
    assert with_views.fp_loose != default.fp_loose
    assert default.fp_strict == profile(path).fp_strict


def test_quoted_identifiers(tmp_path: Path) -> None:
    path = make_db(
        tmp_path / "q.duckdb",
        'CREATE SCHEMA "my schema"',
        'CREATE TABLE "my schema"."we""ird" ("a""b" INTEGER, "ts col" TIMESTAMP)',
        'INSERT INTO "my schema"."we""ird" VALUES (1, \'2026-03-01 12:00:00\')',
    )
    p = profile(path)
    assert p.status == "ok"
    assert p.schemas == ["my schema"]
    weird = table_of(p, 'we"ird')
    assert weird.error is None and weird.row_count == 1
    assert [c.name for c in weird.columns] == ['a"b', "ts col"]
    assert weird.time_min == "2026-03-01T12:00:00"


def test_temporal_types_and_time_windows(tmp_path: Path) -> None:
    path = make_db(
        tmp_path / "t.duckdb",
        "CREATE TABLE a (d DATE, tz TIMESTAMPTZ, ns TIMESTAMP_NS, s TIMESTAMP_S, ms TIMESTAMP_MS)",
        "INSERT INTO a VALUES ('2026-02-01', '2026-01-01 10:00:00+02', '2026-03-01 00:00:00',"
        " '2026-04-01 00:00:00', '2026-05-01 00:00:00'),"
        " ('2026-02-10', '2026-01-02 10:00:00+02', '2026-03-05 00:00:00',"
        " '2026-04-02 00:00:00', '2026-05-02 00:00:00')",
        "CREATE TABLE b (when_ DATE)",
        "INSERT INTO b VALUES ('2025-12-25')",
    )
    p = profile(path)
    a = table_of(p, "a")
    assert all(c.is_temporal for c in a.columns)
    d, tz = a.columns[0], a.columns[1]
    assert d.stats is not None and (d.stats.min, d.stats.max) == ("2026-02-01", "2026-02-10")
    # timestamptz is converted to UTC in SQL: 10:00+02 is 08:00 UTC, whatever the session zone
    assert tz.stats is not None and tz.stats.min == "2026-01-01T08:00:00"
    assert a.time_min == "2026-01-01T08:00:00"
    assert a.time_max == "2026-05-02T00:00:00"
    # database window spans tables; dates compare as midnight
    assert p.time_min == "2025-12-25T00:00:00" and p.time_max == "2026-05-02T00:00:00"
    assert "no_temporal_columns" not in p.health


def test_nested_and_blob_columns_have_no_distinct_or_minmax(tmp_path: Path) -> None:
    path = make_db(
        tmp_path / "n.duckdb",
        "CREATE TABLE n (s STRUCT(a INTEGER), l INTEGER[], m MAP(VARCHAR, INTEGER),"
        " b BLOB, e ENUM('x','y'), dec DECIMAL(18,3), u UUID)",
        "INSERT INTO n VALUES ({'a': 1}, [1, 2], MAP {'k': 1}, 'ab'::BLOB, 'x', 1.5, uuid())",
    )
    p = profile(path)
    n = table_of(p, "n")
    assert n.error is None
    stats = {c.name: c for c in n.columns}
    for name in ("s", "l", "m", "b"):
        assert stats[name].stats is not None
        assert stats[name].stats.approx_distinct is None
        assert stats[name].stats.min is None
        assert stats[name].stats.non_null_count == 1
    assert stats["e"].stats is not None and stats["e"].stats.approx_distinct == 1
    assert stats["dec"].is_numeric and stats["dec"].data_type == "DECIMAL(18,3)"
    assert stats["dec"].stats is not None and stats["dec"].stats.min == "1.500"


def test_table_error_is_recorded_and_the_table_stays_listed(
    demo_fleet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = profiler._table_stats

    def flaky(con, qualified, columns, row_count, config):
        if qualified.endswith('"events"'):
            raise duckdb.Error("boom")
        return real(con, qualified, columns, row_count, config)

    monkeypatch.setattr(profiler, "_table_stats", flaky)
    p = profile(demo_fleet / "exp_01/run_02/metrics.duckdb", "exp_01/run_02/metrics.duckdb")
    assert p.status == "ok"
    events = table_of(p, "events")
    assert events.error is not None and "boom" in events.error
    assert events.row_count == 1000  # the count happened before the failure
    assert events.fp_strict
    assert "table_error:main.events" in p.health
    assert table_of(p, "config").error is None


# ---------------------------------------------------------------- classify_error


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (
            duckdb.IOException(
                'IO Error: Cannot open file "C:\\x.duckdb": The process cannot access the file '
                "because it is being used by another process."
            ),
            "locked",
        ),
        (
            duckdb.IOException(
                'IO Error: Could not set lock on file "/x.duckdb": Conflicting lock is held in '
                "/usr/bin/python3 (PID 12) by user u."
            ),
            "locked",
        ),
        (
            duckdb.IOException(
                'IO Error: Cannot open database "x.duckdb" in read-only mode: database does not '
                "exist"
            ),
            "missing",
        ),
        (FileNotFoundError(2, "No such file or directory"), "missing"),
        (
            duckdb.IOException(
                'IO Error: The file "x" exists, but it is not a valid DuckDB database file!'
            ),
            "not_duckdb",
        ),
        (
            duckdb.IOException(
                "IO Error: Corrupt database file: computed checksum 1 does not match stored 2"
            ),
            "corrupt",
        ),
        (
            duckdb.IOException(
                'IO Error: Could not read enough bytes from file "x": Reached the end of the file'
            ),
            "corrupt",
        ),
        (
            duckdb.IOException(
                "IO Error: Trying to read a database file with version number 99, but we can only "
                "read version numbers 64"
            ),
            "incompatible_version",
        ),
        (duckdb.IOException("IO Error: database is Encrypted, supply a key"), "encrypted"),
        (RuntimeError("something else"), "other"),
    ],
)
def test_classify_error(exception: BaseException, expected: str) -> None:
    error = classify_error(exception)
    assert isinstance(error, ProfileError)
    assert error.type == expected
    assert error.exception == type(exception).__name__


def test_classify_error_truncates_the_message_and_ignores_case() -> None:
    error = classify_error(RuntimeError("X" * 2000 + " COULD NOT SET LOCK"))
    assert len(error.message) == 500
    assert classify_error(RuntimeError("CORRUPT DATABASE FILE")).type == "corrupt"
    # plan order: locked wins over corrupt
    assert classify_error(RuntimeError("corrupt, being used by another process")).type == "locked"


# ---------------------------------------------------------------- locking


@pytest.mark.timeout(60)
def test_database_held_by_a_writer_is_reported_locked(tmp_path: Path, hold_open) -> None:
    db = tmp_path / "held.duckdb"
    with hold_open(db):
        p = profile(db, "held.duckdb")
    assert p.status == "error" and p.error is not None
    assert p.error.type == "locked", p.error
    assert p.size_bytes > 0  # stat still works while the file is held


def test_permission_denied_means_locked_only_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = PermissionError(13, "Permission denied")
    monkeypatch.setattr("duckfleet_core.discovery.sys.platform", "win32")
    assert classify_error(error).type == "locked"
    monkeypatch.setattr("duckfleet_core.discovery.sys.platform", "linux")
    assert classify_error(error).type == "other"
