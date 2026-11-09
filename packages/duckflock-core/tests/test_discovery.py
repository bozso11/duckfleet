from __future__ import annotations

import os
from pathlib import Path

import pytest

from duckflock_core import discovery
from duckflock_core.config import ScanConfig
from duckflock_core.discovery import discover, read_header


def fake_duckdb(path: Path, size: int = 64, version: int = 64) -> Path:
    """A file with a valid DuckDB header (enough for discovery, not for DuckDB)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(8) + b"DUCK" + version.to_bytes(8, "little") + bytes(size))
    return path


def rels(items) -> list[str]:
    return [item.rel_path for item in items]


def test_read_header_real_duckdb_file(demo_flock: Path) -> None:
    is_duckdb, version = read_header(demo_flock / "exp_01" / "run_01" / "metrics.duckdb")
    assert is_duckdb is True
    assert isinstance(version, int) and version > 0


def test_read_header_sqlite_empty_and_short(tmp_path: Path) -> None:
    sqlite = tmp_path / "s.db"
    sqlite.write_bytes(b"SQLite format 3\x00" + bytes(100))
    assert read_header(sqlite) == (False, None)
    empty = tmp_path / "e.duckdb"
    empty.write_bytes(b"")
    assert read_header(empty) == (False, None)
    short = tmp_path / "short.duckdb"
    short.write_bytes(bytes(8) + b"DUCK" + b"\x01")  # magic but fewer than 20 bytes
    assert read_header(short) == (True, None)


def test_read_header_storage_version_little_endian(tmp_path: Path) -> None:
    path = fake_duckdb(tmp_path / "v.duckdb", version=0x0102)
    assert read_header(path) == (True, 0x0102)


def test_read_header_ignores_a_garbage_storage_version(tmp_path: Path) -> None:
    """R-01: a version above 2**63 - 1 is no version (the catalog column used to overflow)."""
    assert read_header(fake_duckdb(tmp_path / "a.duckdb", version=2**63 - 1)) == (True, 2**63 - 1)
    assert read_header(fake_duckdb(tmp_path / "b.duckdb", version=2**63)) == (True, None)
    assert read_header(fake_duckdb(tmp_path / "c.duckdb", version=2**64 - 1)) == (True, None)


def test_read_header_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        read_header(tmp_path / "nope.duckdb")


def test_demo_flock_discovery(demo_flock: Path) -> None:
    result = discover(demo_flock, ScanConfig())
    assert len(result.candidates) == 13
    assert not result.truncated
    assert result.walked_files == 16  # 13 candidates + 2 rejected + 1 wal
    assert {(r.rel_path, r.reason) for r in result.rejected} == {
        ("legacy/sqlite_lookalike.db", "not_duckdb_header"),
        ("broken/truncated.duckdb", "not_duckdb_header"),
    }
    by_rel = {c.rel_path: c for c in result.candidates}
    assert by_rel["exp_01/run_01/metrics.duckdb"].has_wal
    assert not by_rel["exp_01/run_02/metrics.duckdb"].has_wal
    assert by_rel["broken/corrupt.duckdb"].size_bytes == 4096 + 8192
    assert by_rel["broken/highbit.duckdb"].storage_version is None  # R-01
    encrypted = by_rel["secure/encrypted.duckdb"]
    assert isinstance(encrypted.storage_version, int) and encrypted.storage_version > 0
    assert not encrypted.has_wal  # the generator leaves no .wal behind
    assert all("\\" not in c.rel_path for c in result.candidates)
    assert all(c.abs_path.is_absolute() for c in result.candidates)
    assert all(c.mtime_ns > 0 for c in result.candidates)


def test_the_cache_directory_inside_the_root_is_pruned(tmp_path: Path) -> None:
    """R-06, also through a differently spelled path to the same folder."""
    root = tmp_path / "root"
    fake_duckdb(root / "a.duckdb")
    cache = root / "cache" / "roots" / "x"
    fake_duckdb(cache / "catalog.duckdb")
    plain = discover(root, ScanConfig())
    assert rels(plain.candidates) == ["a.duckdb", "cache/roots/x/catalog.duckdb"]
    pruned = discover(root, ScanConfig(cache_dir=str(root / "cache")))
    assert rels(pruned.candidates) == ["a.duckdb"]
    roundabout = str(root / "other" / ".." / "CACHE" if os.name == "nt" else root / "cache")
    (root / "other").mkdir()
    pruned = discover(root, ScanConfig(cache_dir=roundabout))
    assert rels(pruned.candidates) == ["a.duckdb"]
    elsewhere = discover(root, ScanConfig(cache_dir=str(tmp_path / "elsewhere")))
    assert len(elsewhere.candidates) == 2  # a cache outside the root changes nothing


def test_candidates_sorted_largest_first_with_stable_tiebreak(demo_flock: Path) -> None:
    first = discover(demo_flock, ScanConfig()).candidates
    second = discover(demo_flock, ScanConfig()).candidates
    assert rels(first) == rels(second)
    keys = [(-c.size_bytes, c.rel_path) for c in first]
    assert keys == sorted(keys)


def test_nested_dirs_and_excluded_dirs(tmp_path: Path) -> None:
    fake_duckdb(tmp_path / "a" / "b" / "c" / "deep.duckdb")
    fake_duckdb(tmp_path / "top.duckdb")
    fake_duckdb(tmp_path / ".git" / "hidden.duckdb")
    fake_duckdb(tmp_path / "x" / "node_modules" / "dep.duckdb")
    fake_duckdb(tmp_path / ".duckflock" / "own.duckdb")
    fake_duckdb(tmp_path / "x" / "__pycache__" / "p.duckdb")
    result = discover(tmp_path, ScanConfig())
    assert sorted(rels(result.candidates)) == ["a/b/c/deep.duckdb", "top.duckdb"]


def test_exclude_dirs_configurable(tmp_path: Path) -> None:
    fake_duckdb(tmp_path / "keep" / "a.duckdb")
    fake_duckdb(tmp_path / "skip" / "b.duckdb")
    result = discover(tmp_path, ScanConfig(exclude_dirs=["skip"]))
    assert rels(result.candidates) == ["keep/a.duckdb"]


def test_extension_match_is_case_insensitive(tmp_path: Path) -> None:
    fake_duckdb(tmp_path / "UPPER.DUCKDB")
    fake_duckdb(tmp_path / "mixed.Db")
    assert sorted(rels(discover(tmp_path, ScanConfig()).candidates)) == ["UPPER.DUCKDB", "mixed.Db"]


def test_empty_duckdb_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "empty.duckdb").write_bytes(b"")
    result = discover(tmp_path, ScanConfig())
    assert result.candidates == []
    assert [(r.rel_path, r.reason) for r in result.rejected] == [
        ("empty.duckdb", "not_duckdb_header")
    ]


def test_wal_and_tmp_files_are_never_candidates(tmp_path: Path) -> None:
    db = fake_duckdb(tmp_path / "x.duckdb")
    fake_duckdb(tmp_path / "x.duckdb.wal")
    fake_duckdb(tmp_path / "catalog.duckdb.tmp")
    for sniff in (False, True):
        result = discover(tmp_path, ScanConfig(sniff_all_files=sniff))
        assert rels(result.candidates) == ["x.duckdb"]
        assert result.candidates[0].has_wal
        assert result.candidates[0].abs_path == db.resolve()
        assert result.rejected == []


def test_max_files_truncates_and_records_the_overflow(tmp_path: Path) -> None:
    for index in range(5):
        fake_duckdb(tmp_path / f"f{index}.duckdb", size=index)
    result = discover(tmp_path, ScanConfig(max_files=2))
    assert len(result.candidates) == 2
    assert result.truncated
    assert [r.reason for r in result.rejected] == ["over_max_files"]
    # the walk stopped: 2 candidates + the file that tripped the limit
    assert result.walked_files == 3


def test_max_files_equal_to_count_is_not_truncated(tmp_path: Path) -> None:
    for index in range(2):
        fake_duckdb(tmp_path / f"f{index}.duckdb")
    result = discover(tmp_path, ScanConfig(max_files=2))
    assert len(result.candidates) == 2
    assert not result.truncated
    assert result.rejected == []


def test_non_duckdb_overflow_file_is_not_truncation(tmp_path: Path) -> None:
    fake_duckdb(tmp_path / "a.duckdb")
    (tmp_path / "b.db").write_bytes(b"SQLite format 3\x00" + bytes(64))
    result = discover(tmp_path, ScanConfig(max_files=1))
    assert not result.truncated
    assert [r.reason for r in result.rejected] == ["not_duckdb_header"]


def test_path_with_space_and_non_ascii(tmp_path: Path) -> None:
    fake_duckdb(tmp_path / "données été" / "run 1" / "ünï cödé.duckdb")
    result = discover(tmp_path, ScanConfig())
    assert rels(result.candidates) == ["données été/run 1/ünï cödé.duckdb"]
    assert result.candidates[0].abs_path.exists()


def test_sniff_all_files_picks_up_other_extensions(tmp_path: Path) -> None:
    fake_duckdb(tmp_path / "data.bin")
    (tmp_path / "notes.txt").write_text("hello")
    assert discover(tmp_path, ScanConfig()).candidates == []
    sniffed = discover(tmp_path, ScanConfig(sniff_all_files=True))
    assert rels(sniffed.candidates) == ["data.bin"]
    assert [(r.rel_path, r.reason) for r in sniffed.rejected] == [
        ("notes.txt", "not_duckdb_header")
    ]


def test_custom_extensions_are_normalised(tmp_path: Path) -> None:
    fake_duckdb(tmp_path / "x.dat")
    assert rels(discover(tmp_path, ScanConfig(extensions=["DAT"])).candidates) == ["x.dat"]


def test_unreadable_file_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_duckdb(tmp_path / "locked.duckdb")

    def boom(path: Path) -> tuple[bool, int | None]:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(discovery, "read_header", boom)
    result = discover(tmp_path, ScanConfig())
    assert result.candidates == []
    assert [(r.rel_path, r.reason) for r in result.rejected] == [("locked.duckdb", "unreadable")]


def test_root_must_be_a_directory(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        discover(tmp_path / "missing", ScanConfig())
    file = fake_duckdb(tmp_path / "file.duckdb")
    with pytest.raises(NotADirectoryError):
        discover(file, ScanConfig())


def test_symlinked_directories_not_followed_by_default(tmp_path: Path) -> None:
    real = tmp_path / "real"
    fake_duckdb(real / "a.duckdb")
    link = tmp_path / "link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available here")
    assert rels(discover(tmp_path, ScanConfig()).candidates) == ["real/a.duckdb"]
    followed = discover(tmp_path, ScanConfig(follow_symlinks=True))
    # same target seen through two names: the loop guard keeps each real dir once
    assert len(followed.candidates) == 1


@pytest.mark.timeout(60)
def test_file_held_by_a_writer_stays_a_candidate(tmp_path: Path, hold_open) -> None:
    """Windows cannot even read a file a writer holds; it must reach the worker as "locked"."""
    db = tmp_path / "held.duckdb"
    with hold_open(db):
        result = discover(tmp_path, ScanConfig())
    assert rels(result.candidates) == ["held.duckdb"]
    assert result.rejected == []
    assert result.candidates[0].size_bytes > 0


def test_permission_denied_is_unreadable_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_duckdb(tmp_path / "denied.duckdb")

    def denied(path: Path) -> tuple[bool, int | None]:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(discovery, "read_header", denied)
    monkeypatch.setattr(discovery.sys, "platform", "linux")
    result = discover(tmp_path, ScanConfig())
    assert result.candidates == []
    assert [r.reason for r in result.rejected] == ["unreadable"]
    monkeypatch.setattr(discovery.sys, "platform", "win32")
    result = discover(tmp_path, ScanConfig())
    assert rels(result.candidates) == ["denied.duckdb"]
