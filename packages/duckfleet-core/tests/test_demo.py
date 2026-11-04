from __future__ import annotations

from pathlib import Path

import duckdb

from duckfleet_core.demo import make_demo_fleet

EXPECTED_FILES = {
    "exp_01/run_01/metrics.duckdb",
    "exp_01/run_02/metrics.duckdb",
    "exp_01/run_03/metrics.duckdb",
    "exp_02/run_01/metrics.duckdb",
    "exp_02/run_02/metrics.duckdb",
    "exp_02/run_03/metrics.duckdb",
    "exp_03/run_01/metrics.duckdb",
    "exp_03/run_02/metrics.duckdb",
    "legacy/old.db",
    "legacy/sqlite_lookalike.db",
    "empty/empty.duckdb",
    "broken/corrupt.duckdb",
    "broken/truncated.duckdb",
    "exp_01/run_01/metrics.duckdb.wal",
}


def query(path: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_demo_fleet_has_exactly_the_planned_files(demo_fleet: Path) -> None:
    found = {p.relative_to(demo_fleet).as_posix() for p in demo_fleet.rglob("*") if p.is_file()}
    assert found == EXPECTED_FILES


def test_events_tables_and_windows_are_consecutive(demo_fleet: Path) -> None:
    runs = [f"exp_01/run_0{i}" for i in (1, 2, 3)]
    runs += [f"exp_02/run_0{i}" for i in (1, 2, 3)] + [f"exp_03/run_0{i}" for i in (1, 2)]
    windows = []
    for run in runs:
        path = demo_fleet / run / "metrics.duckdb"
        ((rows, low, high),) = query(path, "select count(*), min(ts), max(ts) from events")
        assert rows == 1000
        assert (high - low).days == 6  # 7-day window
        windows.append((low, high))
    assert windows == sorted(windows)
    for (_, previous_high), (next_low, _) in zip(windows, windows[1:], strict=False):
        assert 0 < (next_low - previous_high).total_seconds() < 3600 * 24


def test_column_types_per_experiment(demo_fleet: Path) -> None:
    sql = "select column_name, data_type from duckdb_columns() where table_name='events'"
    exp1 = dict(query(demo_fleet / "exp_01" / "run_01" / "metrics.duckdb", sql))
    exp2 = dict(query(demo_fleet / "exp_02" / "run_01" / "metrics.duckdb", sql))
    exp3 = dict(query(demo_fleet / "exp_03" / "run_01" / "metrics.duckdb", sql))
    assert exp1 == {"ts": "TIMESTAMP", "value": "DOUBLE", "run_id": "INTEGER"}
    assert exp2 == {**exp1, "value": "FLOAT"}
    assert exp3 == {**exp1, "quality": "SMALLINT"}
    notes = query(demo_fleet / "exp_03" / "run_02" / "metrics.duckdb", "select count(*) from notes")
    assert notes == [(2,)]


def test_config_table_identical_across_runs(demo_fleet: Path) -> None:
    sql = "select key, value from config order by key"
    base = query(demo_fleet / "exp_01" / "run_01" / "metrics.duckdb", sql)
    assert len(base) == 3
    for run in ("exp_01/run_02", "exp_02/run_03", "exp_03/run_02"):
        assert query(demo_fleet / run / "metrics.duckdb", sql) == base


def test_legacy_empty_and_broken_files(demo_fleet: Path) -> None:
    assert query(demo_fleet / "legacy" / "old.db", "select count(*) from samples") == [(100,)]
    assert query(demo_fleet / "empty" / "empty.duckdb", "select count(*) from duckdb_tables()") == [
        (0,)
    ]
    assert (
        (demo_fleet / "legacy" / "sqlite_lookalike.db")
        .read_bytes()
        .startswith(b"SQLite format 3\x00")
    )
    assert (demo_fleet / "broken" / "truncated.duckdb").stat().st_size == 100
    corrupt = (demo_fleet / "broken" / "corrupt.duckdb").read_bytes()
    assert corrupt[8:12] == b"DUCK" and len(corrupt) == 4096 + 8192
    assert (demo_fleet / "exp_01" / "run_01" / "metrics.duckdb.wal").stat().st_size == 0


def test_generation_is_repeatable_and_seed_changes_values(tmp_path: Path) -> None:
    sql = "select sum(value) from events"
    make_demo_fleet(tmp_path / "a", seed=0)
    make_demo_fleet(tmp_path / "b", seed=0)
    make_demo_fleet(tmp_path / "c", seed=5)
    rel = "exp_01/run_01/metrics.duckdb"
    a, b, c = (query(tmp_path / name / rel, sql) for name in "abc")
    assert a == b
    assert a != c
    # regenerating into an existing folder overwrites instead of failing
    make_demo_fleet(tmp_path / "a", seed=0)
    assert query(tmp_path / "a" / rel, sql) == a
