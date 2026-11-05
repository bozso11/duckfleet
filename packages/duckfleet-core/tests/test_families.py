"""Families, table variants and schema diffs (fingerprint.py, phase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from duckfleet_core.config import ScanConfig
from duckfleet_core.discovery import discover
from duckfleet_core.fingerprint import (
    SchemaDiff,
    TableDiff,
    TypeChange,
    assign_families,
    canonical_table,
    database_fingerprint,
    diff_schemas,
    family_groups,
    schema_map_from_profile,
    schema_map_from_rows,
    table_fingerprint,
    table_variants,
)
from duckfleet_core.models import ColumnInfo, DatabaseProfile, TableProfile
from duckfleet_core.profiler import profile_database


def col(name: str, data_type: str, ordinal: int = 1) -> ColumnInfo:
    return ColumnInfo(
        name=name,
        ordinal=ordinal,
        data_type=data_type,
        nullable=True,
        is_temporal=False,
        is_numeric=False,
    )


def table(
    name: str, columns: list[ColumnInfo], schema: str = "main", kind: str = "table"
) -> TableProfile:
    return TableProfile(
        schema_name=schema,
        name=name,
        kind=kind,  # type: ignore[arg-type]
        columns=columns,
        fp_strict=table_fingerprint(columns, strict=True),
        fp_loose=table_fingerprint(columns, strict=False),
    )


def db(rel_path: str, tables: list[TableProfile], status: str = "ok") -> DatabaseProfile:
    return DatabaseProfile(
        rel_path=rel_path,
        abs_path="/x/" + rel_path,
        file_name=rel_path.rsplit("/", 1)[-1],
        size_bytes=1,
        mtime_ns=1,
        has_wal=False,
        duckdb_version="x",
        profiled_at="2026-01-01T00:00:00+00:00",
        profile_seconds=0.0,
        status=status,  # type: ignore[arg-type]
        tables=tables,
        fp_strict=database_fingerprint(tables, True, False) if status == "ok" else None,
        fp_loose=database_fingerprint(tables, False, False) if status == "ok" else None,
    )


EVENTS = [col("ts", "TIMESTAMP", 1), col("value", "DOUBLE", 2), col("run_id", "INTEGER", 3)]
FLOAT_EVENTS = [EVENTS[0], col("value", "FLOAT", 2), EVENTS[2]]
CONFIG = [col("key", "VARCHAR"), col("value", "VARCHAR", 2)]


def test_assign_families_orders_by_size_then_fingerprint() -> None:
    profiles = [
        db("a/1", [table("events", EVENTS)]),
        db("b/1", [table("events", FLOAT_EVENTS)]),
        db("b/2", [table("events", FLOAT_EVENTS)]),
        db("b/3", [table("events", FLOAT_EVENTS)]),
        db("c/1", [table("events", EVENTS), table("config", CONFIG)]),
        db("c/2", [table("events", EVENTS), table("config", CONFIG)]),
    ]
    strict = assign_families(profiles, strict=True)
    assert {strict[r] for r in ("b/1", "b/2", "b/3")} == {"F1"}
    assert strict["c/1"] == strict["c/2"] == "F2"
    assert strict["a/1"] == "F3"
    loose = assign_families(profiles, strict=False)
    # a and b share the loose structure (names only): L1 has 4 members
    assert {loose[r] for r in ("a/1", "b/1", "b/2", "b/3")} == {"L1"}
    assert loose["c/1"] == loose["c/2"] == "L2"


def test_equal_sized_families_are_ordered_by_fingerprint() -> None:
    profiles = [db("x", [table("events", EVENTS)]), db("y", [table("events", FLOAT_EVENTS)])]
    groups = family_groups(profiles, strict=True)
    assert [g.fingerprint for g in groups] == sorted(g.fingerprint for g in groups)
    assert [g.family_id for g in groups] == ["F1", "F2"]


def test_profiles_without_fingerprint_get_no_family() -> None:
    profiles = [
        db("ok", [table("events", EVENTS)]),
        db("empty", []),
        db("err", [], status="error"),
    ]
    assert assign_families(profiles, strict=True) == {"ok": "F1"}
    assert assign_families(profiles, strict=False) == {"ok": "L1"}


def test_family_group_details() -> None:
    profiles = [db("b", [table("events", EVENTS)]), db("a", [table("events", EVENTS)])]
    (group,) = family_groups(profiles, strict=True)
    assert group.members == ["a", "b"] and group.n_tables == 1
    assert group.canonical == [["main", "events", canonical_table(EVENTS, True)]]
    (loose,) = family_groups(profiles, strict=False)
    assert loose.canonical == [["main", "events", ["run_id", "ts", "value"]]]
    assert (group.level, loose.level) == ("strict", "loose")


def test_table_variants_count_members_per_structure() -> None:
    profiles = [
        db("a", [table("events", EVENTS), table("config", CONFIG)]),
        db("b", [table("events", EVENTS), table("config", CONFIG)]),
        db("c", [table("events", FLOAT_EVENTS)]),
        db("d", [], status="error"),
        db("e", [table("v", EVENTS, kind="view")]),
    ]
    variants = table_variants(profiles)
    events = [v for v in variants if v.table_name == "events"]
    assert [(v.n_members, v.n_columns) for v in events] == [(2, 3), (1, 3)]
    assert events[0].fp_strict == table_fingerprint(EVENTS, strict=True)
    assert events[0].columns == canonical_table(EVENTS, strict=True)
    config = [v for v in variants if v.table_name == "config"]
    assert [v.n_members for v in config] == [2]
    assert all(v.table_name != "v" for v in variants)  # views are not variants


def test_diff_of_hand_built_schemas() -> None:
    a = {
        ("main", "events"): [("ts", "TIMESTAMP"), ("value", "DOUBLE"), ("gone", "INTEGER")],
        ("main", "only_a"): [("x", "INTEGER")],
        ("main", "same"): [("k", "VARCHAR")],
    }
    b = {
        ("main", "events"): [("value", "FLOAT"), ("ts", "TIMESTAMP"), ("new", "SMALLINT")],
        ("main", "only_b"): [("y", "INTEGER")],
        ("main", "same"): [("k", "VARCHAR")],
    }
    diff = diff_schemas(a, b)
    assert diff == SchemaDiff(
        only_in_a=["main.only_a"],
        only_in_b=["main.only_b"],
        changed=[
            TableDiff(
                table="main.events",
                columns_only_in_a=["gone"],
                columns_only_in_b=["new"],
                type_changes=[TypeChange("value", "DOUBLE", "FLOAT")],
            )
        ],
    )
    assert not diff.identical
    assert diff_schemas(a, a).identical
    # column order alone is no difference
    ordered = {("main", "same"): [("k", "VARCHAR"), ("z", "INTEGER")]}
    reordered = {("main", "same"): [("z", "INTEGER"), ("k", "VARCHAR")]}
    assert diff_schemas(ordered, reordered).identical


def test_schema_maps_from_profile_and_from_rows_agree() -> None:
    tables = [
        table("events", EVENTS),
        table("config", CONFIG),
        table("v", EVENTS, kind="view"),
    ]
    from_profile = schema_map_from_profile(tables)
    assert set(from_profile) == {("main", "events"), ("main", "config")}
    assert from_profile[("main", "events")] == [
        ("ts", "TIMESTAMP"),
        ("value", "DOUBLE"),
        ("run_id", "INTEGER"),
    ]
    table_rows = [
        {"schema_name": t.schema_name, "table_name": t.name, "kind": t.kind} for t in tables
    ]
    column_rows = [
        {
            "schema_name": t.schema_name,
            "table_name": t.name,
            "ordinal": c.ordinal,
            "column_name": c.name,
            "data_type": c.data_type,
        }
        for t in tables
        for c in t.columns
    ]
    assert schema_map_from_rows(table_rows, column_rows) == from_profile
    assert set(schema_map_from_rows(table_rows, column_rows, include_views=True)) == {
        ("main", "events"),
        ("main", "config"),
        ("main", "v"),
    }


@pytest.fixture(scope="module")
def demo_profiles(demo_fleet: Path) -> list[DatabaseProfile]:
    config = ScanConfig().model_dump(mode="json")
    result = discover(demo_fleet, ScanConfig())
    return [
        DatabaseProfile.model_validate(profile_database(str(c.abs_path), c.rel_path, config))
        for c in result.candidates
    ]


def runs(experiment: str, count: int) -> frozenset[str]:
    return frozenset(f"{experiment}/run_0{i}/metrics.duckdb" for i in range(1, count + 1))


def test_demo_fleet_families(demo_profiles: list[DatabaseProfile]) -> None:
    ok = [p for p in demo_profiles if p.status == "ok"]
    assert len(demo_profiles) == 13 and len(ok) == 10

    strict = family_groups(demo_profiles, strict=True)
    assert [len(g.members) for g in strict] == [3, 3, 2, 1]
    assert {frozenset(g.members) for g in strict} == {
        runs("exp_01", 3),
        runs("exp_02", 3),
        runs("exp_03", 2),
        frozenset({"legacy/old.db"}),
    }
    loose = family_groups(demo_profiles, strict=False)
    assert [len(g.members) for g in loose] == [6, 2, 1]
    assert set(loose[0].members) == runs("exp_01", 3) | runs("exp_02", 3)

    families = assign_families(demo_profiles, strict=True)
    assert "empty/empty.duckdb" not in families  # no tables, no family
    assert "broken/corrupt.duckdb" not in families  # failed
    assert sorted(set(families.values())) == ["F1", "F2", "F3", "F4"]


def test_demo_fleet_table_variants_and_diff(demo_profiles: list[DatabaseProfile]) -> None:
    variants = table_variants(demo_profiles)
    events = [v for v in variants if v.table_name == "events"]
    assert sorted(v.n_members for v in events) == [2, 3, 3]
    config = [v for v in variants if v.table_name == "config"]
    assert [v.n_members for v in config] == [8]  # identical across all eight run files

    by_rel = {p.rel_path: p for p in demo_profiles}

    def schema_of(rel_path: str):
        return schema_map_from_profile(by_rel[rel_path].tables)

    one = schema_of("exp_01/run_01/metrics.duckdb")
    two = schema_of("exp_02/run_01/metrics.duckdb")
    three = schema_of("exp_03/run_01/metrics.duckdb")
    assert diff_schemas(one, two).changed == [
        TableDiff("main.events", [], [], [TypeChange("value", "DOUBLE", "FLOAT")])
    ]
    diff = diff_schemas(one, three)
    assert diff.only_in_b == ["main.notes"] and diff.only_in_a == []
    assert diff.changed == [TableDiff("main.events", [], ["quality"], [])]
    assert diff_schemas(one, schema_of("exp_01/run_02/metrics.duckdb")).identical
