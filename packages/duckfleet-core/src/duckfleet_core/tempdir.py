"""Per-process DuckDB temp directories under the cache (review V-01, decision D-034).

DuckDB's default ``temp_directory`` is ``<database path>.tmp``, i.e. beside the
scanned file, and a query that spills past its memory limit creates it even on a
read-only connection. Every connection duckfleet opens therefore passes
``<cache_dir>/tmp/<pid>`` instead. Each process gets its own folder because
whether several DuckDB instances can share one temp directory safely is not
verified; the folder is created before connecting, removed when the process
exits (best effort) and swept by ``run_scan`` when its process no longer exists.
"""

from __future__ import annotations

import atexit
import os
import shutil
from pathlib import Path

import psutil

_registered: set[str] = set()


def temp_root(cache_dir: Path | str) -> Path:
    """``<cache_dir>/tmp``, the parent of the per-process folders."""
    return Path(cache_dir) / "tmp"


def process_temp_dir(cache_dir: Path | str) -> Path:
    """``<cache_dir>/tmp/<pid>`` for this process."""
    return temp_root(cache_dir) / str(os.getpid())


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
