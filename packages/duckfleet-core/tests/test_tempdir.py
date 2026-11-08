"""Per-process DuckDB temp directories under the cache (review V-01, P-05; decision D-034)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from duckfleet_core import profiler
from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.query import QueryConfig, open_readonly
from duckfleet_core.tempdir import (
    ensure_temp_dir,
    process_temp_dir,
    sweep_stale_temp_dirs,
    temp_root,
)

RUN1 = "exp_01/run_01/metrics.duckdb"


def dead_pid() -> int:
    done = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(done.stdout)


def test_process_temp_dir_is_under_the_cache(tmp_path: Path) -> None:
    assert temp_root(tmp_path) == tmp_path / "tmp"
    assert process_temp_dir(tmp_path) == tmp_path / "tmp" / str(os.getpid())


def test_ensure_temp_dir_creates_the_folder(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    assert ensure_temp_dir(target) == str(target)
    assert target.is_dir()
    assert ensure_temp_dir(target) == str(target)  # idempotent


def test_the_folder_is_removed_when_the_process_exits(tmp_path: Path) -> None:
    target = tmp_path / "tmp" / "child"
    code = (
        "import sys, pathlib;"
        "from duckfleet_core.tempdir import ensure_temp_dir;"
        "p = pathlib.Path(ensure_temp_dir(sys.argv[1]));"
        "(p / 'spill.tmp').write_bytes(b'x')"
    )
    subprocess.run([sys.executable, "-c", code, str(target)], check=True, timeout=60)
    assert not target.exists()


def test_sweep_removes_only_folders_of_dead_processes(tmp_path: Path) -> None:
    root = tmp_path / "tmp"
    dead = dead_pid()
    names = [str(dead), str(os.getpid()), str(os.getppid()), "not-a-pid"]
    for name in names:
        (root / name).mkdir(parents=True)
        (root / name / "leftover.tmp").write_bytes(b"x")
    (root / "9999999").write_bytes(b"a file, not a folder")
    removed = sweep_stale_temp_dirs(tmp_path)
    assert removed == [str(dead)]
    assert sorted(p.name for p in root.iterdir()) == sorted([*names[1:], "9999999"])
    assert sweep_stale_temp_dirs(tmp_path / "no-such-cache") == []


def test_run_scan_sweeps_stale_folders_and_workers_clean_up_after_themselves(
    fresh_demo_fleet: Path, cache_dir: Path
) -> None:
    stale = cache_dir / "tmp" / str(dead_pid())
    stale.mkdir(parents=True)
    (stale / "duckdb_temp_storage_DEFAULT-0.tmp").write_bytes(b"x")
    run_scan(fresh_demo_fleet, ScanConfig(cache_dir=str(cache_dir), workers=2))
    left = sorted(p.name for p in (cache_dir / "tmp").iterdir())
    assert not stale.exists()
    # the two pool workers created and removed their own folders; only this process may remain
    assert left in ([], [str(os.getpid())]), left


def test_the_profiler_connects_with_the_sandbox_and_a_cache_temp_directory(
    demo_fleet: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P-05 and V-01: the profiler's config is the query layer's, except threads and memory."""
    seen: list[dict] = []
    real_connect = duckdb.connect

    def spy(*args, **kwargs):
        seen.append(kwargs.get("config", {}))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(profiler.duckdb, "connect", spy)
    config = ScanConfig(cache_dir=str(cache_dir), worker_memory_limit_mb=300)
    result = profiler.profile_database(
        str(demo_fleet / RUN1), RUN1, config.model_dump(mode="json")
    )
    assert result["status"] == "ok"
    (opened,) = seen
    assert opened == {
        "threads": 1,
        "memory_limit": "300MB",
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "enable_external_access": False,
        "temp_directory": str(process_temp_dir(cache_dir)),
        "lock_configuration": True,
    }
    keys = list(opened)
    assert keys.index("temp_directory") > keys.index("enable_external_access")


def test_a_scan_and_a_query_connection_to_one_file_can_overlap_in_a_process(
    demo_fleet: Path, cache_dir: Path
) -> None:
    """P-05: with the same threads and memory the two configs are identical."""
    config = ScanConfig(cache_dir=str(cache_dir), worker_threads=2, worker_memory_limit_mb=700)
    query_config = QueryConfig(
        threads=2, memory_limit_mb=700, temp_dir=str(process_temp_dir(cache_dir))
    )
    with open_readonly(demo_fleet / RUN1, query_config) as con:
        assert con.execute("select count(*) from events").fetchone() == (1000,)
        result = profiler.profile_database(
            str(demo_fleet / RUN1), RUN1, config.model_dump(mode="json")
        )
        assert result["status"] == "ok"
