"""Profile one DuckDB file, read-only. Entry point of the worker processes.

``profile_database`` takes and returns plain types (it is pickled by the
process pool) and never raises: any failure becomes a profile with
``status="error"``. Nothing here ever writes to the scanned file.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from duckfleet_core.config import ScanConfig
from duckfleet_core.discovery import has_wal_sibling, looks_locked, read_header
from duckfleet_core.fingerprint import database_fingerprint, table_fingerprint
from duckfleet_core.models import (
    ColumnInfo,
    ColumnStats,
    DatabaseProfile,
    ProfileError,
    TableProfile,
)
from duckfleet_core.tags import extract_tags

logger = logging.getLogger("duckfleet")

# TUNING(temporal-types): column types that count as temporal for time windows.
TEMPORAL_TYPES = frozenset(
    {
        "DATE",
        "TIMESTAMP",
        "TIMESTAMP WITH TIME ZONE",
        "TIMESTAMP_S",
        "TIMESTAMP_MS",
        "TIMESTAMP_NS",
    }
)
_NUMERIC_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "FLOAT",
        "DOUBLE",
        "DECIMAL",
    }
)
# TUNING(stats-column-types): types without approx distinct (nested types and BLOB).
_NO_DISTINCT_PREFIXES = ("STRUCT", "LIST", "MAP", "UNION", "ARRAY")

# Substrings of DuckDB's error messages, see docs/PLAN_v0.1.md Section 4 item 3.
_LOCKED = (
    "being used by another process",  # Windows
    "could not set lock",  # Linux
    "conflicting lock",  # Linux
)
_MISSING = ("database does not exist", "no such file")
_NOT_DUCKDB = ("not a valid duckdb database file",)
_CORRUPT = ("corrupt", "checksum", "reached the end of the file")
_INCOMPATIBLE = ("version number",)  # TBC: not reproduced
_ENCRYPTED = ("encrypt",)  # TBC: needs an encrypted fixture

_MESSAGE_LIMIT = 500


class NotDuckDBError(Exception):
    """The file header lacks the DuckDB magic bytes (never handed to DuckDB)."""


def quote_ident(name: str) -> str:
    """Quote an SQL identifier: wrap in ``"`` and double embedded ``"``."""
    return '"' + name.replace('"', '""') + '"'


def is_temporal_type(data_type: str) -> bool:
    # TUNING(temporal-types)
    return data_type.upper() in TEMPORAL_TYPES


def is_numeric_type(data_type: str) -> bool:
    return data_type.upper().split("(")[0] in _NUMERIC_TYPES


def _supports_distinct(data_type: str) -> bool:
    # TUNING(stats-column-types)
    upper = data_type.upper()
    if upper == "BLOB" or "[" in upper:
        return False
    return not upper.startswith(_NO_DISTINCT_PREFIXES)


def classify_error(exc: BaseException) -> ProfileError:
    """Map an exception to a ``ProfileError`` type by message substrings (case-insensitive)."""
    text = str(exc)
    lower = text.lower()
    error_type: str
    if any(s in lower for s in _LOCKED) or looks_locked(exc):
        error_type = "locked"
    elif isinstance(exc, FileNotFoundError) or any(s in lower for s in _MISSING):
        error_type = "missing"
    elif isinstance(exc, NotDuckDBError) or any(s in lower for s in _NOT_DUCKDB):
        error_type = "not_duckdb"
    elif any(s in lower for s in _CORRUPT):
        error_type = "corrupt"
    elif any(s in lower for s in _INCOMPATIBLE):
        error_type = "incompatible_version"
    elif any(s in lower for s in _ENCRYPTED):
        error_type = "encrypted"
    else:
        error_type = "other"
    return ProfileError(
        type=error_type,  # type: ignore[arg-type]
        message=text[:_MESSAGE_LIMIT],
        exception=type(exc).__name__,
    )


def profile_database(abs_path: str, rel_path: str, config_dict: dict) -> dict:
    """Profile one file and return ``DatabaseProfile.model_dump(mode="json")``. Never raises."""
    started = time.perf_counter()
    try:
        config = ScanConfig.model_validate(config_dict)
    except Exception:  # a bad config must not kill the worker
        config = ScanConfig()
    path = Path(abs_path)
    facts = _file_facts(path, rel_path, config)
    try:
        profile = _profile(path, config, facts, started)
    except Exception as exc:
        profile = _error_profile(facts, exc, started)
    return profile.model_dump(mode="json")


def error_profile(
    abs_path: str,
    rel_path: str,
    config: ScanConfig,
    error: ProfileError,
    *,
    size_bytes: int,
    mtime_ns: int,
    has_wal: bool,
    storage_version: int | None,
) -> DatabaseProfile:
    """Profile for a file that could not be profiled (used by the coordinator for crashes)."""
    path = Path(abs_path)
    facts = _file_facts(path, rel_path, config)
    facts.update(
        size_bytes=size_bytes, mtime_ns=mtime_ns, has_wal=has_wal, storage_version=storage_version
    )
    return _build_error(facts, error, time.perf_counter())


# ---------------------------------------------------------------- internals


def _file_facts(path: Path, rel_path: str, config: ScanConfig) -> dict[str, Any]:
    """Facts known without opening the database. Never raises."""
    try:
        stat = path.stat()
        size_bytes, mtime_ns = stat.st_size, stat.st_mtime_ns
    except OSError:
        size_bytes, mtime_ns = 0, 0
    try:
        has_wal = has_wal_sibling(path)
    except OSError:
        has_wal = False
    return {
        "rel_path": rel_path,
        "abs_path": str(path),
        "file_name": path.name,
        "size_bytes": size_bytes,
        "mtime_ns": mtime_ns,
        "has_wal": has_wal,
        "storage_version": None,
        "duckdb_version": duckdb.__version__,
        "tags": extract_tags(rel_path, config.tag_patterns),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _build_error(facts: dict[str, Any], error: ProfileError, started: float) -> DatabaseProfile:
    return DatabaseProfile(
        **facts,
        profiled_at=_now(),
        profile_seconds=round(time.perf_counter() - started, 4),
        status="error",
        error=error,
        health=["wal_present"] if facts["has_wal"] else [],
    )


def _error_profile(facts: dict[str, Any], exc: BaseException, started: float) -> DatabaseProfile:
    return _build_error(facts, classify_error(exc), started)


def _as_datetime(value: Any) -> datetime | None:
    """Datetime for comparison: dates become midnight, aware values naive UTC."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            # TUNING(timezones)
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return None


def _serialise(value: Any) -> str | None:
    """datetime/date -> isoformat(); Decimal, ints, floats -> str(); None stays None."""
    if value is None:
        return None
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _profile(
    path: Path, config: ScanConfig, facts: dict[str, Any], started: float
) -> DatabaseProfile:
    is_duckdb, storage_version = read_header(path)  # OSError (also FileNotFound) -> classified
    facts["storage_version"] = storage_version
    if not is_duckdb:
        raise NotDuckDBError(
            f"{path.name} is not a valid DuckDB database file (header lacks the DuckDB magic bytes)"
        )

    con = duckdb.connect(
        str(path),
        read_only=True,
        config={
            "threads": config.worker_threads,
            "memory_limit": f"{config.resolved_memory_limit_mb()}MB",
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        },
    )
    try:
        schemas, tables = _introspect(con, config)
    finally:
        con.close()

    return _assemble(facts, schemas, tables, config, started)


@dataclass
class _TableWork:
    schema_name: str
    name: str
    kind: str
    estimated_size: int | None
    columns: list[ColumnInfo]


def _introspect(
    con: duckdb.DuckDBPyConnection, config: ScanConfig
) -> tuple[list[str], list[_TableResult]]:
    row = con.execute("select database_name from duckdb_databases() where not internal").fetchone()
    if row is None:
        return [], []
    db_name = row[0]

    # "main" is flagged internal even in a user database, so schemas also come from the objects.
    schema_names = {
        r[0]
        for r in con.execute(
            "select schema_name from duckdb_schemas() where database_name = ? and not internal",
            [db_name],
        ).fetchall()
    }

    table_rows = con.execute(
        "select schema_name, table_name, estimated_size from duckdb_tables() "
        "where database_name = ? and not internal and not temporary "
        "order by schema_name, table_name",
        [db_name],
    ).fetchall()
    view_rows = con.execute(
        "select schema_name, view_name from duckdb_views() "
        "where database_name = ? and not internal and not temporary "
        "order by schema_name, view_name",
        [db_name],
    ).fetchall()
    column_rows = con.execute(
        "select schema_name, table_name, column_name, column_index, data_type, is_nullable "
        "from duckdb_columns() where database_name = ? and not internal "
        "order by schema_name, table_name, column_index",
        [db_name],
    ).fetchall()

    columns_by_table: dict[tuple[str, str], list[ColumnInfo]] = {}
    for schema_name, table_name, column_name, index, data_type, nullable in column_rows:
        columns_by_table.setdefault((schema_name, table_name), []).append(
            ColumnInfo(
                name=column_name,
                ordinal=int(index),
                data_type=data_type,
                nullable=bool(nullable),
                is_temporal=is_temporal_type(data_type),
                is_numeric=is_numeric_type(data_type),
            )
        )

    work = [
        _TableWork(
            s, t, "table", int(size) if size is not None else None, columns_by_table.get((s, t), [])
        )
        for s, t, size in table_rows
    ] + [_TableWork(s, v, "view", None, columns_by_table.get((s, v), [])) for s, v in view_rows]
    work.sort(key=lambda w: (w.schema_name, w.name))

    schema_names.update(item.schema_name for item in work)
    results = [_profile_table(con, db_name, item, config) for item in work]
    return sorted(schema_names), results


@dataclass
class _TableResult:
    profile: TableProfile
    time_min: datetime | None
    time_max: datetime | None


def _value_expr(column: ColumnInfo) -> str:
    """Expression min/max run over; aware timestamps are converted to UTC in SQL."""
    quoted = quote_ident(column.name)
    if column.data_type.upper() == "TIMESTAMP WITH TIME ZONE":
        # TUNING(timezones): converting in SQL also avoids the pytz requirement of the
        # Python client for timestamptz values.
        return f"{quoted} AT TIME ZONE 'UTC'"
    return quoted


def _run_aggregates(
    con: duckdb.DuckDBPyConnection,
    qualified: str,
    columns: list[ColumnInfo],
    wanted: Any,
    sample_rows: int | None,
) -> dict[tuple[int, str], Any]:
    """One aggregate query over all columns; ``wanted(column)`` yields the stat kinds."""
    expressions: list[str] = []
    keys: list[tuple[int, str]] = []
    for index, column in enumerate(columns):
        quoted = quote_ident(column.name)
        for kind in wanted(column):
            if kind == "count":
                expressions.append(f"count({quoted})")
            elif kind in ("min", "max"):
                expressions.append(f"{kind}({_value_expr(column)})")
            else:
                expressions.append(f"approx_count_distinct({quoted})")
            keys.append((index, kind))
    if not expressions:
        return {}
    sample = f" USING SAMPLE {int(sample_rows)} ROWS" if sample_rows else ""
    row = con.execute(f"SELECT {', '.join(expressions)} FROM {qualified}{sample}").fetchone()
    assert row is not None
    return dict(zip(keys, row, strict=True))


def _profile_table(
    con: duckdb.DuckDBPyConnection, db_name: str, item: _TableWork, config: ScanConfig
) -> _TableResult:
    columns = item.columns
    qualified = ".".join(quote_ident(p) for p in (db_name, item.schema_name, item.name))
    row_count: int | None = None
    error: str | None = None
    t_min: datetime | None = None
    t_max: datetime | None = None
    stats: dict[int, ColumnStats] = {}

    try:
        if item.kind == "table" or config.count_view_rows:
            row = con.execute(f"SELECT count(*) FROM {qualified}").fetchone()
            row_count = int(row[0]) if row else None
        if item.kind == "table" and columns:
            stats, t_min, t_max = _table_stats(con, qualified, columns, row_count, config)
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:_MESSAGE_LIMIT]}"
        logger.debug("table error in %s: %s", qualified, error)

    profiled_columns = [
        column.model_copy(update={"stats": stats.get(index)})
        for index, column in enumerate(columns)
    ]
    table = TableProfile(
        schema_name=item.schema_name,
        name=item.name,
        kind=item.kind,  # type: ignore[arg-type]
        row_count=row_count,
        estimated_size=item.estimated_size,
        columns=profiled_columns,
        fp_strict=table_fingerprint(columns, strict=True),
        fp_loose=table_fingerprint(columns, strict=False),
        time_min=t_min.isoformat() if t_min else None,
        time_max=t_max.isoformat() if t_max else None,
        error=error,
    )
    return _TableResult(table, t_min, t_max)


def _table_stats(
    con: duckdb.DuckDBPyConnection,
    qualified: str,
    columns: list[ColumnInfo],
    row_count: int | None,
    config: ScanConfig,
) -> tuple[dict[int, ColumnStats], datetime | None, datetime | None]:
    # TUNING(stats-threshold)
    sampled = row_count is not None and row_count > config.stats_row_threshold

    def wants_minmax(column: ColumnInfo) -> bool:
        return column.is_numeric or column.is_temporal

    if not sampled:

        def wanted(column: ColumnInfo) -> list[str]:
            kinds = ["count"]
            if wants_minmax(column):
                kinds += ["min", "max"]
            if _supports_distinct(column.data_type):
                kinds.append("distinct")
            return kinds

        values = _run_aggregates(con, qualified, columns, wanted, None)
    else:
        # Temporal min/max exactly (cheap, and the time windows depend on them) ...
        def wanted_exact(column: ColumnInfo) -> list[str]:
            return ["min", "max"] if column.is_temporal else []

        values = _run_aggregates(con, qualified, columns, wanted_exact, None)

        # ... everything else on a sample.
        def wanted_sampled(column: ColumnInfo) -> list[str]:
            kinds = ["count"]
            if column.is_numeric:
                kinds += ["min", "max"]
            if _supports_distinct(column.data_type):
                kinds.append("distinct")
            return kinds

        values.update(
            _run_aggregates(con, qualified, columns, wanted_sampled, config.stats_sample_rows)
        )

    stats: dict[int, ColumnStats] = {}
    mins: list[datetime] = []
    maxs: list[datetime] = []
    for index, column in enumerate(columns):
        low = values.get((index, "min"))
        high = values.get((index, "max"))
        distinct = values.get((index, "distinct"))
        count = values.get((index, "count"))
        stats[index] = ColumnStats(
            non_null_count=int(count) if count is not None else None,
            min=_serialise(low),
            max=_serialise(high),
            approx_distinct=int(distinct) if distinct is not None else None,
            sampled=sampled,
        )
        if column.is_temporal:
            low_dt, high_dt = _as_datetime(low), _as_datetime(high)
            if low_dt is not None:
                mins.append(low_dt)
            if high_dt is not None:
                maxs.append(high_dt)
    # TUNING(time-window-scope): all temporal columns of all tables contribute.
    return stats, (min(mins) if mins else None), (max(maxs) if maxs else None)


def _assemble(
    facts: dict[str, Any],
    schemas: list[str],
    results: list[_TableResult],
    config: ScanConfig,
    started: float,
) -> DatabaseProfile:
    tables = [r.profile for r in results]
    real_tables = [t for t in tables if t.kind == "table"]

    health: list[str] = []
    if facts["has_wal"]:
        health.append("wal_present")
    if not real_tables:
        health.append("empty_database")
    elif not any(c.is_temporal for t in real_tables for c in t.columns):
        health.append("no_temporal_columns")
    health += [f"table_error:{t.schema_name}.{t.name}" for t in tables if t.error]

    mins = [r.time_min for r in results if r.time_min is not None and r.profile.kind == "table"]
    maxs = [r.time_max for r in results if r.time_max is not None and r.profile.kind == "table"]
    include_views = config.include_views_in_fingerprint
    return DatabaseProfile(
        **facts,
        profiled_at=_now(),
        profile_seconds=round(time.perf_counter() - started, 4),
        status="ok",
        schemas=schemas,
        tables=tables,
        fp_strict=database_fingerprint(tables, strict=True, include_views=include_views),
        fp_loose=database_fingerprint(tables, strict=False, include_views=include_views),
        time_min=min(mins).isoformat() if mins else None,
        time_max=max(maxs).isoformat() if maxs else None,
        total_rows=sum(t.row_count or 0 for t in real_tables),
        health=health,
    )
