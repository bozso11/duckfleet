"""Per-column summaries of one table (DuckDB ``SUMMARIZE``), computed on demand.

``summarize_table`` never raises for a failing table: the failure becomes a
``TableSummary`` with ``error`` set, so a page can show it next to the rest.
Summaries are cached as JSON sidecars by ``store.SummaryStore`` (decision D-020).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel

from duckfleet_core.query import (
    QueryConfig,
    QueryError,
    as_query_errors,
    database_name,
    execute_limited,
    open_readonly,
)
from duckfleet_core.sqlutil import qualified_name

SUMMARY_SCHEMA_VERSION = 1
_MAX_SUMMARY_COLUMNS = 100_000  # SUMMARIZE returns one row per column


class ColumnSummary(BaseModel):
    column_name: str
    column_type: str
    min: str | None = None
    max: str | None = None
    approx_unique: int | None = None
    avg: str | None = None
    std: str | None = None
    q25: str | None = None
    q50: str | None = None
    q75: str | None = None
    count: int | None = None
    null_percentage: float | None = None


class TableSummary(BaseModel):
    summary_schema_version: int = SUMMARY_SCHEMA_VERSION
    rel_path: str
    schema_name: str
    table_name: str
    size_bytes: int  # of the database file when computed
    mtime_ns: int
    duckdb_version: str
    computed_at: str  # ISO 8601 UTC
    elapsed_s: float
    row_count: int | None
    sampled: bool
    sample_rows: int | None
    columns: list[ColumnSummary]
    error: str | None = None


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _integer(value: Any) -> int | None:
    return None if value is None else int(value)


def _column_summary(row: dict[str, Any]) -> ColumnSummary:
    null_percentage = row.get("null_percentage")
    return ColumnSummary(
        column_name=str(row["column_name"]),
        column_type=str(row["column_type"]),
        min=_text(row.get("min")),
        max=_text(row.get("max")),
        approx_unique=_integer(row.get("approx_unique")),
        avg=_text(row.get("avg")),
        std=_text(row.get("std")),
        q25=_text(row.get("q25")),
        q50=_text(row.get("q50")),
        q75=_text(row.get("q75")),
        count=_integer(row.get("count")),
        null_percentage=None if null_percentage is None else float(null_percentage),
    )


def summarize_table(
    db_path: str | Path,
    schema_name: str,
    table_name: str,
    config: QueryConfig,
    *,
    row_count: int | None,
    rel_path: str,
    size_bytes: int,
    mtime_ns: int,
) -> TableSummary:
    """``SUMMARIZE`` of a table or view, on a sample above ``config.stats_row_threshold`` rows."""
    started = time.perf_counter()
    # TUNING(stats-threshold): the same switch to sampling as the scan's column stats.
    sampled = row_count is not None and row_count > config.stats_row_threshold
    columns: list[ColumnSummary] = []
    error: str | None = None
    try:
        with as_query_errors(), open_readonly(db_path, config) as con:
            qualified = qualified_name(database_name(con), schema_name, table_name)
            sql = f"SUMMARIZE SELECT * FROM {qualified}"
            if sampled:
                sql += f" USING SAMPLE {int(config.stats_sample_rows)} ROWS"
            result = execute_limited(con, sql, _MAX_SUMMARY_COLUMNS, config.timeout_s)
        columns = [
            _column_summary(dict(zip(result.columns, row, strict=True))) for row in result.rows
        ]
    except QueryError as exc:
        error = f"{exc.kind}: {exc.message}"
    except duckdb.Error as exc:  # not reached through as_query_errors
        error = str(exc)[:1000]
    return TableSummary(
        rel_path=rel_path,
        schema_name=schema_name,
        table_name=table_name,
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        duckdb_version=duckdb.__version__,
        computed_at=datetime.now(UTC).isoformat(timespec="seconds"),
        elapsed_s=round(time.perf_counter() - started, 4),
        row_count=row_count,
        sampled=sampled and error is None,
        sample_rows=int(config.stats_sample_rows) if sampled else None,
        columns=columns,
        error=error,
    )
