"""DuckDB temp directories under the cache: one folder per file per process (D-034, D-042).

DuckDB's default ``temp_directory`` is ``<database path>.tmp``, i.e. beside the
scanned file, and a query that spills past its memory limit creates it even on a
read-only connection (review V-01). Every connection duckflock opens therefore passes
a folder under the cache instead.

Two DuckDB instances in one process must never share a temp folder: when both spill
they write the same ``duckdb_temp_storage_DEFAULT-0.tmp`` and the process dies
(review W-01). Connections to one file with an identical config share one DuckDB
instance (D-021), so the folder is derived from the file:
``<cache dir>/tmp/<pid>/<sha1(absolute path)[:16]>``, built by ``file_temp_dir`` and
used by the query layer, the profiler and the catalog writer alike. A flock connection
(several files, one in-memory instance) gets a folder of its own, ``flock-<8 hex>``.

The ``<pid>`` folder is created before connecting and removed when the process exits
(best effort); folders of processes that no longer exist are swept by ``run_scan`` and
on the first connection of every process (review P-08).
"""

from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import uuid
from pathlib import Path

import psutil

_registered: set[str] = set()
_swept: set[str] = set()


def temp_root(cache_dir: Path | str) -> Path:
    """``<cache_dir>/tmp``, the parent of the per-process folders."""
    return Path(cache_dir) / "tmp"


def process_temp_dir(cache_dir: Path | str) -> Path:
    """``<cache_dir>/tmp/<pid>`` for this process."""
    return temp_root(cache_dir) / str(os.getpid())


def file_temp_dir(root: Path | str, abs_path: Path | str) -> Path:
    """``<root>/<sha1(absolute path)[:16]>``: the temp folder of the DuckDB instance of one file.

    ``root`` is the per-process folder. The path is made absolute and case-normalised
    (Windows) first, so the same file always maps to the same folder however it is spelled;
    that keeps one config per file per process (D-021).
    """
    normal = os.path.normcase(os.path.abspath(os.fspath(abs_path)))
    return Path(root) / hashlib.sha1(normal.encode("utf-8")).hexdigest()[:16]


def flock_temp_dir(root: Path | str) -> Path:
    """``<root>/flock-<8 hex>``: a new folder name for one flock connection (not created)."""
    return Path(root) / f"flock-{uuid.uuid4().hex[:8]}"


def _remove(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def ensure_temp_dir(path: Path | str) -> str:
    """Create ``path`` (and its parents), remove it at interpreter exit, return it as text."""
    text = str(path)
    Path(text).mkdir(parents=True, exist_ok=True)
    if text not in _registered:
        _registered.add(text)
        atexit.register(_remove, text)
    return text


def _sweep_once(root: Path) -> None:
    """Sweep stale folders before the first connection that uses this process folder (P-08)."""
    if root.name != str(os.getpid()) or root.parent.name != "tmp":
        return  # not the standard layout: never look at a folder we did not name
    key = str(root.parent)
    if key in _swept:
        return
    _swept.add(key)
    sweep_stale_temp_dirs(root.parent.parent)


def ensure_file_temp_dir(root: Path | str, abs_path: Path | str) -> str:
    """Create the process folder ``root`` and the leaf of ``abs_path``; return the leaf as text.

    Only ``root`` is registered for removal at exit; the leaf goes with it.
    """
    root = Path(root)
    _sweep_once(root)
    ensure_temp_dir(root)
    leaf = file_temp_dir(root, abs_path)
    leaf.mkdir(parents=True, exist_ok=True)
    return str(leaf)


def ensure_flock_temp_dir(root: Path | str) -> str:
    """Create a new flock leaf under ``root`` and return it; the caller removes it when done."""
    root = Path(root)
    _sweep_once(root)
    ensure_temp_dir(root)
    leaf = flock_temp_dir(root)
    leaf.mkdir(parents=True, exist_ok=True)
    return str(leaf)


def remove_temp_dir(path: Path | str) -> None:
    """Remove a leaf (best effort; DuckDB may still hold a handle on Windows for a moment)."""
    shutil.rmtree(path, ignore_errors=True)


def sweep_stale_temp_dirs(cache_dir: Path | str) -> list[str]:
    """Remove ``tmp/<pid>`` folders whose process no longer exists; return their names.

    Folders with a non-numeric name, this process's own folder and folders of
    live processes stay. A reused pid keeps a stale folder alive until that
    process ends, which is harmless.
    """
    root = temp_root(cache_dir)
    removed: list[str] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return removed
    for entry in entries:
        if not entry.name.isdigit() or not entry.is_dir():
            continue
        pid = int(entry.name)
        if pid == os.getpid() or psutil.pid_exists(pid):
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not entry.exists():
            removed.append(entry.name)
    return removed
