"""Pydantic models: the profile of one database file and the summary of a scan.

Profiles are serialised with ``model_dump_json()`` and loaded with
``model_validate_json()``. All timestamps are ISO 8601 strings; ``profiled_at``
is UTC. Bump ``PROFILE_SCHEMA_VERSION`` whenever ``DatabaseProfile`` changes
shape: cached profiles with another version are re-profiled.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from duckfleet_core.config import ScanConfig
from duckfleet_core.discovery import RejectedFile

PROFILE_SCHEMA_VERSION = 2  # 2: TableProfile.content_hash (v0.4)

ErrorType = Literal[
    "locked",
    "not_duckdb",
    "corrupt",
    "incompatible_version",
    "encrypted",
    "missing",
    "worker_crashed",
    "other",
]


class ColumnStats(BaseModel):
    non_null_count: int | None = None
    min: str | None = None  # numeric and temporal columns only
    max: str | None = None
    approx_distinct: int | None = None
    # True: counts, approx_distinct and numeric min/max come from a sample
    # (temporal min/max are always exact).
    sampled: bool = False


class ColumnInfo(BaseModel):
    name: str
    ordinal: int  # duckdb_columns().column_index, 1-based
    data_type: str  # duckdb_columns().data_type verbatim, e.g. "DECIMAL(18,3)"
    nullable: bool
    is_temporal: bool  # see TUNING(temporal-types)
    is_numeric: bool
    stats: ColumnStats | None = None


class TableProfile(BaseModel):
    schema_name: str
    name: str
    kind: Literal["table", "view"]
    row_count: int | None = None  # exact for tables; None for views unless count_view_rows
    estimated_size: int | None = None  # duckdb_tables().estimated_size, tables only
    columns: list[ColumnInfo]
    fp_strict: str  # 16 hex chars, see fingerprint.py
    fp_loose: str
    time_min: str | None = None  # min over temporal columns' mins
    time_max: str | None = None
    error: str | None = None  # per-table failure, table still listed
    # md5 of the sorted rows as text for base tables up to content_hash_rows rows, "empty" for an
    # empty table, None for a larger table, a view or a table whose rows cannot be hashed (D-041)
    content_hash: str | None = None


class ProfileError(BaseModel):
    type: ErrorType
    message: str  # first 500 chars of the exception text
    exception: str  # exception class name


class DatabaseProfile(BaseModel):
    profile_schema_version: int = PROFILE_SCHEMA_VERSION
    rel_path: str  # posix separators, relative to root
    abs_path: str
    file_name: str
    size_bytes: int
    mtime_ns: int
    has_wal: bool  # "<path>.wal" exists next to the file
    storage_version: int | None = None  # uint64 little-endian at byte offset 12 (verified)
    duckdb_version: str  # library used for profiling
    profiled_at: str
    profile_seconds: float
    status: Literal["ok", "error"]
    error: ProfileError | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    schemas: list[str] = Field(default_factory=list)
    tables: list[TableProfile] = Field(default_factory=list)
    fp_strict: str | None = None  # None when status=error or no tables
    fp_loose: str | None = None
    time_min: str | None = None
    time_max: str | None = None
    total_rows: int | None = None  # sum of table row counts (tables only)
    # "wal_present", "empty_database", "no_temporal_columns", "table_error:<schema>.<table>"
    health: list[str] = Field(default_factory=list)


class ScanSummary(BaseModel):
    """Result of one scan, also written to ``last_scan.json``.

    ``workers`` is the resolved configured worker count; the pool itself never
    has more processes than files to profile.
    """

    run_id: str
    root: str
    started_at: str
    finished_at: str
    duckfleet_version: str
    duckdb_version: str
    workers: int
    files_walked: int
    files_candidates: int
    files_rejected: int
    files_profiled: int  # profiled during this run (reused ones not counted)
    files_reused: int
    files_failed: int  # candidates whose profile has status "error"
    truncated: bool
    catalog_path: str
    rejected: list[RejectedFile] = Field(default_factory=list)
    config: ScanConfig
