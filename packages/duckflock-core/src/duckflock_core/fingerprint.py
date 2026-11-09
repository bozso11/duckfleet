"""Canonical schemas and fingerprints (pure functions, no I/O).

A strict fingerprint covers table names, column names and column types; a
loose one covers names only. Column order never matters (decision D-007).
Fingerprints are the first 16 hex characters of a sha256 over compact JSON.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from duckflock_core.models import ColumnInfo, DatabaseProfile, TableProfile


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


# ---------------------------------------------------------------- families


@dataclass(frozen=True)
class FamilyGroup:
    """Databases sharing one database fingerprint."""

    level: str  # "strict" or "loose"
    family_id: str  # F1, F2, ... (strict) or L1, L2, ... (loose)
    fingerprint: str
    members: list[str]  # rel_paths, sorted
    canonical: list[Any]  # [[schema, table, canonical_table(...)], ...] of the first member

    @property
    def n_tables(self) -> int:
        return len(self.canonical)


def family_groups(
    profiles: list[DatabaseProfile], strict: bool, include_views: bool = False
) -> list[FamilyGroup]:
    """Group profiles by database fingerprint, largest family first.

    Ordered by member count descending, then fingerprint ascending, so the
    numbering is deterministic. Profiles without a fingerprint (errors, no
    tables) belong to no family.
    """
    # TUNING(family-labels)
    by_fp: dict[str, list[DatabaseProfile]] = {}
    for profile in profiles:
        fp = profile.fp_strict if strict else profile.fp_loose
        if profile.status == "ok" and fp is not None:
            by_fp.setdefault(fp, []).append(profile)

    prefix = "F" if strict else "L"
    ordered = sorted(by_fp.items(), key=lambda item: (-len(item[1]), item[0]))
    groups: list[FamilyGroup] = []
    for number, (fp, members) in enumerate(ordered, start=1):
        members = sorted(members, key=lambda p: p.rel_path)
        groups.append(
            FamilyGroup(
                level="strict" if strict else "loose",
                family_id=f"{prefix}{number}",
                fingerprint=fp,
                members=[m.rel_path for m in members],
                canonical=[
                    [t.schema_name, t.name, canonical_table(t.columns, strict)]
                    for t in sorted(members[0].tables, key=lambda t: (t.schema_name, t.name))
                    if t.kind == "table" or include_views
                ],
            )
        )
    return groups


def assign_families(profiles: list[DatabaseProfile], strict: bool) -> dict[str, str]:
    """``rel_path -> family id`` (``F1...`` strict, ``L1...`` loose)."""
    return {
        rel_path: group.family_id
        for group in family_groups(profiles, strict)
        for rel_path in group.members
    }


@dataclass(frozen=True)
class TableVariant:
    """One distinct strict structure of a ``(schema, table)`` across the flock."""

    schema_name: str
    table_name: str
    fp_strict: str
    n_members: int  # databases having this exact structure
    columns: list[list[str]]  # strict canonical: [[name, type], ...]

    @property
    def n_columns(self) -> int:
        return len(self.columns)


def table_variants(profiles: list[DatabaseProfile]) -> list[TableVariant]:
    """Distinct strict structures per ``(schema, table)`` over all ``ok`` profiles.

    Tables only (views are not fingerprinted). Ordered by schema, table, then
    member count descending and fingerprint.
    """
    counts: dict[tuple[str, str, str], int] = {}
    columns: dict[tuple[str, str, str], list[list[str]]] = {}
    for profile in profiles:
        if profile.status != "ok":
            continue
        for table in profile.tables:
            if table.kind != "table":
                continue
            key = (table.schema_name, table.name, table.fp_strict)
            counts[key] = counts.get(key, 0) + 1
            columns.setdefault(key, canonical_table(table.columns, strict=True))
    variants = [
        TableVariant(schema, name, fp, n, columns[(schema, name, fp)])
        for (schema, name, fp), n in counts.items()
    ]
    variants.sort(key=lambda v: (v.schema_name, v.table_name, -v.n_members, v.fp_strict))
    return variants


# ---------------------------------------------------------------- diffs

SchemaMap = dict[tuple[str, str], list[tuple[str, str]]]  # (schema, table) -> [(column, type)]


def schema_map_from_profile(tables: list[TableProfile], include_views: bool = False) -> SchemaMap:
    """Schema map from profile tables (columns in ordinal order)."""
    return {
        (t.schema_name, t.name): [
            (c.name, c.data_type) for c in sorted(t.columns, key=lambda c: c.ordinal)
        ]
        for t in tables
        if t.kind == "table" or include_views
    }


def schema_map_from_rows(
    table_rows: list[dict], column_rows: list[dict], include_views: bool = False
) -> SchemaMap:
    """Schema map from catalog ``tables`` and ``columns`` rows (as dicts)."""
    wanted = {
        (r["schema_name"], r["table_name"])
        for r in table_rows
        if r.get("kind", "table") == "table" or include_views
    }
    result: SchemaMap = {key: [] for key in wanted}
    for row in sorted(column_rows, key=lambda r: r["ordinal"]):
        key = (row["schema_name"], row["table_name"])
        if key in result:
            result[key].append((row["column_name"], row["data_type"]))
    return result


@dataclass(frozen=True)
class TypeChange:
    name: str
    type_a: str
    type_b: str


@dataclass(frozen=True)
class TableDiff:
    table: str  # "schema.table"
    columns_only_in_a: list[str] = field(default_factory=list)
    columns_only_in_b: list[str] = field(default_factory=list)
    type_changes: list[TypeChange] = field(default_factory=list)


@dataclass(frozen=True)
class SchemaDiff:
    only_in_a: list[str]  # "schema.table" present only in A
    only_in_b: list[str]
    changed: list[TableDiff]  # common tables that differ

    @property
    def identical(self) -> bool:
        return not (self.only_in_a or self.only_in_b or self.changed)


def _label(key: tuple[str, str]) -> str:
    return f"{key[0]}.{key[1]}"


def diff_schemas(a: SchemaMap, b: SchemaMap) -> SchemaDiff:
    """Structural difference between two schema maps (column order is ignored)."""
    changed: list[TableDiff] = []
    for key in sorted(a.keys() & b.keys()):
        types_a, types_b = dict(a[key]), dict(b[key])
        only_a = sorted(types_a.keys() - types_b.keys())
        only_b = sorted(types_b.keys() - types_a.keys())
        type_changes = [
            TypeChange(name, types_a[name], types_b[name])
            for name in sorted(types_a.keys() & types_b.keys())
            if normalize_type(types_a[name]) != normalize_type(types_b[name])
        ]
        if only_a or only_b or type_changes:
            changed.append(TableDiff(_label(key), only_a, only_b, type_changes))
    return SchemaDiff(
        only_in_a=sorted(_label(k) for k in a.keys() - b.keys()),
        only_in_b=sorted(_label(k) for k in b.keys() - a.keys()),
        changed=changed,
    )
