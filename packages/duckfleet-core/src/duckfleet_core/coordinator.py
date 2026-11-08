"""run_scan(): discovery -> reuse decision -> process pool -> store -> catalog.

Progress is reported through the ``on_event`` callback with the events of
``docs/PLAN_v0.1.md`` Section 3.5 (plain dicts, JSON-serialisable).
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from duckfleet_core import __version__
from duckfleet_core.catalog import build_catalog
from duckfleet_core.config import MAX_POOL_WORKERS, ScanConfig
from duckfleet_core.discovery import Candidate, discover
from duckfleet_core.models import (
    PROFILE_SCHEMA_VERSION,
    DatabaseProfile,
    ProfileError,
    ScanSummary,
)
from duckfleet_core.profiler import error_profile, profile_database
from duckfleet_core.store import ProfileStore, remember_root
from duckfleet_core.tempdir import sweep_stale_temp_dirs

logger = logging.getLogger("duckfleet")

EventCallback = Callable[[dict], None]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _can_reuse(existing: DatabaseProfile | None, candidate: Candidate) -> bool:
    """Reuse rule (plan 3.3 rule 3): unchanged size, mtime and schema version, status ok.

    The WAL sibling flag must match too, so a file that gained or lost its WAL
    is profiled again, and so is every file after a DuckDB upgrade (P-01: type
    spellings can change between versions and would split a family).
    """
    return (
        existing is not None
        and existing.status == "ok"
        and existing.size_bytes == candidate.size_bytes
        and existing.mtime_ns == candidate.mtime_ns
        and existing.profile_schema_version == PROFILE_SCHEMA_VERSION
        and existing.has_wal == candidate.has_wal
        and existing.duckdb_version == duckdb.__version__
    )


def _failed_profile(
    candidate: Candidate, config: ScanConfig, error_type: str, exc: BaseException
) -> DatabaseProfile:
    return error_profile(
        str(candidate.abs_path),
        candidate.rel_path,
        config,
        ProfileError(
            type=error_type,  # type: ignore[arg-type]
            message=f"{type(exc).__name__}: {exc}"[:500] or type(exc).__name__,
            exception=type(exc).__name__,
        ),
        size_bytes=candidate.size_bytes,
        mtime_ns=candidate.mtime_ns,
        has_wal=candidate.has_wal,
        storage_version=candidate.storage_version,
    )


def _profiles_in_process(
    candidates: list[Candidate], config_dict: dict
) -> Iterator[tuple[Candidate, dict | DatabaseProfile]]:
    for candidate in candidates:
        yield candidate, profile_database(str(candidate.abs_path), candidate.rel_path, config_dict)


def _profiles_in_pool(
    candidates: list[Candidate], config_dict: dict, config: ScanConfig, pool_size: int
) -> Iterator[tuple[Candidate, dict | DatabaseProfile]]:
    """Yield ``(candidate, profile dict or DatabaseProfile)`` as the workers finish.

    A broken pool (a worker died) turns every unfinished candidate into a
    ``worker_crashed`` profile (docs/LIMITS.md L-10). When the consumer stops
    early (an exception, Ctrl-C) the queued files are cancelled instead of
    being profiled first (P-02).
    """
    context = multiprocessing.get_context("spawn")  # same behaviour on Windows and Linux
    pool = ProcessPoolExecutor(max_workers=pool_size, mp_context=context)
    try:
        futures: dict[Future, Candidate] = {}
        unsubmitted: list[Candidate] = []
        crash: BaseException = BrokenProcessPool("the process pool broke")
        for candidate in candidates:
            try:
                future = pool.submit(
                    profile_database, str(candidate.abs_path), candidate.rel_path, config_dict
                )
            except BrokenProcessPool as exc:
                unsubmitted.append(candidate)
                crash = exc
                continue
            futures[future] = candidate
        for future in as_completed(futures):
            candidate = futures[future]
            try:
                yield candidate, future.result()
            except BrokenProcessPool as exc:
                yield candidate, _failed_profile(candidate, config, "worker_crashed", exc)
            except Exception as exc:  # e.g. an unpicklable result; the worker itself never raises
                yield candidate, _failed_profile(candidate, config, "other", exc)
        for candidate in unsubmitted:
            yield candidate, _failed_profile(candidate, config, "worker_crashed", crash)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def run_scan(root: Path, config: ScanConfig, on_event: EventCallback | None = None) -> ScanSummary:
    """Scan ``root``: discover, profile (reusing cached profiles), build the catalog."""
    emit: EventCallback = on_event or (lambda event: None)
    started_at = _now()
    cache_dir = config.resolved_cache_dir()

    # Resolve the defaults once so the summary and the workers see the same numbers.
    workers = config.resolved_workers()
    resolved = config.model_copy(
        update={
            "workers": workers,
            "worker_memory_limit_mb": config.resolved_memory_limit_mb(),
            "cache_dir": str(cache_dir),
        }
    )

    discovery = discover(Path(root), resolved)  # raises NotADirectoryError for a bad root
    # Temp folders of processes that no longer exist (a crashed worker leaves one) (V-01).
    removed_tmp = sweep_stale_temp_dirs(cache_dir)
    if removed_tmp:
        logger.debug("removed stale temp folders: %s", ", ".join(removed_tmp))
    store = ProfileStore(cache_dir, discovery.root)
    emit(
        {
            "event": "discovered",
            "walked": discovery.walked_files,
            "candidates": len(discovery.candidates),
            "rejected": len(discovery.rejected),
            "truncated": discovery.truncated,
        }
    )

    to_profile: list[Candidate] = []
    reused = 0
    for candidate in discovery.candidates:
        existing = None if resolved.full else store.load(candidate.rel_path)
        if _can_reuse(existing, candidate):
            reused += 1
        else:
            to_profile.append(candidate)
    emit({"event": "planned", "to_profile": len(to_profile), "reused": reused})

    config_dict = resolved.model_dump(mode="json")
    pool_size = min(workers, len(to_profile), MAX_POOL_WORKERS)  # no idle workers
    stream = (
        _profiles_in_process(to_profile, config_dict)
        if pool_size <= 1
        else _profiles_in_pool(to_profile, config_dict, resolved, pool_size)
    )
    done = 0
    with contextlib.closing(stream):  # an error below must stop the pool at once (P-02)
        for candidate, result in stream:
            if isinstance(result, DatabaseProfile):
                profile = result
            else:
                try:
                    profile = DatabaseProfile.model_validate(result)
                except Exception as exc:
                    profile = _failed_profile(candidate, resolved, "other", exc)
            store.save(profile)
            done += 1
            emit(
                {
                    "event": "profiled",
                    "done": done,
                    "total": len(to_profile),
                    "rel_path": profile.rel_path,
                    "status": profile.status,
                    "error_type": profile.error.type if profile.error else None,
                    "seconds": profile.profile_seconds,
                }
            )

    store.prune({candidate.rel_path for candidate in discovery.candidates})
    profiles = store.load_all()
    summary = ScanSummary(
        run_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6],
        root=str(discovery.root),
        started_at=started_at,
        finished_at=_now(),
        duckfleet_version=__version__,
        duckdb_version=duckdb.__version__,
        workers=workers,
        files_walked=discovery.walked_files,
        files_candidates=len(discovery.candidates),
        files_rejected=len(discovery.rejected),
        files_profiled=len(to_profile),
        files_reused=reused,
        files_failed=sum(p.status == "error" for p in profiles),
        truncated=discovery.truncated,
        catalog_path=str(store.catalog_path),
        rejected=discovery.rejected,
        config=resolved,
    )
    build_catalog(profiles, summary, discovery.rejected, store.catalog_path, store.tmp_catalog_path)
    emit({"event": "catalog", "path": str(store.catalog_path)})

    store.save_summary(summary)
    remember_root(cache_dir, discovery.root)
    emit({"event": "finished", "summary": summary.model_dump(mode="json")})
    return summary
