from __future__ import annotations

import pytest

from duckfleet_core.fingerprint import (
    canonical_table,
    database_entries,
    database_fingerprint,
    normalize_type,
    table_fingerprint,
)
from duckfleet_core.models import ColumnInfo, TableProfile


def col(name: str, data_type: str, ordinal: int = 1) -> ColumnInfo:
    return ColumnInfo(
        name=name, ordinal=ordinal, data_type=data_type, nullable=True,
        is_temporal=False, is_numeric=False,
    )  # fmt: skip


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


EVENTS = [col("ts", "TIMESTAMP", 1), col("value", "DOUBLE", 2), col("run_id", "INTEGER", 3)]


def test_normalize_type_is_identity_in_v01() -> None:
    assert normalize_type("DECIMAL(18,3)") == "DECIMAL(18,3)"
    assert normalize_type("DECIMAL(18,3)") != normalize_type("DECIMAL(18,4)")


def test_canonical_table_strict_and_loose() -> None:
    assert canonical_table(EVENTS, strict=True) == [
        ["run_id", "INTEGER"],
        ["ts", "TIMESTAMP"],
        ["value", "DOUBLE"],
    ]
    assert canonical_table(EVENTS, strict=False) == ["run_id", "ts", "value"]


def test_fingerprints_are_16_hex_chars_and_stable() -> None:
    strict = table_fingerprint(EVENTS, strict=True)
    loose = table_fingerprint(EVENTS, strict=False)
    assert len(strict) == len(loose) == 16
    int(strict, 16)
    int(loose, 16)
    assert strict != loose
    # pinned: a change here means every cached fingerprint changes (bump PROFILE_SCHEMA_VERSION)
    assert strict == "56cf38956b149548"
    assert loose == "d02812ede453c2a6"


def test_column_order_does_not_matter() -> None:
    reordered = [EVENTS[2], EVENTS[0], EVENTS[1]]
    assert table_fingerprint(reordered, strict=True) == table_fingerprint(EVENTS, strict=True)
    assert table_fingerprint(reordered, strict=False) == table_fingerprint(EVENTS, strict=False)


def test_type_change_alters_strict_but_not_loose() -> None:
    changed = [EVENTS[0], col("value", "FLOAT", 2), EVENTS[2]]
    assert table_fingerprint(changed, strict=True) != table_fingerprint(EVENTS, strict=True)
    assert table_fingerprint(changed, strict=False) == table_fingerprint(EVENTS, strict=False)


def test_renamed_or_added_column_alters_both() -> None:
    renamed = [EVENTS[0], col("val", "DOUBLE", 2), EVENTS[2]]
    extended = [*EVENTS, col("quality", "SMALLINT", 4)]
    for other in (renamed, extended):
        assert table_fingerprint(other, strict=True) != table_fingerprint(EVENTS, strict=True)
        assert table_fingerprint(other, strict=False) != table_fingerprint(EVENTS, strict=False)


def test_non_ascii_and_quoted_names_are_hashed() -> None:
    a = table_fingerprint([col('wé"ird', "VARCHAR")], strict=True)
    b = table_fingerprint([col("weird", "VARCHAR")], strict=True)
    assert a != b


def test_database_fingerprint_ignores_table_order_and_depends_on_names() -> None:
    config = [col("key", "VARCHAR"), col("value", "VARCHAR")]
    a = [table("events", EVENTS), table("config", config)]
    b = [table("config", config), table("events", EVENTS)]
    assert database_fingerprint(a, True, False) == database_fingerprint(b, True, False)
    renamed = [table("events2", EVENTS), table("config", config)]
    assert database_fingerprint(renamed, True, False) != database_fingerprint(a, True, False)
    other_schema = [table("events", EVENTS, schema="s1"), table("config", config)]
    assert database_fingerprint(other_schema, True, False) != database_fingerprint(a, True, False)


def test_database_fingerprint_none_without_tables() -> None:
    assert database_fingerprint([], True, False) is None
    assert database_fingerprint([], False, True) is None
    only_view = [table("v", EVENTS, kind="view")]
    assert database_fingerprint(only_view, True, False) is None
    assert database_fingerprint(only_view, True, True) is not None


def test_views_only_count_when_included() -> None:
    base = [table("events", EVENTS)]
    with_view = [*base, table("v", EVENTS, kind="view")]
    assert database_fingerprint(with_view, True, False) == database_fingerprint(base, True, False)
    assert database_fingerprint(with_view, True, True) != database_fingerprint(base, True, True)


def test_database_entries_shape() -> None:
    entries = database_entries([table("b", EVENTS), table("a", EVENTS)], True, False)
    assert [e[:2] for e in entries] == [["main", "a"], ["main", "b"]]


@pytest.mark.parametrize("strict", [True, False])
def test_strict_and_loose_database_fingerprints_differ_on_types_only_when_strict(
    strict: bool,
) -> None:
    a = [table("events", EVENTS)]
    b = [table("events", [EVENTS[0], col("value", "FLOAT", 2), EVENTS[2]])]
    same = database_fingerprint(a, strict, False) == database_fingerprint(b, strict, False)
    assert same is (not strict)
