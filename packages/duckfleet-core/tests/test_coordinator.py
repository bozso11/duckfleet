from __future__ import annotations

import json
import os
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import duckdb
import pytest

from duckfleet_core import coordinator
from duckfleet_core.catalog import CatalogReader
from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.demo import make_demo_fleet
from duckfleet_core.store import ProfileStore, recent_roots, root_key

RUN1 = "exp_01/run_01/metrics.duckdb"
CORRUPT = "broken/corrupt.duckdb"


def scan(fleet: Path, cache: Path, **options):
    events: list[dict] = []
    summary = run_scan(fleet, ScanConfig(cache_dir=str(cache), **options), events.append)
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


def test_first_scan_of_the_demo_fleet(demo_fleet: Path, cache_dir: Path) -> None:
    summary, events = scan(demo_fleet, cache_dir, workers=1)
    assert (summary.files_candidates, summary.files_rejected) == (11, 2)
    assert (summary.files_profiled, summary.files_reused, summary.files_failed) == (11, 0, 1)
    assert summary.files_walked == 14 and not summary.truncated
    assert summary.workers == 1 and summary.config.workers == 1
    assert summary.root == str(demo_fleet.resolve())
    assert {(r.rel_path, r.reason) for r in summary.rejected} == {
        ("legacy/sqlite_lookalike.db", "not_duckdb_header"),
        ("broken/truncated.duckdb", "not_duckdb_header"),
    }

    reader = CatalogReader(summary.catalog_path)
    databases = reader.databases()
    assert sum(d["status"] == "ok" for d in databases) == 10
    errors = [d for d in databases if d["status"] == "error"]
    assert [(e["rel_path"], e["error_type"]) for e in errors] == [(CORRUPT, "corrupt")]
    assert [f["n_members"] for f in reader.families("strict")] == [3, 3, 2, 1]
    assert [f["n_members"] for f in reader.families("loose")] == [6, 2, 1]
    assert reader.scan_run()["files_candidates"] == 11

    kinds = [e["event"] for e in events]
    assert kinds == ["discovered", "planned", *["profiled"] * 11, "catalog", "finished"]
    assert events[0] == {
        "event": "discovered",
        "walked": 14,
        "candidates": 11,
        "rejected": 2,
        "truncated": False,
    }
    assert events[1] == {"event": "planned", "to_profile": 11, "reused": 0}
    profiled = [e for e in events if e["event"] == "profiled"]
    assert [e["done"] for e in profiled] == list(range(1, 12))
    assert {e["total"] for e in profiled} == {11}
    bad = [e for e in profiled if e["status"] == "error"]
    assert [(e["rel_path"], e["error_type"]) for e in bad] == [(CORRUPT, "corrupt")]
    assert all(e["error_type"] is None for e in profiled if e["status"] == "ok")
    assert events[-2] == {"event": "catalog", "path": summary.catalog_path}
    finished = events[-1]["summary"]
    assert json.loads(json.dumps(finished)) == finished  # JSON-serialisable
    assert finished["files_candidates"] == 11 and finished["run_id"] == summary.run_id


def test_cache_directory_contents(demo_fleet: Path, cache_dir: Path) -> None:
    summary, _ = scan(demo_fleet, cache_dir, workers=1)
    store = ProfileStore(cache_dir, demo_fleet)
    assert Path(summary.catalog_path) == store.catalog_path
    assert store.catalog_path.is_file() and not store.tmp_catalog_path.exists()
    assert len(list(store.profiles_dir.glob("*.json"))) == 11
    assert (store.root_dir / "root.txt").read_text(encoding="utf-8") == str(demo_fleet.resolve())
    assert store.load_summary() == summary
    assert recent_roots(cache_dir)[0]["root"] == str(demo_fleet.resolve())
    assert (cache_dir / "roots" / root_key(demo_fleet)).is_dir()


def test_nothing_is_written_inside_the_scanned_folder(
    fresh_demo_fleet: Path, cache_dir: Path
) -> None:
    def state() -> dict[str, tuple[int, int]]:
        return {
            p.relative_to(fresh_demo_fleet).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in fresh_demo_fleet.rglob("*")
            if p.is_file()
        }

    before = state()
    scan(fresh_demo_fleet, cache_dir, workers=1)
    scan(fresh_demo_fleet, cache_dir, workers=2)
    assert state() == before


@pytest.mark.timeout(120)
def test_one_worker_and_a_pool_produce_the_same_catalog(demo_fleet: Path, tmp_path: Path) -> None:
    single, _ = scan(demo_fleet, tmp_path / "c1", workers=1)
    pooled, events = scan(demo_fleet, tmp_path / "c2", workers=2)  # real spawn pool
    assert pooled.workers == 2
    assert [e["done"] for e in events if e["event"] == "profiled"] == list(range(1, 12))
    assert snapshot(CatalogReader(single.catalog_path)) == snapshot(
        CatalogReader(pooled.catalog_path)
    )


def test_second_scan_reuses_ok_profiles_and_retries_failures(
    demo_fleet: Path, cache_dir: Path
) -> None:
    scan(demo_fleet, cache_dir, workers=1)
    summary, events = scan(demo_fleet, cache_dir, workers=1)
    assert (summary.files_reused, summary.files_profiled, summary.files_failed) == (10, 1, 1)
    assert summary.files_candidates == 11
    assert [e for e in events if e["event"] == "planned"] == [
        {"event": "planned", "to_profile": 1, "reused": 10}
    ]
    profiled = [e for e in events if e["event"] == "profiled"]
    assert [(e["rel_path"], e["error_type"]) for e in profiled] == [(CORRUPT, "corrupt")]
    reader = CatalogReader(summary.catalog_path)
    assert len(reader.databases()) == 11 and len(reader.families("strict")) == 4


def test_full_scan_reuses_nothing(demo_fleet: Path, cache_dir: Path) -> None:
    scan(demo_fleet, cache_dir, workers=1)
    summary, _ = scan(demo_fleet, cache_dir, workers=1, full=True)
    assert (summary.files_reused, summary.files_profiled) == (0, 11)


def test_deleting_a_file_prunes_its_profile(fresh_demo_fleet: Path, cache_dir: Path) -> None:
    scan(fresh_demo_fleet, cache_dir, workers=1)
    store = ProfileStore(cache_dir, fresh_demo_fleet)
    assert store.load("legacy/old.db") is not None
    (fresh_demo_fleet / "legacy" / "old.db").unlink()
    summary, _ = scan(fresh_demo_fleet, cache_dir, workers=1)
    assert summary.files_candidates == 10 and summary.files_reused == 9
    assert store.load("legacy/old.db") is None
    assert len(list(store.profiles_dir.glob("*.json"))) == 10
    reader = CatalogReader(summary.catalog_path)
    assert "legacy/old.db" not in {d["rel_path"] for d in reader.databases()}
    assert [f["n_members"] for f in reader.families("strict")] == [3, 3, 2]


def test_a_changed_file_is_profiled_again(fresh_demo_fleet: Path, cache_dir: Path) -> None:
    scan(fresh_demo_fleet, cache_dir, workers=1)
    path = fresh_demo_fleet / RUN1
    con = duckdb.connect(str(path))
    con.execute("INSERT INTO config VALUES ('extra', 'row')")
    con.close()
    summary, events = scan(fresh_demo_fleet, cache_dir, workers=1)
    reprofiled = {e["rel_path"] for e in events if e["event"] == "profiled"}
    assert reprofiled == {RUN1, CORRUPT}
    config = [
        t for t in CatalogReader(summary.catalog_path).tables(RUN1) if t["table_name"] == "config"
    ]
    assert config[0]["row_count"] == 4


def test_a_wal_that_appears_or_disappears_triggers_a_new_profile(
    fresh_demo_fleet: Path, cache_dir: Path
) -> None:
    scan(fresh_demo_fleet, cache_dir, workers=1)
    (fresh_demo_fleet / (RUN1 + ".wal")).unlink()
    summary, events = scan(fresh_demo_fleet, cache_dir, workers=1)
    assert RUN1 in {e["rel_path"] for e in events if e["event"] == "profiled"}
    row = next(d for d in CatalogReader(summary.catalog_path).databases() if d["rel_path"] == RUN1)
    assert row["has_wal"] is False and row["health"] == []


def test_profiles_from_another_schema_version_are_profiled_again(
    fresh_demo_fleet: Path, cache_dir: Path
) -> None:
    scan(fresh_demo_fleet, cache_dir, workers=1)
    store = ProfileStore(cache_dir, fresh_demo_fleet)
    stale = store.load("legacy/old.db")
    assert stale is not None
    store.save(stale.model_copy(update={"profile_schema_version": 0}))
    summary, events = scan(fresh_demo_fleet, cache_dir, workers=1)
    assert "legacy/old.db" in {e["rel_path"] for e in events if e["event"] == "profiled"}
    assert summary.files_reused == 9


def test_truncated_walk(demo_fleet: Path, cache_dir: Path) -> None:
    summary, events = scan(demo_fleet, cache_dir, workers=1, max_files=3)
    assert summary.truncated and summary.files_candidates == 3
    assert events[0]["truncated"] is True
    assert "over_max_files" in {r.reason for r in summary.rejected}
    assert CatalogReader(summary.catalog_path).scan_run()["truncated"] is True


def test_missing_root_raises(tmp_path: Path, cache_dir: Path) -> None:
    with pytest.raises(NotADirectoryError):
        run_scan(tmp_path / "nope", ScanConfig(cache_dir=str(cache_dir)))


def test_works_without_an_event_callback(demo_fleet: Path, cache_dir: Path) -> None:
    summary = run_scan(demo_fleet, ScanConfig(cache_dir=str(cache_dir), workers=1))
    assert summary.files_candidates == 11


def test_workers_default_to_cpu_count_minus_two(
    demo_fleet: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 3)
    summary = run_scan(demo_fleet, ScanConfig(cache_dir=str(cache_dir)))
    assert summary.workers == 1
    assert summary.config.workers == 1 and summary.config.worker_memory_limit_mb >= 256
    monkeypatch.setattr(os, "cpu_count", lambda: 40)
    summary = run_scan(demo_fleet, ScanConfig(cache_dir=str(cache_dir), max_files=1))
    assert summary.workers == 38  # the configured count; the pool never exceeds the files to do


def test_non_ascii_root_with_a_space_and_a_pool(tmp_path: Path) -> None:
    fleet = make_demo_fleet(tmp_path / "données été" / "my fleet")
    summary, _ = scan(fleet, tmp_path / "cache dir é", workers=2)
    assert (summary.files_candidates, summary.files_failed) == (11, 1)
    names = {d["rel_path"] for d in CatalogReader(summary.catalog_path).databases()}
    assert RUN1 in names


@pytest.mark.timeout(60)
def test_a_locked_file_is_reported_not_fatal(
    fresh_demo_fleet: Path, cache_dir: Path, hold_open
) -> None:
    with hold_open(fresh_demo_fleet / "held" / "writer.duckdb"):
        summary, events = scan(fresh_demo_fleet, cache_dir, workers=1)
    assert summary.files_candidates == 12 and summary.files_failed == 2
    locked = [
        e for e in events if e["event"] == "profiled" and e["rel_path"] == "held/writer.duckdb"
    ]
    assert [(e["status"], e["error_type"]) for e in locked] == [("error", "locked")]


# ---------------------------------------------------------------- worker crashes


class FakeExecutor:
    """Stands in for ProcessPoolExecutor: runs a few jobs, then the 'pool' breaks."""

    ok_jobs = 4
    break_on_submit = False

    def __init__(self, max_workers=None, mp_context=None):
        self.submitted = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

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
    demo_fleet: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coordinator, "ProcessPoolExecutor", FakeExecutor)
    summary, events = scan(demo_fleet, cache_dir, workers=4)
    profiled = [e for e in events if e["event"] == "profiled"]
    assert len(profiled) == 11
    crashed = [e for e in profiled if e["error_type"] == "worker_crashed"]
    assert len(crashed) == 7  # 11 files, the first 4 finished before the pool broke
    reader = CatalogReader(summary.catalog_path)
    rows = {d["rel_path"]: d for d in reader.databases()}
    assert len(rows) == 11  # the catalog is still built, crashed files included
    assert {rows[e["rel_path"]]["error_type"] for e in crashed} == {"worker_crashed"}
    assert all(rows[e["rel_path"]]["status"] == "error" for e in crashed)
    assert all("worker process died" in rows[e["rel_path"]]["error_message"] for e in crashed)
    assert summary.files_failed >= 7
    # a rescan retries them (errors are never reused)
    monkeypatch.undo()
    again, _ = scan(demo_fleet, cache_dir, workers=1)
    assert again.files_profiled >= 7 and again.files_failed == 1


def test_a_pool_that_breaks_during_submit(
    demo_fleet: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BreakingExecutor(FakeExecutor):
        break_on_submit = True
        ok_jobs = 3

    monkeypatch.setattr(coordinator, "ProcessPoolExecutor", BreakingExecutor)
    summary, events = scan(demo_fleet, cache_dir, workers=4)
    profiled = [e for e in events if e["event"] == "profiled"]
    assert len(profiled) == 11
    assert sum(e["error_type"] == "worker_crashed" for e in profiled) == 8
    assert summary.files_candidates == 11
