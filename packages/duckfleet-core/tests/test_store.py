from __future__ import annotations

import json
import os
from pathlib import Path

from duckfleet_core.config import ScanConfig
from duckfleet_core.models import PROFILE_SCHEMA_VERSION, DatabaseProfile, ScanSummary
from duckfleet_core.store import (
    MAX_RECENT_ROOTS,
    ProfileStore,
    file_key,
    recent_roots,
    remember_root,
    root_key,
)


def make_profile(rel_path: str, **overrides) -> DatabaseProfile:
    fields = {
        "rel_path": rel_path,
        "abs_path": "/data/" + rel_path,
        "file_name": rel_path.rsplit("/", 1)[-1],
        "size_bytes": 10,
        "mtime_ns": 20,
        "has_wal": False,
        "duckdb_version": "1.5.6",
        "profiled_at": "2026-01-01T00:00:00+00:00",
        "profile_seconds": 0.1,
        "status": "ok",
    }
    fields.update(overrides)
    return DatabaseProfile(**fields)


def make_summary(root: Path, catalog: Path) -> ScanSummary:
    return ScanSummary(
        run_id="r1",
        root=str(root),
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:05+00:00",
        duckfleet_version="0.0",
        duckdb_version="1.5.6",
        workers=2,
        files_walked=3,
        files_candidates=2,
        files_rejected=1,
        files_profiled=2,
        files_reused=0,
        files_failed=0,
        truncated=False,
        catalog_path=str(catalog),
        rejected=[{"rel_path": "x.db", "reason": "not_duckdb_header"}],
        config=ScanConfig(),
    )


def test_keys() -> None:
    assert len(file_key("a/b.duckdb")) == 24
    assert file_key("a/b.duckdb") == file_key("a/b.duckdb")
    assert file_key("a/b.duckdb") != file_key("a/c.duckdb")
    assert file_key("données/é.duckdb")  # non-ascii paths hash fine


def test_root_key_is_stable_for_equivalent_spellings(tmp_path: Path) -> None:
    key = root_key(tmp_path)
    assert len(key) == 16
    assert root_key(tmp_path / ".") == key
    assert root_key(tmp_path / "sub" / "..") == key
    if os.name == "nt":  # Windows paths are case-insensitive
        assert root_key(Path(str(tmp_path).upper())) == key
        assert root_key(Path(str(tmp_path).replace("\\", "/"))) == key
    assert root_key(tmp_path / "other") != key


def test_store_layout_and_lazy_creation(tmp_path: Path) -> None:
    cache, root = tmp_path / "cache", tmp_path / "root"
    root.mkdir()
    store = ProfileStore(cache, root)
    assert not cache.exists()  # constructing creates nothing
    assert store.root_dir == cache / "roots" / root_key(root)
    assert store.profiles_dir == store.root_dir / "profiles"
    assert store.catalog_path == store.root_dir / "catalog.duckdb"
    assert store.tmp_catalog_path == store.root_dir / "catalog.duckdb.tmp"
    store.save(make_profile("a/x.duckdb"))
    assert (store.root_dir / "root.txt").read_text(encoding="utf-8") == str(root.resolve())
    assert (store.profiles_dir / f"{file_key('a/x.duckdb')}.json").is_file()


def test_save_load_roundtrip(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "cache", tmp_path)
    profile = make_profile("é ü/x.duckdb", tags={"run": "1"}, health=["wal_present"])
    store.save(profile)
    assert store.load("é ü/x.duckdb") == profile
    assert store.load("missing.duckdb") is None
    store.save(profile.model_copy(update={"size_bytes": 99}))  # overwrite
    assert store.load("é ü/x.duckdb").size_bytes == 99
    assert not list(store.profiles_dir.glob("*.tmp"))  # no temp files left behind


def test_unreadable_and_foreign_profiles_are_ignored(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "cache", tmp_path)
    store.save(make_profile("good.duckdb"))
    (store.profiles_dir / f"{file_key('broken.duckdb')}.json").write_text("{not json")
    (store.profiles_dir / f"{file_key('shape.duckdb')}.json").write_text('{"rel_path": 1}')
    assert store.load("broken.duckdb") is None
    assert store.load("shape.duckdb") is None
    # a file stored under another path's key is not returned for the wrong path
    other = make_profile("other.duckdb")
    (store.profiles_dir / f"{file_key('wrong.duckdb')}.json").write_text(other.model_dump_json())
    assert store.load("wrong.duckdb") is None
    assert [p.rel_path for p in store.load_all()] == ["good.duckdb", "other.duckdb"]


def test_load_all_sorted_and_current_version_only(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "cache", tmp_path)
    assert store.load_all() == []
    for rel in ("b/2.duckdb", "a/1.duckdb", "c/3.duckdb"):
        store.save(make_profile(rel))
    store.save(make_profile("old.duckdb", profile_schema_version=PROFILE_SCHEMA_VERSION - 1))
    assert [p.rel_path for p in store.load_all()] == ["a/1.duckdb", "b/2.duckdb", "c/3.duckdb"]


def test_prune_removes_profiles_of_missing_files(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "cache", tmp_path)
    assert store.prune({"x"}) == 0  # nothing there yet
    for rel in ("a.duckdb", "b.duckdb", "c.duckdb"):
        store.save(make_profile(rel))
    (store.profiles_dir / "leftover.json.123.tmp").write_text("x")
    assert store.prune({"a.duckdb", "c.duckdb"}) == 1
    assert [p.rel_path for p in store.load_all()] == ["a.duckdb", "c.duckdb"]
    assert not list(store.profiles_dir.glob("*.tmp"))
    assert store.prune(set()) == 2
    assert store.load_all() == []


def test_summary_roundtrip(tmp_path: Path) -> None:
    store = ProfileStore(tmp_path / "cache", tmp_path)
    assert store.load_summary() is None
    summary = make_summary(tmp_path, store.catalog_path)
    store.save_summary(summary)
    assert store.load_summary() == summary
    store.summary_path.write_text("garbage")
    assert store.load_summary() is None


def test_recent_roots_newest_first_deduplicated_and_capped(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    assert recent_roots(cache) == []
    folders = []
    for index in range(MAX_RECENT_ROOTS + 3):
        folder = tmp_path / f"root{index}"
        folder.mkdir()
        folders.append(folder)
        remember_root(cache, folder)
    entries = recent_roots(cache)
    assert len(entries) == MAX_RECENT_ROOTS == 20
    assert entries[0]["root"] == str(folders[-1].resolve())  # newest first
    assert str(folders[0].resolve()) not in {e["root"] for e in entries}  # oldest dropped
    remember_root(cache, folders[-5])  # moves to the front, no duplicate
    entries = recent_roots(cache)
    assert entries[0]["root"] == str(folders[-5].resolve())
    assert [e["root"] for e in entries].count(str(folders[-5].resolve())) == 1
    assert len(entries) == 20
    assert entries[0]["last_scanned_at"].endswith("+00:00")


def test_recent_roots_survives_garbage(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "recent_roots.json").write_text("not json")
    assert recent_roots(cache) == []
    (cache / "recent_roots.json").write_text(json.dumps({"not": "a list"}))
    assert recent_roots(cache) == []
    remember_root(cache, tmp_path)
    assert len(recent_roots(cache)) == 1
