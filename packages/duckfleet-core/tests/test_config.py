from __future__ import annotations

import os
from pathlib import Path

import platformdirs
import pytest

from duckfleet_core.config import MAX_POOL_WORKERS, ScanConfig, default_cache_dir


def test_defaults_match_the_plan() -> None:
    config = ScanConfig()
    assert config.extensions == [".duckdb", ".db", ".ddb"]
    assert config.max_files == 1000
    assert config.worker_threads == 1
    assert config.stats_row_threshold == 20_000_000
    assert config.stats_sample_rows == 1_000_000
    assert not config.sniff_all_files and not config.full and not config.follow_symlinks
    assert ".git" in config.exclude_dirs and ".duckfleet" in config.exclude_dirs


def test_workers_default_is_cpu_count_minus_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    assert ScanConfig().resolved_workers() == 6
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    assert ScanConfig().resolved_workers() == 1
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert ScanConfig().resolved_workers() == 1
    assert ScanConfig(workers=3).resolved_workers() == 3


def test_workers_are_capped_at_the_pool_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-04: 64+ CPUs gave a default above the Windows pool limit of 61."""
    monkeypatch.setattr(os, "cpu_count", lambda: 72)
    assert ScanConfig().resolved_workers() == MAX_POOL_WORKERS == 61
    assert ScanConfig(workers=100).resolved_workers() == 61
    assert ScanConfig(workers=61).resolved_workers() == 61
    monkeypatch.setattr(os, "cpu_count", lambda: 63)
    assert ScanConfig().resolved_workers() == 61


def test_tag_patterns_are_validated() -> None:
    """R-03: a bad pattern fails when the config is built, with the pattern in the message."""
    assert ScanConfig(tag_patterns=[r"(?P<key>[a-z]+)_(?P<value>\d+)"]).tag_patterns
    assert ScanConfig(tag_patterns=[]).tag_patterns == []
    with pytest.raises(ValueError, match=r"invalid tag pattern '\('"):
        ScanConfig(tag_patterns=["("])
    with pytest.raises(ValueError, match="named groups"):
        ScanConfig(tag_patterns=[r"(\w+)_(\d+)"])
    with pytest.raises(ValueError, match="named groups"):
        ScanConfig(tag_patterns=[r"(?P<key>\w+)"])


def test_memory_limit_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMemory:
        total = 16 * 1024**3

    monkeypatch.setattr("duckfleet_core.config.psutil.virtual_memory", lambda: FakeMemory)
    assert ScanConfig(workers=2).resolved_memory_limit_mb() == 2048  # 4096 capped
    assert ScanConfig(workers=8).resolved_memory_limit_mb() == 1024  # 16384 / 2 / 8
    assert ScanConfig(workers=60).resolved_memory_limit_mb() == 256  # floor
    assert ScanConfig(workers=2, worker_memory_limit_mb=700).resolved_memory_limit_mb() == 700


def test_cache_dir_resolution(tmp_path: Path) -> None:
    assert ScanConfig(cache_dir=str(tmp_path)).resolved_cache_dir() == tmp_path
    assert ScanConfig().resolved_cache_dir() == default_cache_dir()
    assert default_cache_dir() == Path(platformdirs.user_cache_dir("duckfleet", appauthor=False))


def test_extensions_are_normalised() -> None:
    assert ScanConfig(extensions=["DB", ".Duckdb", " ", "ddb "]).extensions == [
        ".db",
        ".duckdb",
        ".ddb",
    ]


def test_model_dump_round_trip_is_plain_json() -> None:
    config = ScanConfig(workers=2, max_files=5)
    dumped = config.model_dump(mode="json")
    assert isinstance(dumped, dict)
    assert ScanConfig.model_validate(dumped) == config
