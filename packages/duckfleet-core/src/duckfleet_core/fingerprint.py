"""Canonical schemas and fingerprints (pure functions, no I/O).

A strict fingerprint covers table names, column names and column types; a
loose one covers names only. Column order never matters (decision D-007).
Fingerprints are the first 16 hex characters of a sha256 over compact JSON.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from duckfleet_core.models import ColumnInfo, TableProfile


def normalize_type(data_type: str) -> str:
    """Type string used in strict fingerprints.

    Identity in v0.1: ``DECIMAL(18,3)`` and ``DECIMAL(18,4)`` differ.
    """
    # TUNING(type-normalization)
    return data_type


def canonical_table(columns: list[ColumnInfo], strict: bool) -> list[Any]:
    """Canonical column list of one table.

    Strict: ``[[name, type], ...]`` sorted by name. Loose: ``[name, ...]`` sorted.
    """
    if strict:
        return sorted([[c.name, normalize_type(c.data_type)] for c in columns])
    return sorted(c.name for c in columns)


def _digest(canonical: Any) -> str:
    text = json.dumps(canonical, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def table_fingerprint(columns: list[ColumnInfo], strict: bool) -> str:
    """16-hex fingerprint of one table's canonical column list."""
    return _digest(canonical_table(columns, strict))


def database_entries(
    tables: list[TableProfile], strict: bool, include_views: bool
) -> list[list[str]]:
    """``[[schema, name, table_fingerprint], ...]`` sorted; the database's canonical form.

    Views are included only when ``include_views`` is true.
    """
    # TUNING(views-in-fingerprint)
    entries = [
        [t.schema_name, t.name, t.fp_strict if strict else t.fp_loose]
        for t in tables
        if t.kind == "table" or include_views
    ]
    return sorted(entries)


def database_fingerprint(
    tables: list[TableProfile], strict: bool, include_views: bool
) -> str | None:
    """Fingerprint of a whole database; ``None`` when it has no tables to hash."""
    entries = database_entries(tables, strict, include_views)
    if not entries:
        return None
    return _digest(entries)
