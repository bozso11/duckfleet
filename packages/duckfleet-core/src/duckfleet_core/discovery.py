"""Find DuckDB files under a root folder.

A file is a candidate when its header carries the DuckDB magic bytes. The
check is mandatory: DuckDB itself opens SQLite files without complaint
(decision D-011), so the extension alone is not trusted.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from duckfleet_core.config import ScanConfig

logger = logging.getLogger("duckfleet")

MAGIC = b"DUCK"
MAGIC_OFFSET = 8
STORAGE_VERSION_OFFSET = 12
HEADER_BYTES = 20  # magic ends at byte 12, the uint64 storage version at byte 20 (verified)
_MAX_STORAGE_VERSION = 2**63 - 1  # a larger value is garbage, not a version (R-01)
_SKIP_SUFFIXES = (".wal", ".tmp")


@dataclass(frozen=True)
class Candidate:
    abs_path: Path
    rel_path: str  # posix separators
    size_bytes: int
    mtime_ns: int
    has_wal: bool
    storage_version: int | None


@dataclass(frozen=True)
class RejectedFile:
    rel_path: str
    reason: str  # "not_duckdb_header", "unreadable", "over_max_files"


@dataclass(frozen=True)
class DiscoveryResult:
    root: Path
    candidates: list[Candidate]
    rejected: list[RejectedFile]
    walked_files: int
    truncated: bool


def read_header(path: Path) -> tuple[bool, int | None]:
    """Return ``(is_duckdb, storage_version)`` from the first 20 bytes of a file.

    Bytes 8..11 are ``b"DUCK"``; bytes 12..19 are the storage version as a
    little-endian uint64; a value above ``2**63 - 1`` is reported as ``None``
    (a header with the magic but garbage after it). Raises ``OSError`` when the
    file cannot be read.
    """
    with open(path, "rb") as handle:
        head = handle.read(HEADER_BYTES)
    if head[MAGIC_OFFSET : MAGIC_OFFSET + len(MAGIC)] != MAGIC:
        return False, None
    if len(head) < HEADER_BYTES:
        return True, None
    version = int.from_bytes(head[STORAGE_VERSION_OFFSET:HEADER_BYTES], "little")
    return True, (version if version <= _MAX_STORAGE_VERSION else None)


def looks_locked(exc: BaseException) -> bool:
    """True when ``exc`` from reading a file most likely means a writer holds it open.

    On Windows DuckDB opens a file exclusively while a read-write connection is
    open, so even reading its header raises ``PermissionError`` (errno 13, no
    ``winerror``). On Linux the lock is advisory and reads succeed, so there a
    ``PermissionError`` is a real permission problem.
    """
    return sys.platform == "win32" and isinstance(exc, PermissionError)


def has_wal_sibling(path: Path) -> bool:
    """True when ``<path>.wal`` exists next to the file."""
    return (path.parent / (path.name + ".wal")).exists()


def discover(root: Path, config: ScanConfig) -> DiscoveryResult:
    """Walk ``root`` and return the DuckDB candidates, largest first.

    Raises ``NotADirectoryError`` when ``root`` is not a directory.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Root folder does not exist or is not a directory: {root}")

    extensions = {ext.lower() for ext in config.extensions}
    excluded = {os.path.normcase(name) for name in config.exclude_dirs}
    # R-06: the cache directory must not be walked, or the catalog becomes a fleet member.
    cache_real = os.path.normcase(os.path.realpath(config.resolved_cache_dir()))
    root_real = os.path.normcase(os.path.realpath(root))
    cache_inside_root = cache_real.startswith(root_real.rstrip(os.sep) + os.sep)

    candidates: list[Candidate] = []
    rejected: list[RejectedFile] = []
    walked = 0
    truncated = False
    visited_real: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root, followlinks=config.follow_symlinks):
        if config.follow_symlinks:
            real = os.path.normcase(os.path.realpath(dirpath))
            if real in visited_real:  # symlink loop
                dirnames[:] = []
                continue
            visited_real.add(real)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if os.path.normcase(d) not in excluded
            and not (
                cache_inside_root
                and os.path.normcase(os.path.realpath(os.path.join(dirpath, d))) == cache_real
            )
        )

        for name in sorted(filenames):
            walked += 1
            lower = name.lower()
            if lower.endswith(_SKIP_SUFFIXES):
                continue
            if not config.sniff_all_files and os.path.splitext(lower)[1] not in extensions:
                continue

            abs_path = Path(dirpath) / name
            rel_path = abs_path.relative_to(root).as_posix()
            try:
                stat = abs_path.stat()
            except OSError as exc:
                logger.warning("cannot stat %s: %s", abs_path, exc)
                rejected.append(RejectedFile(rel_path, "unreadable"))
                continue
            try:
                is_duckdb, storage_version = read_header(abs_path)
            except OSError as exc:
                if looks_locked(exc):
                    # Held by a writer: the header cannot be verified now. Keep it as a
                    # candidate so the worker reports it as "locked" instead of hiding it.
                    is_duckdb, storage_version = True, None
                else:
                    logger.warning("cannot read %s: %s", abs_path, exc)
                    rejected.append(RejectedFile(rel_path, "unreadable"))
                    continue
            if not is_duckdb:
                rejected.append(RejectedFile(rel_path, "not_duckdb_header"))
                continue

            if len(candidates) >= config.max_files:
                # Only a file that really is a further candidate counts as truncation.
                rejected.append(RejectedFile(rel_path, "over_max_files"))
                truncated = True
                break

            candidates.append(
                Candidate(
                    abs_path=abs_path,
                    rel_path=rel_path,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    has_wal=has_wal_sibling(abs_path),
                    storage_version=storage_version,
                )
            )
        if truncated:
            break

    # Largest first balances the pool; rel_path makes the order deterministic.
    candidates.sort(key=lambda c: (-c.size_bytes, c.rel_path))
    return DiscoveryResult(
        root=root,
        candidates=candidates,
        rejected=rejected,
        walked_files=walked,
        truncated=truncated,
    )
