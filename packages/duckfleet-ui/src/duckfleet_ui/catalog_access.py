"""Cached DataFrame access to the catalog for the Overview page.

Every function takes ``(catalog_path, catalog_mtime_ns, ...)`` so a rebuilt
catalog (new mtime) invalidates the cache. A ``CatalogReader`` is created per
call and never kept: an open handle would block the next scan's ``os.replace``
on Windows (plan Section 10 item 4).

Code here works with pandas 2.2 and 3.x (explicit datetime conversion).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from duckfleet_core.catalog import CatalogReader
from duckfleet_core.fingerprint import SchemaDiff

DATABASE_COLUMNS = [
    "rel_path", "size_mb", "n_tables", "total_rows", "family_strict", "family_loose",
    "time_min", "time_max", "tags", "status", "error_type", "health",
]  # fmt: skip
FAMILY_COLUMNS = ["family_id", "n_members", "n_tables", "tables", "members"]
VARIANT_COLUMNS = ["table", "n_variants", "fp_strict", "n_members", "n_columns", "columns"]
TIMELINE_COLUMNS = ["rel_path", "family_strict", "time_min", "time_max"]
PROBLEM_COLUMNS = ["rel_path", "status", "error_type", "health", "message"]
REJECTED_COLUMNS = ["rel_path", "reason"]


def catalog_key(catalog_path: str | Path) -> tuple[str, int] | None:
    """``(path, mtime_ns)`` cache key of a catalog file, or ``None`` when it does not exist."""
    path = Path(catalog_path)
    try:
        return str(path), path.stat().st_mtime_ns
    except OSError:
        return None


def _frame(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    """DataFrame with a fixed column order, also when there are no rows."""
    return pd.DataFrame(rows, columns=columns)


def _datetimes(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        frame[column] = pd.to_datetime(frame[column])
    return frame


@st.cache_data(show_spinner=False)
def scan_run(catalog_path: str, catalog_mtime_ns: int) -> dict[str, Any]:
    return CatalogReader(catalog_path).scan_run() or {}


@st.cache_data(show_spinner=False)
def databases(catalog_path: str, catalog_mtime_ns: int) -> pd.DataFrame:
    rows = CatalogReader(catalog_path).databases()
    for row in rows:
        row["size_mb"] = row["size_bytes"] / 1_000_000
        row["tags"] = ", ".join(f"{key}={value}" for key, value in row["tags"].items())
        row["health"] = ", ".join(row["health"])
    return _datetimes(_frame(rows, DATABASE_COLUMNS), ["time_min", "time_max"])


@st.cache_data(show_spinner=False)
def families(catalog_path: str, catalog_mtime_ns: int, level: str) -> pd.DataFrame:
    rows = CatalogReader(catalog_path).families(level)
    for row in rows:
        row["tables"] = ", ".join(
            f"{schema}.{table}" for schema, table, _columns in row["canonical"]
        )
    return _frame(rows, FAMILY_COLUMNS)


@st.cache_data(show_spinner=False)
def family_matrix(catalog_path: str, catalog_mtime_ns: int, level: str) -> pd.DataFrame:
    """Rows are families, columns ``schema.table``, cells the members having the table."""
    reader = CatalogReader(catalog_path)
    order = [family["family_id"] for family in reader.families(level)]
    cells = reader.family_table_matrix(level)
    if not cells:
        return pd.DataFrame(index=pd.Index(order, name="family_id"))
    frame = pd.DataFrame(cells)
    frame["table"] = frame["schema_name"] + "." + frame["table_name"]
    matrix = frame.pivot(index="family_id", columns="table", values="n_members_with_table")
    return matrix.reindex(order).fillna(0).astype(int)


@st.cache_data(show_spinner=False)
def table_variants(catalog_path: str, catalog_mtime_ns: int) -> pd.DataFrame:
    rows = CatalogReader(catalog_path).table_variants()
    variants_per_table: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row["schema_name"], row["table_name"])
        variants_per_table[key] = variants_per_table.get(key, 0) + 1
    for row in rows:
        row["table"] = f"{row['schema_name']}.{row['table_name']}"
        row["n_variants"] = variants_per_table[(row["schema_name"], row["table_name"])]
        row["columns"] = ", ".join(f"{name} {data_type}" for name, data_type in row["columns"])
    return _frame(rows, VARIANT_COLUMNS)


@st.cache_data(show_spinner=False)
def timeline(catalog_path: str, catalog_mtime_ns: int) -> pd.DataFrame:
    rows = CatalogReader(catalog_path).timeline()
    frame = _datetimes(_frame(rows, TIMELINE_COLUMNS), ["time_min", "time_max"])
    frame["family_strict"] = frame["family_strict"].fillna("(none)").astype(str)
    return frame


@st.cache_data(show_spinner=False)
def problems(catalog_path: str, catalog_mtime_ns: int) -> pd.DataFrame:
    rows = CatalogReader(catalog_path).health()
    for row in rows:
        row["health"] = ", ".join(row["health"])
        row["message"] = row["error_message"]
    return _frame(rows, PROBLEM_COLUMNS)


@st.cache_data(show_spinner=False)
def rejected(catalog_path: str, catalog_mtime_ns: int) -> pd.DataFrame:
    return _frame(CatalogReader(catalog_path).rejected(), REJECTED_COLUMNS)


@st.cache_data(show_spinner=False)
def diff(catalog_path: str, catalog_mtime_ns: int, rel_path_a: str, rel_path_b: str) -> SchemaDiff:
    return CatalogReader(catalog_path).diff(rel_path_a, rel_path_b)
