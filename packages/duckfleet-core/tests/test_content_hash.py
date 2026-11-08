"""Content hashes of small tables (plan v0.4 Phase 2, decision D-041)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from duckfleet_core.catalog import CatalogReader
from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.models import PROFILE_SCHEMA_VERSION
from duckfleet_core.profiler import EMPTY_HASH, profile_database
from duckfleet_core.report import build_report
from duckfleet_core.store import ProfileStore

EXP_1_2 = [f"exp_0{e}/run_0{r}/metrics.duckdb" for e in (1, 2) for r in (1, 2, 3)]
EXP_3 = ["exp_03/run_01/metrics.duckdb", "exp_03/run_02/metrics.duckdb"]
EVENTS_FILES = [*EXP_1_2, *EXP_3]


@pytest.fixture(scope="module")
def scanned(
    demo_fleet: Path, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, CatalogReader]:
    cache = tmp_path_factory.mktemp("hashcache")
    summary = run_scan(demo_fleet, ScanConfig(cache_dir=str(cache), workers=1))
    return demo_fleet, CatalogReader(summary.catalog_path)


def hashes(reader: CatalogReader, table: str) -> dict[str, str | None]:
    return {
        row["rel_path"]: row["content_hash"]
        for row in reader.tables()
        if row["table_name"] == table and row["schema_name"] == "main"
    }


def profile_one(tmp_path: Path, statements: list[str], **options) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "t.duckdb"
    con = duckdb.connect(str(path))
    for statement in statements:
        con.execute(statement)
    con.close()
    config = ScanConfig(cache_dir=str(tmp_path / "cache"), **options)
    profile = profile_database(str(path), "t.duckdb", config.model_dump(mode="json"))
    assert profile["status"] == "ok", profile
    return {t["name"]: t for t in profile["tables"]}


def hash_of(tmp_path: Path, statements: list[str], table: str = "t", **options) -> str | None:
    return profile_one(tmp_path, statements, **options)[table]["content_hash"]


# ---------------------------------------------------------------- the demo fleet


def test_the_schema_version_is_two() -> None:
    assert PROFILE_SCHEMA_VERSION == 2
    assert ScanConfig().content_hash_rows == 10_000


def test_identical_config_tables_share_one_hash_and_events_do_not(scanned) -> None:
    _fleet, reader = scanned
    config = hashes(reader, "config")
    assert len(config) == 8 and len(set(config.values())) == 1  # the same settings everywhere
    assert all(isinstance(h, str) and len(h) == 32 for h in config.values())  # md5 hex
    events = hashes(reader, "events")
    assert len(events) == 8 and len(set(events.values())) == 8  # every run has its own values
    notes = hashes(reader, "notes")
    assert set(notes) == set(EXP_3) and len(set(notes.values())) == 1


def test_files_without_tables_and_failed_files_have_no_hashes(scanned) -> None:
    _fleet, reader = scanned
    rows = reader.tables()
    assert not [r for r in rows if r["rel_path"] in ("empty/empty.duckdb", "broken/corrupt.duckdb")]
    assert all(r["content_hash"] is not None for r in rows if r["kind"] == "table")


def test_content_variants_group_the_members_by_hash(scanned) -> None:
    _fleet, reader = scanned
    variants = reader.content_variants()
    config = [v for v in variants if v["table_name"] == "config"]
    assert [(v["n_members"], v["members"]) for v in config] == [(8, sorted(EVENTS_FILES))]
    assert config[0]["schema_name"] == "main" and config[0]["row_count"] == 3
    events = [v for v in variants if v["table_name"] == "events"]
    assert [v["n_members"] for v in events] == [1] * 8
    assert sorted(m for v in events for m in v["members"]) == sorted(EVENTS_FILES)
    notes = [v for v in variants if v["table_name"] == "notes"]
    assert [(v["n_members"], v["members"]) for v in notes] == [(2, EXP_3)]
    assert [(v["schema_name"], v["table_name"]) for v in variants] == sorted(
        (v["schema_name"], v["table_name"]) for v in variants
    )


def test_a_changed_config_makes_a_second_variant_and_the_largest_group_comes_first(
    fresh_demo_fleet: Path, cache_dir: Path
) -> None:
    changed = fresh_demo_fleet / EXP_3[1]
    con = duckdb.connect(str(changed))
    con.execute("UPDATE config SET value = '64' WHERE key = 'batch_size'")
    con.close()
    summary = run_scan(fresh_demo_fleet, ScanConfig(cache_dir=str(cache_dir), workers=1))
    reader = CatalogReader(summary.catalog_path)
    config = [v for v in reader.content_variants() if v["table_name"] == "config"]
    assert [(v["n_members"], v["members"]) for v in config] == [
        (7, sorted(set(EVENTS_FILES) - {EXP_3[1]})),
        (1, [EXP_3[1]]),
    ]
    text = build_report(reader)
    assert "main.config: 2 content variants over 8 databases, largest group 7 databases" in text


def test_the_report_has_an_identical_small_tables_section(scanned) -> None:
    _fleet, reader = scanned
    text = build_report(reader)
    assert "Identical small tables" in text
    section = text.split("Identical small tables")[1].split("Time windows")[0]
    assert "main.config: 1 content variant over 8 databases, largest group 8 databases" in section
    assert "main.events: 8 content variants over 8 databases, no two are identical" in section
    assert "main.notes: 1 content variant over 2 databases, largest group 2 databases" in section
    markdown = build_report(reader, "md")
    assert "## Identical small tables" in markdown


# ---------------------------------------------------------------- the hash itself


def test_an_empty_table_is_hashed_as_empty(tmp_path: Path) -> None:
    tables = profile_one(tmp_path, ["CREATE TABLE t (a INTEGER)", "CREATE TABLE u (b VARCHAR)"])
    assert tables["t"]["content_hash"] == EMPTY_HASH == tables["u"]["content_hash"] == "empty"


def test_a_table_above_the_cap_and_a_view_have_no_hash(tmp_path: Path) -> None:
    tables = profile_one(
        tmp_path,
        [
            "CREATE TABLE t AS SELECT i FROM range(501) r(i)",
            "CREATE TABLE exact AS SELECT i FROM range(500) r(i)",
            "CREATE VIEW v AS SELECT i FROM exact",
        ],
        content_hash_rows=500,
    )
    assert tables["t"]["content_hash"] is None  # 501 rows
    assert len(tables["exact"]["content_hash"]) == 32  # exactly the cap
    assert tables["v"]["content_hash"] is None and tables["v"]["kind"] == "view"


def test_zero_turns_the_hash_off(tmp_path: Path) -> None:
    tables = profile_one(
        tmp_path, ["CREATE TABLE t AS SELECT 1 AS a", "CREATE TABLE e (a INTEGER)"],
        content_hash_rows=0,
    )  # fmt: skip
    assert tables["t"]["content_hash"] is None and tables["e"]["content_hash"] is None
    with pytest.raises(ValueError):
        ScanConfig(content_hash_rows=-1)


def test_the_default_cap_is_ten_thousand_rows(tmp_path: Path) -> None:
    tables = profile_one(
        tmp_path,
        [
            "CREATE TABLE at_cap AS SELECT i FROM range(10000) r(i)",
            "CREATE TABLE over_cap AS SELECT i FROM range(10001) r(i)",
        ],
    )
    assert len(tables["at_cap"]["content_hash"]) == 32
    assert tables["over_cap"]["content_hash"] is None


def test_the_hash_ignores_row_order_but_not_values_duplicates_or_structure(tmp_path: Path) -> None:
    rows = "(1, 'a'), (2, 'b'), (3, NULL)"
    base = hash_of(tmp_path / "a", [f"CREATE TABLE t AS SELECT * FROM (VALUES {rows}) v(x, y)"])
    reordered = hash_of(
        tmp_path / "b",
        ["CREATE TABLE t AS SELECT * FROM (VALUES (3, NULL), (2, 'b'), (1, 'a')) v(x, y)"],
    )
    assert base == reordered
    other_value = hash_of(
        tmp_path / "c",
        ["CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) v(x, y)"],
    )
    duplicated = hash_of(
        tmp_path / "d", [f"CREATE TABLE t AS SELECT * FROM (VALUES {rows}, (1, 'a')) v(x, y)"]
    )
    fewer = hash_of(
        tmp_path / "e", ["CREATE TABLE t AS SELECT * FROM (VALUES (1, 'a'), (2, 'b')) v(x, y)"]
    )
    assert len({base, other_value, duplicated, fewer}) == 4


def test_the_hash_depends_on_column_names_order_and_the_printed_values(tmp_path: Path) -> None:
    """Documented (TUNING content-hash): the rows are hashed as DuckDB prints them.

    Names and their order count; a type counts only through its printed form: INTEGER 1 and
    DOUBLE 1.0 differ, INTEGER 1 and VARCHAR '1' print alike (DuckDB leaves strings unquoted
    inside a printed row) and share a hash.
    """
    make = "CREATE TABLE t AS SELECT {} FROM range(3) r(i)"
    base = hash_of(tmp_path / "a", [make.format("i AS a, i * 2 AS b")])
    swapped = hash_of(tmp_path / "b", [make.format("i * 2 AS b, i AS a")])
    renamed = hash_of(tmp_path / "c", [make.format("i AS a, i * 2 AS c")])
    as_double = hash_of(tmp_path / "d", [make.format("i::DOUBLE AS a, i * 2 AS b")])
    assert len({base, swapped, renamed, as_double}) == 4
    wider = hash_of(tmp_path / "e", [make.format("i::BIGINT AS a, i * 2 AS b")])
    as_text = hash_of(tmp_path / "f", [make.format("i::VARCHAR AS a, i * 2 AS b")])
    assert wider == base == as_text


def test_awkward_values_and_names_are_hashed(tmp_path: Path) -> None:
    """A column named like the row alias, newlines, quotes, nested and large values."""
    tables = profile_one(
        tmp_path,
        [
            'CREATE TABLE t AS SELECT 1 AS t, \'x\ny\' AS "df_row", \'q"uote\' AS "a b"',
            "CREATE TABLE n AS SELECT [1, 2] AS l, {'k': 'v'} AS s, 1.5::DECIMAL(5,2) AS d, "
            "'nan'::DOUBLE AS f, from_hex('00ff') AS b, DATE '2026-01-01' AS dt",
        ],
    )
    assert len(tables["t"]["content_hash"]) == 32 and len(tables["n"]["content_hash"]) == 32
    assert tables["t"]["error"] is None and tables["n"]["error"] is None


def test_identical_content_in_two_files_has_one_hash(tmp_path: Path) -> None:
    make = [
        "CREATE TABLE config AS SELECT * FROM (VALUES ('lr', '0.01'), ('bs', '32')) v(key, value)"
    ]
    first = hash_of(tmp_path / "one", make, table="config")
    second = hash_of(tmp_path / "two", list(reversed(make)), table="config")
    assert first == second and first not in (None, EMPTY_HASH)


def test_a_failing_hash_is_not_a_table_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stats of the table stay when only the hash query fails (no table_error flag)."""
    from duckfleet_core import profiler

    real = profiler._content_hash

    def failing(con, qualified, row_count, config):
        # the same query on a table that does not exist raises like an unhashable table would
        return real(con, qualified + "_gone", row_count, config)

    monkeypatch.setattr(profiler, "_content_hash", failing)
    tables = profile_one(tmp_path, ["CREATE TABLE t AS SELECT 1 AS a, 2.5 AS b"])
    assert tables["t"]["content_hash"] is None and tables["t"]["error"] is None
    assert tables["t"]["columns"][0]["stats"]["non_null_count"] == 1


# ---------------------------------------------------------------- the version bump


def test_a_cache_from_version_one_is_profiled_again_in_full(
    fresh_demo_fleet: Path, cache_dir: Path
) -> None:
    """After the bump to 2 the next scan re-profiles every file once (and only once)."""
    config = ScanConfig(cache_dir=str(cache_dir), workers=1)
    first = run_scan(fresh_demo_fleet, config)
    assert first.files_profiled == 13
    store = ProfileStore(cache_dir, fresh_demo_fleet)
    for profile in store.load_all():  # rewrite the cache as an 0.3.x scan would have left it
        old = profile.model_copy(
            update={
                "profile_schema_version": 1,
                "tables": [t.model_copy(update={"content_hash": None}) for t in profile.tables],
            }
        )
        store.save(old)
    assert store.load_all() == []  # nothing of version 1 counts as a current profile
    second = run_scan(fresh_demo_fleet, config)
    assert (second.files_profiled, second.files_reused) == (13, 0)
    assert all(t.content_hash for p in store.load_all() for t in p.tables if t.kind == "table")
    third = run_scan(fresh_demo_fleet, config)
    assert (third.files_profiled, third.files_reused) == (3, 10)  # only the 3 failed files again
