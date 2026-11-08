"""Access to the scanned databases for the Database and SQL pages.

``query_config()`` is resolved once per process (decision D-021): DuckDB refuses
a second connection to the same file with another threads, memory or temp
directory setting, so the pages share one ``QueryConfig`` and never expose those
fields. Summaries and preview pages are cached (Streamlit cache keyed by the
database file's live size and mtime, summaries also as JSON sidecars); ad-hoc SQL
is never cached.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import streamlit as st

from duckfleet_core.query import QueryConfig, QueryResult, preview_table
from duckfleet_core.store import SummaryStore
from duckfleet_core.summary import TableSummary
from duckfleet_core.tempdir import process_temp_dir
from duckfleet_ui import common

ENV_QUERY_THREADS = "DUCKFLEET_QUERY_THREADS"
ENV_QUERY_MEMORY_MB = "DUCKFLEET_QUERY_MEMORY_MB"

_TEMPORAL_PREFIXES = ("DATE", "TIMESTAMP")
SUMMARY_COLUMNS = [
    "column_name", "column_type", "min", "max", "approx_unique", "avg", "std", "q25", "q50",
    "q75", "count", "null_percentage",
]  # fmt: skip


def _env_int(name: str) -> int | None:
    text = os.environ.get(name, "").strip()
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value > 0 else None


@functools.lru_cache(maxsize=1)
def query_config() -> QueryConfig:
    """The process-wide ``QueryConfig`` (optional overrides from the environment).

    DuckDB's temp directory is ``<cache dir>/tmp/<pid>``, never beside a scanned file (V-01).
    """
    return QueryConfig(
        threads=_env_int(ENV_QUERY_THREADS),
        memory_limit_mb=_env_int(ENV_QUERY_MEMORY_MB),
        temp_dir=str(process_temp_dir(common.cache_dir())),
    )


# ---------------------------------------------------------------- results as DataFrames


def _unique_names(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    names = []
    for name in columns:
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return names


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def frame_from_result(result: QueryResult) -> tuple[pd.DataFrame, list[str]]:
    """DataFrame of a query result for ``st.dataframe``, plus the columns shown as text.

    Temporal columns become datetimes (pandas 2.2 and 3.x); lists and structs stay
    objects. A column that pandas or Arrow cannot hold (a year outside the pandas range,
    UUIDs, huge integers) is converted to text and reported (plan Section 10 item 4).
    """
    names = _unique_names(result.columns)
    frame = pd.DataFrame(result.rows, columns=names)
    as_text: list[str] = []
    for name, data_type in zip(names, result.types, strict=True):
        if data_type.upper().startswith(_TEMPORAL_PREFIXES) and "[" not in data_type:
            try:
                frame[name] = pd.to_datetime(frame[name])
            except (ValueError, TypeError, OverflowError):  # incl. OutOfBoundsDatetime
                frame[name] = frame[name].map(_as_text)
                as_text.append(name)
        elif frame[name].dtype == object:
            try:
                pa.array(frame[name].tolist(), from_pandas=True)
            except (pa.ArrowException, OverflowError, TypeError, ValueError):
                frame[name] = frame[name].map(_as_text)
                as_text.append(name)
    return frame, as_text


# ---------------------------------------------------------------- preview


@st.cache_data(show_spinner=False, max_entries=64)
def preview_page(
    abs_path: str,
    size_bytes: int,
    mtime_ns: int,
    schema_name: str,
    table_name: str,
    columns: tuple[str, ...] | None,
    order_by: str | None,
    descending: bool,
    offset: int,
    limit: int,
) -> tuple[pd.DataFrame, bool, list[str], float]:
    """One preview page as ``(frame, more rows follow, columns shown as text, seconds)``.

    ``size_bytes`` and ``mtime_ns`` only key the cache: pass the file's live values, so a file
    that changed after the scan is read again (V-05). Raises ``QueryError``.
    """
    result = preview_table(
        abs_path,
        schema_name,
        table_name,
        query_config(),
        columns=list(columns) if columns is not None else None,
        order_by=order_by,
        descending=descending,
        offset=offset,
        limit=limit,
    )
    frame, as_text = frame_from_result(result)
    return frame, result.truncated, as_text, result.elapsed_s


# ---------------------------------------------------------------- summaries


def summary_store(cache_dir: Path, root: str) -> SummaryStore:
    return SummaryStore(cache_dir, Path(root))


def cached_summary(
    cache_dir: Path,
    root: str,
    rel_path: str,
    abs_path: str,
    schema_name: str,
    table_name: str,
) -> TableSummary | None:
    """The valid cached summary of a table, or ``None`` (no query runs)."""
    try:
        stat = Path(abs_path).stat()
    except OSError:
        return None
    return summary_store(cache_dir, root).load(
        rel_path, schema_name, table_name, size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns
    )


def compute_summary(
    cache_dir: Path,
    root: str,
    rel_path: str,
    abs_path: str,
    schema_name: str,
    table_name: str,
    row_count: int | None,
) -> TableSummary:
    """Compute (or recompute) a summary, save it and return it; failures set ``error``."""
    return summary_store(cache_dir, root).get_or_compute(
        abs_path,
        rel_path,
        schema_name,
        table_name,
        query_config(),
        row_count=row_count,
        refresh=True,
    )


def summary_frame(summary: TableSummary) -> pd.DataFrame:
    rows = [[getattr(column, name) for name in SUMMARY_COLUMNS] for column in summary.columns]
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
