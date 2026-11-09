"""Per-process DuckDB temp directories under the cache (review V-01, P-05; decision D-034)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from duckflock_core import profiler, tempdir
from duckflock_core.config import ScanConfig
from duckflock_core.coordinator import run_scan
from duckflock_core.query import QueryConfig, open_readonly
from duckflock_core.tempdir import (
    ensure_file_temp_dir,
    ensure_flock_temp_dir,
    ensure_temp_dir,
    file_temp_dir,
    process_temp_dir,
    remove_temp_dir,
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


def test_file_temp_dir_is_one_leaf_per_file_however_the_path_is_spelled(tmp_path: Path) -> None:
    """W-01: the same file always maps to the same leaf (D-021), different files never share one."""
    root = tmp_path / "tmp" / str(os.getpid())
    db = tmp_path / "data" / "q é.duckdb"
    leaf = file_temp_dir(root, db)
    assert leaf.parent == root and re.fullmatch(r"[0-9a-f]{16}", leaf.name)
    assert file_temp_dir(root, str(db)) == leaf  # str or Path
    assert file_temp_dir(root, tmp_path / "data" / ".." / "data" / "q é.duckdb") == leaf
    assert file_temp_dir(root, tmp_path / "data" / "other.duckdb") != leaf
    if os.name == "nt":  # Windows paths are case-insensitive and accept both separators
        assert file_temp_dir(root, str(db).upper()) == leaf
        assert file_temp_dir(root, str(db).replace("\\", "/")) == leaf


def test_ensure_file_temp_dir_registers_the_process_folder_only(tmp_path: Path) -> None:
    root = tmp_path / "tmp" / str(os.getpid())
    leaf = ensure_file_temp_dir(root, tmp_path / "a.duckdb")
    assert Path(leaf) == file_temp_dir(root, tmp_path / "a.duckdb") and Path(leaf).is_dir()
    assert ensure_file_temp_dir(root, tmp_path / "a.duckdb") == leaf  # idempotent
    assert str(root) in tempdir._registered and leaf not in tempdir._registered


def test_the_first_connection_of_a_process_sweeps_stale_folders(tmp_path: Path) -> None:
    """P-08: a killed process leaves its folder; the next process's first connection removes it."""
    stale = tmp_path / "tmp" / str(dead_pid())
    stale.mkdir(parents=True)
    (stale / "duckdb_temp_storage_DEFAULT-0.tmp").write_bytes(b"x")
    foreign = tmp_path / "tmp" / "not-a-pid"
    foreign.mkdir()
    ensure_file_temp_dir(process_temp_dir(tmp_path), tmp_path / "a.duckdb")
    assert not stale.exists() and foreign.exists()
    # only once per process and tmp folder: a folder that shows up later stays until a scan
    late = tmp_path / "tmp" / str(dead_pid())
    late.mkdir()
    ensure_file_temp_dir(process_temp_dir(tmp_path), tmp_path / "b.duckdb")
    assert late.exists()


def test_a_folder_outside_the_standard_layout_is_never_swept(tmp_path: Path) -> None:
    other = tmp_path / "x" / "tmp" / str(dead_pid())
    other.mkdir(parents=True)
    ensure_file_temp_dir(tmp_path / "custom-root", tmp_path / "a.duckdb")
    assert other.exists()


def test_flock_temp_dirs_are_distinct_and_removable(tmp_path: Path) -> None:
    root = process_temp_dir(tmp_path)
    first, second = ensure_flock_temp_dir(root), ensure_flock_temp_dir(root)
    assert first != second and Path(first).parent == root
    assert re.fullmatch(r"flock-[0-9a-f]{8}", Path(first).name)
    (Path(first) / "spill.tmp").write_bytes(b"x")
    remove_temp_dir(first)
    assert not Path(first).exists() and Path(second).is_dir()


def test_ensure_temp_dir_creates_the_folder(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    assert ensure_temp_dir(target) == str(target)
    assert target.is_dir()
    assert ensure_temp_dir(target) == str(target)  # idempotent


def test_the_folder_is_removed_when_the_process_exits(tmp_path: Path) -> None:
    target = tmp_path / "tmp" / "child"
    code = (
        "import sys, pathlib;"
        "from duckflock_core.tempdir import ensure_temp_dir;"
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
    fresh_demo_flock: Path, cache_dir: Path
) -> None:
    stale = cache_dir / "tmp" / str(dead_pid())
    stale.mkdir(parents=True)
    (stale / "duckdb_temp_storage_DEFAULT-0.tmp").write_bytes(b"x")
    run_scan(fresh_demo_flock, ScanConfig(cache_dir=str(cache_dir), workers=2))
    left = sorted(p.name for p in (cache_dir / "tmp").iterdir())
    assert not stale.exists()
    # the two pool workers created and removed their own folders; only this process may remain
    assert left in ([], [str(os.getpid())]), left


def test_the_profiler_connects_with_the_sandbox_and_a_cache_temp_directory(
    demo_flock: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
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
        str(demo_flock / RUN1), RUN1, config.model_dump(mode="json")
    )
    assert result["status"] == "ok"
    (opened,) = seen
    assert opened == {
        "threads": 1,
        "memory_limit": "300MB",
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "enable_external_access": False,
        "temp_directory": str(file_temp_dir(process_temp_dir(cache_dir), demo_flock / RUN1)),
        "lock_configuration": True,
    }
    keys = list(opened)
    assert keys.index("temp_directory") > keys.index("enable_external_access")


def test_a_scan_and_a_query_connection_to_one_file_can_overlap_in_a_process(
    demo_flock: Path, cache_dir: Path
) -> None:
    """P-05: with the same threads and memory the two configs are identical."""
    config = ScanConfig(cache_dir=str(cache_dir), worker_threads=2, worker_memory_limit_mb=700)
    query_config = QueryConfig(
        threads=2, memory_limit_mb=700, temp_dir=str(process_temp_dir(cache_dir))
    )
    with open_readonly(demo_flock / RUN1, query_config) as con:
        assert con.execute("select count(*) from events").fetchone() == (1000,)
        result = profiler.profile_database(
            str(demo_flock / RUN1), RUN1, config.model_dump(mode="json")
        )
        assert result["status"] == "ok"


def test_two_instances_spilling_at_once_in_one_process_survive(tmp_path: Path) -> None:
    """W-01: two threads, two files, ``memory_limit_mb=64``, a 12 M-row sort each.

    With one shared temp folder (0.3.0) both instances write ``duckdb_temp_storage_DEFAULT-0.tmp``
    and the process dies. It runs in a child process so that a regression is a failed test, not a
    dead test run.
    """
    script = Path(__file__).with_name("_spill_threads.py")
    done = subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=280,
    )
    detail = (done.returncode, done.stdout, done.stderr)
    assert done.returncode == 0 and done.stdout.strip() == "ok", detail
