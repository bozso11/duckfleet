from __future__ import annotations

import json
import os
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import _cancel_worker
import duckdb
import pytest

from duckflock_core import coordinator
from duckflock_core.catalog import CatalogReader
from duckflock_core.config import ScanConfig
from duckflock_core.coordinator import run_scan
from duckflock_core.demo import make_demo_flock
from duckflock_core.store import ProfileStore, recent_roots, root_key

RUN1 = "exp_01/run_01/metrics.duckdb"
CORRUPT = "broken/corrupt.duckdb"
HIGHBIT = "broken/highbit.duckdb"
ENCRYPTED = "secure/encrypted.duckdb"
DEMO_FAILURES = {(CORRUPT, "corrupt"), (HIGHBIT, "corrupt"), (ENCRYPTED, "encrypted")}


def scan(flock: Path, cache: Path, **options):
    events: list[dict] = []
    summary = run_scan(flock, ScanConfig(cache_dir=str(cache), **options), events.append)
    return summary, events


def snapshot(reader: CatalogReader) -> dict:
    """Everything in the catalog except timings, for comparing two scans."""
    databases = [
        {k: v for k, v in row.items() if k not in ("profiled_at", "profile_seconds")}
        for row in reader.databases()
    ]
    return {
        "databases": databases,
        "tables": reader.tables(),
        "columns": [c for d in databases for c in reader.columns(d["rel_path"])],
        "families": [reader.families(level) for level in ("strict", "loose")],
        "matrix": [reader.family_table_matrix(level) for level in ("strict", "loose")],
        "variants": reader.table_variants(),
        "rejected": reader.rejected(),
    }


def test_first_scan_of_the_demo_flock(demo_flock: Path, cache_dir: Path) -> None:
    summary, events = scan(demo_flock, cache_dir, workers=1)
    assert (summary.files_candidates, summary.files_rejected) == (13, 2)
    assert (summary.files_profiled, summary.files_reused, summary.files_failed) == (13, 0, 3)
    assert summary.files_walked == 16 and not summary.truncated
    assert summary.workers == 1 and summary.config.workers == 1
    assert summary.root == str(demo_flock.resolve())
    assert {(r.rel_path, r.reason) for r in summary.rejected} == {
        ("legacy/sqlite_lookalike.db", "not_duckdb_header"),
        ("broken/truncated.duckdb", "not_duckdb_header"),
    }

    reader = CatalogReader(summary.catalog_path)
    databases = reader.databases()
    assert sum(d["status"] == "ok" for d in databases) == 10
    errors = [d for d in databases if d["status"] == "error"]
    assert [(e["rel_path"], e["error_type"]) for e in errors] == sorted(DEMO_FAILURES)
    assert [f["n_members"] for f in reader.families("strict")] == [3, 3, 2, 1]
    assert [f["n_members"] for f in reader.families("loose")] == [6, 2, 1]
    assert reader.scan_run()["files_candidates"] == 13

    kinds = [e["event"] for e in events]
    assert kinds == ["discovered", "planned", *["profiled"] * 13, "catalog", "finished"]
    assert events[0] == {
        "event": "discovered",
        "walked": 16,
        "candidates": 13,
        "rejected": 2,
        "truncated": False,
    }
    assert events[1] == {"event": "planned", "to_profile": 13, "reused": 0}
    profiled = [e for e in events if e["event"] == "profiled"]
    assert [e["done"] for e in profiled] == list(range(1, 14))
    assert {e["total"] for e in profiled} == {13}
    bad = [e for e in profiled if e["status"] == "error"]
    assert {(e["rel_path"], e["error_type"]) for e in bad} == DEMO_FAILURES
    assert all(e["error_type"] is None for e in profiled if e["status"] == "ok")
    assert events[-2] == {"event": "catalog", "path": summary.catalog_path}
    finished = events[-1]["summary"]
    assert json.loads(json.dumps(finished)) == finished  # JSON-serialisable
    assert finished["files_candidates"] == 13 and finished["run_id"] == summary.run_id


def test_cache_directory_contents(demo_flock: Path, cache_dir: Path) -> None:
    summary, _ = scan(demo_flock, cache_dir, workers=1)
    store = ProfileStore(cache_dir, demo_flock)
    assert Path(summary.catalog_path) == store.catalog_path
    assert store.catalog_path.is_file() and not store.tmp_catalog_path.exists()
    assert len(list(store.profiles_dir.glob("*.json"))) == 13
    assert (store.root_dir / "root.txt").read_text(encoding="utf-8") == str(demo_flock.resolve())
    assert store.load_summary() == summary
    assert recent_roots(cache_dir)[0]["root"] == str(demo_flock.resolve())
    assert (cache_dir / "roots" / root_key(demo_flock)).is_dir()


def test_nothing_is_written_inside_the_scanned_folder(
    fresh_demo_flock: Path, cache_dir: Path
) -> None:
    def state() -> dict[str, tuple[int, int]]:
        return {
            p.relative_to(fresh_demo_flock).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in fresh_demo_flock.rglob("*")
            if p.is_file()
        }

    before = state()
    scan(fresh_demo_flock, cache_dir, workers=1)
    scan(fresh_demo_flock, cache_dir, workers=2)
    assert state() == before


@pytest.mark.timeout(120)
def test_one_worker_and_a_pool_produce_the_same_catalog(demo_flock: Path, tmp_path: Path) -> None:
    single, _ = scan(demo_flock, tmp_path / "c1", workers=1)
    pooled, events = scan(demo_flock, tmp_path / "c2", workers=2)  # real spawn pool
    assert pooled.workers == 2
    assert [e["done"] for e in events if e["event"] == "profiled"] == list(range(1, 14))
    assert snapshot(CatalogReader(single.catalog_path)) == snapshot(
        CatalogReader(pooled.catalog_path)
    )


def test_second_scan_reuses_ok_profiles_and_retries_failures(
    demo_flock: Path, cache_dir: Path
) -> None:
    scan(demo_flock, cache_dir, workers=1)
    summary, events = scan(demo_flock, cache_dir, workers=1)
    assert (summary.files_reused, summary.files_profiled, summary.files_failed) == (10, 3, 3)
    assert summary.files_candidates == 13
    assert [e for e in events if e["event"] == "planned"] == [
        {"event": "planned", "to_profile": 3, "reused": 10}
    ]
    profiled = [e for e in events if e["event"] == "profiled"]
    assert {(e["rel_path"], e["error_type"]) for e in profiled} == DEMO_FAILURES
    reader = CatalogReader(summary.catalog_path)
    assert len(reader.databases()) == 13 and len(reader.families("strict")) == 4


def test_full_scan_reuses_nothing(demo_flock: Path, cache_dir: Path) -> None:
    scan(demo_flock, cache_dir, workers=1)
    summary, _ = scan(demo_flock, cache_dir, workers=1, full=True)
    assert (summary.files_reused, summary.files_profiled) == (0, 13)


def test_deleting_a_file_prunes_its_profile(fresh_demo_flock: Path, cache_dir: Path) -> None:
    scan(fresh_demo_flock, cache_dir, workers=1)
    store = ProfileStore(cache_dir, fresh_demo_flock)
    assert store.load("legacy/old.db") is not None
    (fresh_demo_flock / "legacy" / "old.db").unlink()
    summary, _ = scan(fresh_demo_flock, cache_dir, workers=1)
    assert summary.files_candidates == 12 and summary.files_reused == 9
    assert store.load("legacy/old.db") is None
    assert len(list(store.profiles_dir.glob("*.json"))) == 12
    reader = CatalogReader(summary.catalog_path)
    assert "legacy/old.db" not in {d["rel_path"] for d in reader.databases()}
    assert [f["n_members"] for f in reader.families("strict")] == [3, 3, 2]


def test_a_changed_file_is_profiled_again(fresh_demo_flock: Path, cache_dir: Path) -> None:
    scan(fresh_demo_flock, cache_dir, workers=1)
    path = fresh_demo_flock / RUN1
    con = duckdb.connect(str(path))
    con.execute("INSERT INTO config VALUES ('extra', 'row')")
    con.close()
    summary, events = scan(fresh_demo_flock, cache_dir, workers=1)
    reprofiled = {e["rel_path"] for e in events if e["event"] == "profiled"}
    assert reprofiled == {RUN1, CORRUPT, HIGHBIT, ENCRYPTED}
    config = [
        t for t in CatalogReader(summary.catalog_path).tables(RUN1) if t["table_name"] == "config"
    ]
    assert config[0]["row_count"] == 4


def test_a_wal_that_appears_or_disappears_triggers_a_new_profile(
    fresh_demo_flock: Path, cache_dir: Path
) -> None:
    scan(fresh_demo_flock, cache_dir, workers=1)
    (fresh_demo_flock / (RUN1 + ".wal")).unlink()
    summary, events = scan(fresh_demo_flock, cache_dir, workers=1)
    assert RUN1 in {e["rel_path"] for e in events if e["event"] == "profiled"}
    row = next(d for d in CatalogReader(summary.catalog_path).databases() if d["rel_path"] == RUN1)
    assert row["has_wal"] is False and row["health"] == []


def test_profiles_from_another_schema_version_are_profiled_again(
    fresh_demo_flock: Path, cache_dir: Path
) -> None:
    scan(fresh_demo_flock, cache_dir, workers=1)
    store = ProfileStore(cache_dir, fresh_demo_flock)
    stale = store.load("legacy/old.db")
    assert stale is not None
    store.save(stale.model_copy(update={"profile_schema_version": 0}))
    summary, events = scan(fresh_demo_flock, cache_dir, workers=1)
    assert "legacy/old.db" in {e["rel_path"] for e in events if e["event"] == "profiled"}
    assert summary.files_reused == 9


def test_profiles_from_another_duckdb_version_are_profiled_again(
    fresh_demo_flock: Path, cache_dir: Path
) -> None:
    """P-01: after a DuckDB upgrade type spellings can change, so nothing is reused."""
    scan(fresh_demo_flock, cache_dir, workers=1)
    store = ProfileStore(cache_dir, fresh_demo_flock)
    stale = store.load("legacy/old.db")
    assert stale is not None and stale.duckdb_version == duckdb.__version__
    store.save(stale.model_copy(update={"duckdb_version": "0.9.0"}))
    summary, events = scan(fresh_demo_flock, cache_dir, workers=1)
    assert "legacy/old.db" in {e["rel_path"] for e in events if e["event"] == "profiled"}
    assert summary.files_reused == 9
    reloaded = store.load("legacy/old.db")
    assert reloaded is not None and reloaded.duckdb_version == duckdb.__version__


def test_truncated_walk(demo_flock: Path, cache_dir: Path) -> None:
    summary, events = scan(demo_flock, cache_dir, workers=1, max_files=3)
    assert summary.truncated and summary.files_candidates == 3
    assert events[0]["truncated"] is True
    assert "over_max_files" in {r.reason for r in summary.rejected}
    assert CatalogReader(summary.catalog_path).scan_run()["truncated"] is True


def test_missing_root_raises(tmp_path: Path, cache_dir: Path) -> None:
    with pytest.raises(NotADirectoryError):
        run_scan(tmp_path / "nope", ScanConfig(cache_dir=str(cache_dir)))


def test_works_without_an_event_callback(demo_flock: Path, cache_dir: Path) -> None:
    summary = run_scan(demo_flock, ScanConfig(cache_dir=str(cache_dir), workers=1))
    assert summary.files_candidates == 13


def test_workers_default_to_cpu_count_minus_two(
    demo_flock: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 3)
    summary = run_scan(demo_flock, ScanConfig(cache_dir=str(cache_dir)))
    assert summary.workers == 1
    assert summary.config.workers == 1 and summary.config.worker_memory_limit_mb >= 256
    monkeypatch.setattr(os, "cpu_count", lambda: 40)
    summary = run_scan(demo_flock, ScanConfig(cache_dir=str(cache_dir), max_files=1))
    assert summary.workers == 38  # the configured count; the pool never exceeds the files to do


def test_non_ascii_root_with_a_space_and_a_pool(tmp_path: Path) -> None:
    flock = make_demo_flock(tmp_path / "données été" / "my flock")
    summary, _ = scan(flock, tmp_path / "cache dir é", workers=2)
    assert (summary.files_candidates, summary.files_failed) == (13, 3)
    names = {d["rel_path"] for d in CatalogReader(summary.catalog_path).databases()}
    assert RUN1 in names


@pytest.mark.timeout(120)
def test_a_valid_tag_pattern_that_matches_nothing_works_in_a_pool(
    demo_flock: Path, cache_dir: Path
) -> None:
    """R-03: the pattern is validated once and shipped to the workers as plain text."""
    summary, _ = scan(demo_flock, cache_dir, workers=2, tag_patterns=["(?P<key>zzz)_(?P<value>9)"])
    assert (summary.files_candidates, summary.files_failed) == (13, 3)
    rows = CatalogReader(summary.catalog_path).databases()
    assert len(rows) == 13 and all(row["tags"] == {} for row in rows)


@pytest.mark.timeout(60)
def test_a_locked_file_is_reported_not_fatal(
    fresh_demo_flock: Path, cache_dir: Path, hold_open
) -> None:
    with hold_open(fresh_demo_flock / "held" / "writer.duckdb"):
        summary, events = scan(fresh_demo_flock, cache_dir, workers=1)
    assert summary.files_candidates == 14 and summary.files_failed == 4
    locked = [
        e for e in events if e["event"] == "profiled" and e["rel_path"] == "held/writer.duckdb"
    ]
    assert [(e["status"], e["error_type"]) for e in locked] == [("error", "locked")]


# ---------------------------------------------------------------- early stop of the pool


class StopScan(Exception):
    pass


@pytest.mark.timeout(120)
def test_a_consumer_error_cancels_the_queued_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P-02: with a real spawn pool, an error after the first profile must not make the pool
    profile all the queued files first. A marker file written inside the workers counts the
    files that were really started: without ``cancel_futures`` all 20 would be.
    """
    flock = tmp_path / "many"
    flock.mkdir()
    for i in range(20):
        con = duckdb.connect(str(flock / f"db_{i:02d}.duckdb"))
        con.execute("CREATE TABLE t AS SELECT range AS i FROM range(2000)")
        con.close()
    markers = tmp_path / "markers"
    markers.mkdir()
    monkeypatch.setenv(_cancel_worker.MARKER_DIR_ENV, str(markers))
    monkeypatch.setattr(coordinator, "profile_database", _cancel_worker.slow_profile)
    config = ScanConfig(cache_dir=str(tmp_path / "cache"), workers=2)
    discovery = coordinator.discover(flock, config)
    assert len(discovery.candidates) == 20
    stream = coordinator._profiles_in_pool(
        discovery.candidates, config.model_dump(mode="json"), config, 2
    )
    with pytest.raises(StopScan):
        for _candidate, _result in stream:
            raise StopScan
    stream.close()  # what run_scan does through contextlib.closing
    started = len(list(markers.glob("*.started")))
    # the files already running or in the call queue finish (2 workers + 1 queued call), the
    # other ~17 are cancelled
    assert 1 <= started <= 8, started


def test_the_pool_is_shut_down_with_cancel_futures_when_the_consumer_stops(
    demo_flock: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeExecutor.instances = []
    monkeypatch.setattr(coordinator, "ProcessPoolExecutor", FakeExecutor)
    seen = []
    with pytest.raises(StopScan):
        run_scan(
            demo_flock,
            ScanConfig(cache_dir=str(cache_dir), workers=4),
            lambda event: (seen.append(event), _raise_on_first_profile(event)),
        )
    (executor,) = FakeExecutor.instances
    assert executor.shutdowns == [{"wait": True, "cancel_futures": True}]
    assert sum(e["event"] == "profiled" for e in seen) == 1


def _raise_on_first_profile(event: dict) -> None:
    if event["event"] == "profiled":
        raise StopScan


# ---------------------------------------------------------------- worker crashes


class FakeExecutor:
    """Stands in for ProcessPoolExecutor: runs a few jobs, then the 'pool' breaks."""

    ok_jobs = 4
    break_on_submit = False
    instances: list[FakeExecutor] = []

    def __init__(self, max_workers=None, mp_context=None):
        self.submitted = 0
        self.shutdowns: list[dict] = []
        FakeExecutor.instances.append(self)

    def shutdown(self, wait=True, *, cancel_futures=False):
        self.shutdowns.append({"wait": wait, "cancel_futures": cancel_futures})

    def submit(self, fn, *args):
        self.submitted += 1
        if self.break_on_submit and self.submitted > self.ok_jobs:
            raise BrokenProcessPool("pool already broken")
        future: Future = Future()
        if self.submitted <= self.ok_jobs:
            future.set_result(fn(*args))
        else:
            future.set_exception(BrokenProcessPool("a worker process died abruptly"))
        return future


def test_a_broken_pool_marks_unfinished_files_worker_crashed(
    demo_flock: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coordinator, "ProcessPoolExecutor", FakeExecutor)
    summary, events = scan(demo_flock, cache_dir, workers=4)
    profiled = [e for e in events if e["event"] == "profiled"]
    assert len(profiled) == 13
    crashed = [e for e in profiled if e["error_type"] == "worker_crashed"]
    assert len(crashed) == 9  # 13 files, the first 4 finished before the pool broke
    reader = CatalogReader(summary.catalog_path)
    rows = {d["rel_path"]: d for d in reader.databases()}
    assert len(rows) == 13  # the catalog is still built, crashed files included
    assert {rows[e["rel_path"]]["error_type"] for e in crashed} == {"worker_crashed"}
    assert all(rows[e["rel_path"]]["status"] == "error" for e in crashed)
    assert all("worker process died" in rows[e["rel_path"]]["error_message"] for e in crashed)
    assert summary.files_failed >= 9
    # a rescan retries them (errors are never reused)
    monkeypatch.undo()
    again, _ = scan(demo_flock, cache_dir, workers=1)
    assert again.files_profiled >= 9 and again.files_failed == 3


def test_a_pool_that_breaks_during_submit(
    demo_flock: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BreakingExecutor(FakeExecutor):
        break_on_submit = True
        ok_jobs = 3

    monkeypatch.setattr(coordinator, "ProcessPoolExecutor", BreakingExecutor)
    summary, events = scan(demo_flock, cache_dir, workers=4)
    profiled = [e for e in events if e["event"] == "profiled"]
    assert len(profiled) == 13
    assert sum(e["error_type"] == "worker_crashed" for e in profiled) == 10
    assert summary.files_candidates == 13
