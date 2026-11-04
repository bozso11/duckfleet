"""Profile store: one JSON file per scanned database in the user cache.

Layout (decision D-003, D-009)::

    <cache_dir>/roots/<root_key>/
        root.txt              absolute root path, for humans
        profiles/<file_key>.json
        catalog.duckdb        derived index, rebuilt at the end of every scan
        catalog.duckdb.tmp    build target, replaced atomically
        last_scan.json        ScanSummary of the latest run
    <cache_dir>/recent_roots.json

The JSON profiles are the source of truth; the catalog is derived from them.
Nothing is ever written inside the scanned folder.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from duckfleet_core.models import PROFILE_SCHEMA_VERSION, DatabaseProfile, ScanSummary

logger = logging.getLogger("duckfleet")

MAX_RECENT_ROOTS = 20  # L-13


def root_key(root: Path) -> str:
    """Stable key of a root folder: sha1 of its normalised absolute path, 16 hex chars."""
    text = os.path.normcase(str(Path(root).resolve())).replace("\\", "/")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def file_key(rel_path: str) -> str:
    """Stable key of a database file inside a root: sha1 of its relative path, 24 hex chars."""
    return hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:24]


def _write_atomic(path: Path, text: str) -> None:
    """Write via a unique temp file and ``os.replace`` so readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


class ProfileStore:
    """Per-root view of the cache. Creates nothing until the first write."""

    def __init__(self, cache_dir: Path, root: Path):
        self.cache_dir = Path(cache_dir)
        self.root = Path(root).resolve()
        self.root_key = root_key(self.root)
        self.root_dir = self.cache_dir / "roots" / self.root_key
        self.profiles_dir = self.root_dir / "profiles"
        self.catalog_path = self.root_dir / "catalog.duckdb"
        self.tmp_catalog_path = self.root_dir / "catalog.duckdb.tmp"
        self.summary_path = self.root_dir / "last_scan.json"

    def _profile_path(self, rel_path: str) -> Path:
        return self.profiles_dir / f"{file_key(rel_path)}.json"

    def _read(self, path: Path) -> DatabaseProfile | None:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("cannot read profile %s: %s", path, exc)
            return None
        try:
            return DatabaseProfile.model_validate_json(text)
        except ValidationError as exc:
            logger.warning("ignoring unreadable profile %s: %s", path, exc.error_count())
            return None

    def load(self, rel_path: str) -> DatabaseProfile | None:
        """Cached profile of ``rel_path``, or ``None`` when missing or unreadable."""
        profile = self._read(self._profile_path(rel_path))
        if profile is not None and profile.rel_path != rel_path:  # key collision guard
            return None
        return profile

    def save(self, profile: DatabaseProfile) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        root_txt = self.root_dir / "root.txt"
        if not root_txt.exists():
            root_txt.write_text(str(self.root), encoding="utf-8")
        _write_atomic(self._profile_path(profile.rel_path), profile.model_dump_json())

    def load_all(self) -> list[DatabaseProfile]:
        """All current-version profiles, sorted by ``rel_path``."""
        if not self.profiles_dir.is_dir():
            return []
        profiles = []
        for path in self.profiles_dir.glob("*.json"):
            profile = self._read(path)
            if profile is not None and profile.profile_schema_version == PROFILE_SCHEMA_VERSION:
                profiles.append(profile)
        profiles.sort(key=lambda p: p.rel_path)
        return profiles

    def prune(self, keep_rel_paths: set[str]) -> int:
        """Delete profiles of files that were not discovered; returns how many."""
        if not self.profiles_dir.is_dir():
            return 0
        keep = {file_key(rel) for rel in keep_rel_paths}
        removed = 0
        for path in self.profiles_dir.iterdir():
            if path.name.endswith(".tmp"):
                path.unlink(missing_ok=True)
            elif path.suffix == ".json" and path.stem not in keep:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def save_summary(self, summary: ScanSummary) -> None:
        _write_atomic(self.summary_path, summary.model_dump_json(indent=2))

    def load_summary(self) -> ScanSummary | None:
        try:
            text = self.summary_path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return ScanSummary.model_validate_json(text)
        except ValidationError:
            return None


def _recent_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "recent_roots.json"


def recent_roots(cache_dir: Path) -> list[dict]:
    """``[{"root": ..., "last_scanned_at": ...}]``, newest first."""
    try:
        data = json.loads(_recent_path(cache_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [
        {"root": str(item["root"]), "last_scanned_at": str(item.get("last_scanned_at", ""))}
        for item in data
        if isinstance(item, dict) and "root" in item
    ]


def remember_root(cache_dir: Path, root: Path) -> None:
    """Move ``root`` to the front of the recent roots list (at most 20 are kept)."""
    resolved = Path(root).resolve()
    key = root_key(resolved)
    entries = [e for e in recent_roots(cache_dir) if root_key(Path(e["root"])) != key]
    entries.insert(
        0,
        {
            "root": str(resolved),
            "last_scanned_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    _write_atomic(_recent_path(cache_dir), json.dumps(entries[:MAX_RECENT_ROOTS], indent=2))
