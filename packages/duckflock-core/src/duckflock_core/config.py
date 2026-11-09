"""ScanConfig: every knob of a scan, with the resolved defaults."""

from __future__ import annotations

import os
import re
from pathlib import Path

import platformdirs
import psutil
from pydantic import BaseModel, Field, field_validator

from duckflock_core.tags import DEFAULT_TAG_PATTERNS

APP_NAME = "duckflock"
_MIN_MEMORY_MB = 256
_MAX_MEMORY_MB = 2048
# Windows limits ProcessPoolExecutor to 61 workers; keep the same cap everywhere.
MAX_POOL_WORKERS = 61


class ScanConfig(BaseModel):
    """Scan options. Workers receive ``model_dump(mode="json")`` (a plain dict)."""

    extensions: list[str] = Field(default_factory=lambda: [".duckdb", ".db", ".ddb"])
    sniff_all_files: bool = False
    exclude_dirs: list[str] = Field(
        default_factory=lambda: [".git", ".venv", "__pycache__", "node_modules", ".duckflock"]
    )
    follow_symlinks: bool = False
    max_files: int = 1000  # L-01
    workers: int | None = None  # L-02
    worker_threads: int = 1  # L-03
    worker_memory_limit_mb: int | None = None  # L-04
    stats_row_threshold: int = 20_000_000  # L-05, TUNING(stats-threshold)
    stats_sample_rows: int = 1_000_000  # L-06, TUNING(stats-threshold)
    # Tables up to this many rows get a content hash (L-07, TUNING(content-hash)); 0 turns the
    # hash off, an empty table is hashed as "empty" otherwise.
    content_hash_rows: int = Field(default=10_000, ge=0)
    include_views_in_fingerprint: bool = False  # TUNING(views-in-fingerprint)
    count_view_rows: bool = False
    tag_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_TAG_PATTERNS))
    full: bool = False
    cache_dir: str | None = None

    @field_validator("extensions")
    @classmethod
    def _normalise_extensions(cls, value: list[str]) -> list[str]:
        """Lower-case, with a leading dot, so ``DB`` and ``.db`` mean the same."""
        out: list[str] = []
        for ext in value:
            ext = ext.strip().lower()
            if not ext:
                continue
            out.append(ext if ext.startswith(".") else "." + ext)
        return out

    @field_validator("tag_patterns")
    @classmethod
    def _check_tag_patterns(cls, value: list[str]) -> list[str]:
        """Every pattern must compile and carry the named groups ``key`` and ``value`` (R-03)."""
        for pattern in value:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid tag pattern {pattern!r}: {exc}") from None
            if not {"key", "value"} <= set(compiled.groupindex):
                raise ValueError(
                    f"invalid tag pattern {pattern!r}: it needs the named groups "
                    "(?P<key>...) and (?P<value>...)"
                )
        return value

    def resolved_workers(self) -> int:
        """``workers``, or ``max(1, cpu_count - 2)`` when unset, at most ``MAX_POOL_WORKERS``.

        Decision D-005; the cap (R-04) keeps the summary, the pool and the UI input in agreement.
        """
        wanted = self.workers if self.workers is not None else (os.cpu_count() or 1) - 2
        return min(max(1, wanted), MAX_POOL_WORKERS)

    def resolved_memory_limit_mb(self) -> int:
        """Memory limit per worker in MB.

        Default ``min(2048, total_ram_mb // 2 // workers)``, floor 256.
        """
        # TUNING(worker-memory)
        if self.worker_memory_limit_mb is not None:
            return max(1, self.worker_memory_limit_mb)
        total_mb = psutil.virtual_memory().total // (1024 * 1024)
        per_worker = total_mb // 2 // self.resolved_workers()
        return max(_MIN_MEMORY_MB, min(_MAX_MEMORY_MB, per_worker))

    def resolved_cache_dir(self) -> Path:
        """``cache_dir``, or the per-user cache directory (decision D-009)."""
        if self.cache_dir:
            return Path(self.cache_dir)
        return default_cache_dir()


def default_cache_dir() -> Path:
    """``platformdirs.user_cache_dir("duckflock")`` without the doubled author folder."""
    return Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False))
