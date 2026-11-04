"""The subprocess that runs a scan for the UI (real processes, no mocks)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from duckfleet_core.demo import make_demo_fleet
from duckfleet_ui.scan_runner import ScanOptions, build_command, run_scan_subprocess


def test_build_command() -> None:
    command = build_command(
        "/data/x y",
        ScanOptions(workers=3, max_files=50, full=True, sniff_all=True, cache_dir="/c"),
    )
    assert command[:5] == [sys.executable, "-m", "duckfleet_core", "scan", "/data/x y"]
    assert command[5:7] == ["--progress", "json"]
    assert ["--workers", "3", "--max-files", "50", "--cache-dir", "/c"] == [
        command[command.index("--workers")],
        command[command.index("--workers") + 1],
        command[command.index("--max-files")],
        command[command.index("--max-files") + 1],
        command[command.index("--cache-dir")],
        command[command.index("--cache-dir") + 1],
    ]
    assert "--full" in command and "--sniff-all" in command
    plain = build_command("/r", ScanOptions())
    assert plain == [sys.executable, "-m", "duckfleet_core", "scan", "/r", "--progress", "json"]


@pytest.mark.timeout(180)
def test_scan_through_the_subprocess(tmp_path: Path) -> None:
    fleet = make_demo_fleet(tmp_path / "données été" / "my fleet")
    events: list[dict] = []
    result = run_scan_subprocess(
        str(fleet), ScanOptions(workers=2, cache_dir=str(tmp_path / "cache dir")), events.append
    )
    assert result.ok and result.returncode == 0 and result.fatal is None
    assert result.stray_stdout == []
    kinds = [e["event"] for e in events]
    assert kinds[:2] == ["discovered", "planned"] and kinds[-2:] == ["catalog", "finished"]
    assert kinds.count("profiled") == 11
    assert result.summary is not None
    assert result.summary["workers"] == 2 and result.summary["files_candidates"] == 11
    assert Path(result.summary["catalog_path"]).is_file()

    again: list[dict] = []
    second = run_scan_subprocess(
        str(fleet), ScanOptions(workers=1, cache_dir=str(tmp_path / "cache dir")), again.append
    )
    assert second.ok and second.summary is not None
    assert (second.summary["files_reused"], second.summary["files_profiled"]) == (10, 1)


def test_the_default_worker_count_is_visible_in_the_summary(tmp_path: Path) -> None:
    from duckfleet_core.config import ScanConfig

    fleet = make_demo_fleet(tmp_path / "fleet")
    result = run_scan_subprocess(
        str(fleet), ScanOptions(max_files=2, cache_dir=str(tmp_path / "c")), lambda event: None
    )
    assert result.ok and result.summary is not None
    assert result.summary["workers"] == ScanConfig().resolved_workers()  # max(1, cpu_count - 2)


def test_fatal_error_is_reported(tmp_path: Path) -> None:
    events: list[dict] = []
    result = run_scan_subprocess(
        str(tmp_path / "does not exist"), ScanOptions(cache_dir=str(tmp_path / "c")), events.append
    )
    assert not result.ok and result.returncode == 2
    assert result.fatal and "does not exist" in result.fatal
    assert [e["event"] for e in events] == ["fatal"]
    assert "error:" in result.stderr
    assert result.summary is None
