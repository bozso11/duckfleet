"""The fleet connection (plan v0.4 Phase 1): aliases, attach, union views, lockdown, failures."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from duckfleet_core.fleet import (
    FALLBACK_LABEL_COLUMN,
    MAX_MEMBERS,
    FleetSelection,
    alias_base,
    export_fleet_query,
    fleet_tables,
    open_fleet,
    run_fleet_query,
)
from duckfleet_core.query import QueryConfig, QueryError

EVENTS_FILES = [
    "exp_01/run_01/metrics.duckdb", "exp_01/run_02/metrics.duckdb", "exp_01/run_03/metrics.duckdb",
    "exp_02/run_01/metrics.duckdb", "exp_02/run_02/metrics.duckdb", "exp_02/run_03/metrics.duckdb",
    "exp_03/run_01/metrics.duckdb", "exp_03/run_02/metrics.duckdb",
]  # fmt: skip
F1 = EVENTS_FILES[:3]
ENCRYPTED = "secure/encrypted.duckdb"
CORRUPT = "broken/corrupt.duckdb"
HIGHBIT = "broken/highbit.duckdb"
LEGACY = "legacy/old.db"
CROSS_JOIN = "select sum(a.i * b.i) from range(200000) a(i), range(200000) b(i)"


@pytest.fixture
def config(cache_dir: Path) -> QueryConfig:
    """A config whose temp root is below the test's cache folder."""
    return QueryConfig(temp_dir=str(cache_dir / "tmp" / str(os.getpid())), threads=2)


def paths_of(root: Path, *rel_paths: str) -> dict[str, str]:
    return {rel: str(root / rel) for rel in rel_paths}


def select(*rel_paths: str) -> FleetSelection:
    return FleetSelection(rel_paths=list(rel_paths))


def run(root: Path, config: QueryConfig, rel_paths: list[str], sql: str, **kwargs):
    return run_fleet_query(
        FleetSelection(rel_paths=rel_paths), paths_of(root, *rel_paths), sql, config, **kwargs
    )


def make_db(path: Path, *statements: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()
    return path


# ---------------------------------------------------------------- aliases


def test_the_alias_rule_on_the_example_of_the_plan() -> None:
    assert alias_base("exp_01/run_03/metrics.duckdb") == "exp_01_run_03_metrics"
    sel = select(*F1)
    assert sel.aliases() == {
        "exp_01_run_01_metrics": F1[0],
        "exp_01_run_02_metrics": F1[1],
        "exp_01_run_03_metrics": F1[2],
    }  # alias -> rel_path, in the given order


@pytest.mark.parametrize(
    ("rel_path", "alias"),
    [
        ("2026/Run 7/Data.DB", "d_2026_run_7_data"),  # leading digit, lower case, runs become _
        ("a--b__c//d.duckdb", "a_b_c_d"),
        ("é/日本.duckdb", "_"),  # non-ASCII characters are outside [a-z0-9]
        ("x" * 60 + ".duckdb", "x" * 48),  # cut to 48
        ("1" + "y" * 60 + ".duckdb", ("d_1" + "y" * 60)[:48]),  # cut after the prefix
        ("noext", "noext"),
        ("legacy/old.db", "legacy_old"),
    ],
)
def test_alias_base_cases(rel_path: str, alias: str) -> None:
    assert alias_base(rel_path) == alias


def test_alias_collisions_get_numbers_in_rel_path_order() -> None:
    rels = ["b_c/x.duckdb", "b-c/x.duckdb", "b c/x.duckdb", "other.duckdb"]
    aliases = FleetSelection(rel_paths=rels).aliases()
    # '-' < ' ' < '_'? no: sorted order of the rel_paths decides ('b c' < 'b-c' < 'b_c')
    assert {rel: alias for alias, rel in aliases.items()} == {
        "b c/x.duckdb": "b_c_x",
        "b-c/x.duckdb": "b_c_x_2",
        "b_c/x.duckdb": "b_c_x_3",
        "other.duckdb": "other",
    }
    # the same files get the same aliases whatever order they were chosen in
    again = FleetSelection(rel_paths=list(reversed(rels))).aliases()
    assert {rel: alias for alias, rel in again.items()} == {
        rel: alias for alias, rel in aliases.items()
    }
    assert list(again.values()) == list(reversed(rels))  # but the dict follows the given order


def test_reserved_names_and_existing_aliases_are_never_reused() -> None:
    aliases = FleetSelection(
        rel_paths=["memory.duckdb", "system.db", "temp/x.db", "temp_x.db", "a.db", "a_2.db"]
    ).aliases()
    by_rel = {rel: alias for alias, rel in aliases.items()}
    assert by_rel["memory.duckdb"] == "memory_2" and by_rel["system.db"] == "system_2"
    assert by_rel["temp/x.db"] == "temp_x" and by_rel["temp_x.db"] == "temp_x_2"
    assert by_rel["a.db"] == "a" and by_rel["a_2.db"] == "a_2"
    assert len(set(aliases)) == len(aliases)


def test_a_selection_has_one_to_fifty_distinct_members() -> None:
    assert (
        len(FleetSelection(rel_paths=[f"d{i}.duckdb" for i in range(MAX_MEMBERS)]).rel_paths) == 50
    )
    with pytest.raises(ValidationError, match="at most 50 databases"):
        FleetSelection(rel_paths=[f"d{i}.duckdb" for i in range(MAX_MEMBERS + 1)])
    with pytest.raises(ValidationError):
        FleetSelection(rel_paths=[])
    with pytest.raises(ValidationError, match="more than once: a.duckdb"):
        FleetSelection(rel_paths=["a.duckdb", "b.duckdb", "a.duckdb"])


# ---------------------------------------------------------------- the union views


def test_eight_files_attach_and_events_is_one_view(demo_fleet: Path, config: QueryConfig) -> None:
    sel = select(*EVENTS_FILES)
    with open_fleet(sel, paths_of(demo_fleet, *EVENTS_FILES), config) as fleet:
        assert list(fleet.aliases.values()) == EVENTS_FILES and not fleet.failures
        assert fleet.con is not None
        (total,) = fleet.con.execute("select count(*) from events").fetchone()
        assert total == 8_000
        labels = fleet.con.execute(
            "select database, count(*) from events group by 1 order by 1"
        ).fetchall()
        assert labels == [(rel, 1000) for rel in sorted(EVENTS_FILES)]  # the label is the rel_path
        # quality exists in exp_03 only: NULL elsewhere (columns filled by name)
        quality = dict(
            fleet.con.execute("select database, count(quality) from events group by 1").fetchall()
        )
        assert sorted(rel for rel, n in quality.items() if n) == EVENTS_FILES[6:]
        view = fleet.view("main", "events")
        assert view is not None and view.members == EVENTS_FILES and view.casts == []
        assert view.columns[0] == ("database", "VARCHAR") and view.label_column == "database"
        assert {name for name, _t in view.columns} == {
            "database",
            "ts",
            "value",
            "run_id",
            "quality",
        }
        assert dict(view.columns)["value"] in ("DOUBLE", "FLOAT")  # DOUBLE and FLOAT unify
        config_view = fleet.view("main", "config")
        assert config_view is not None and len(config_view.members) == 8
        notes = fleet.view("main", "notes")
        assert notes is not None and notes.members == EVENTS_FILES[6:]
        names = {(v.schema_name, v.table_name) for v in fleet.views}
        assert names == {("main", "events"), ("main", "config"), ("main", "notes")}


def test_the_per_file_aliases_stay_available(demo_fleet: Path, config: QueryConfig) -> None:
    result, summary = run(
        demo_fleet, config, F1,
        "select count(*) from exp_01_run_02_metrics.main.events",
    )  # fmt: skip
    assert result.rows == [(1000,)]
    assert set(summary.aliases.values()) == set(F1) and summary.failures == []


def test_fleet_tables_lists_the_members_of_every_table(
    demo_fleet: Path, config: QueryConfig
) -> None:
    rels = [*EVENTS_FILES, LEGACY]
    with open_fleet(select(*rels), paths_of(demo_fleet, *rels), config) as fleet:
        assert fleet.con is not None
        tables = fleet_tables(fleet.con, fleet.aliases)
    assert list(tables) == [
        ("main", "config"), ("main", "events"), ("main", "notes"), ("main", "samples"),
    ]  # fmt: skip
    assert tables[("main", "events")] == EVENTS_FILES
    assert tables[("main", "notes")] == EVENTS_FILES[6:]
    assert tables[("main", "samples")] == [LEGACY]


def test_a_query_across_files_returns_one_group_per_file(
    demo_fleet: Path, config: QueryConfig
) -> None:
    result, _summary = run(
        demo_fleet, config, F1,
        "select database, count(*) n from events group by 1 order by 1",
    )  # fmt: skip
    assert result.rows == [(rel, 1000) for rel in F1]
    assert result.columns == ["database", "n"]


def test_views_false_attaches_only(demo_fleet: Path, config: QueryConfig) -> None:
    with open_fleet(select(*F1), paths_of(demo_fleet, *F1), config, views=False) as fleet:
        assert fleet.views == [] and fleet.con is not None
        with pytest.raises(duckdb.CatalogException):
            fleet.con.execute("select * from events")
        assert fleet.con.execute(
            "select count(*) from exp_01_run_01_metrics.main.events"
        ).fetchone() == (1000,)


# ---------------------------------------------------------------- members that fail


def test_a_member_held_by_a_writer_is_a_locked_failure_and_the_rest_work(
    demo_fleet: Path, config: QueryConfig, tmp_path: Path, hold_open
) -> None:
    held = tmp_path / "held" / "metrics.duckdb"
    held.parent.mkdir()
    shutil.copy(demo_fleet / F1[0], held)
    paths = {F1[1]: str(demo_fleet / F1[1]), "held/metrics.duckdb": str(held)}
    with hold_open(held):
        result, summary = run_fleet_query(
            FleetSelection(rel_paths=["held/metrics.duckdb", F1[1]]),
            paths,
            "select database, count(*) from events group by 1",
            config,
        )
    assert result.rows == [(F1[1], 1000)]
    (failure,) = summary.failures
    assert (failure.rel_path, failure.alias, failure.kind) == (
        "held/metrics.duckdb", "held_metrics", "locked",
    )  # fmt: skip
    assert failure.message
    assert list(summary.aliases.values()) == [F1[1]]


def test_unreadable_members_are_classified_and_skipped(
    demo_fleet: Path, config: QueryConfig, tmp_path: Path
) -> None:
    rels = [F1[0], ENCRYPTED, CORRUPT, HIGHBIT, "gone/missing.duckdb"]
    paths = paths_of(demo_fleet, F1[0], ENCRYPTED, CORRUPT, HIGHBIT)
    paths["gone/missing.duckdb"] = str(tmp_path / "gone" / "missing.duckdb")
    result, summary = run_fleet_query(
        FleetSelection(rel_paths=rels), paths, "select count(*) from events", config
    )
    assert result.rows == [(1000,)]
    kinds = {f.rel_path: f.kind for f in summary.failures}
    assert kinds == {
        ENCRYPTED: "encrypted",
        CORRUPT: "corrupt",
        HIGHBIT: "corrupt",
        "gone/missing.duckdb": "missing",
    }  # fmt: skip
    assert [f.rel_path for f in summary.failures] == [
        ENCRYPTED,
        CORRUPT,
        HIGHBIT,
        "gone/missing.duckdb",
    ]
    assert not (tmp_path / "gone").exists()  # a missing file is not created by ATTACH


def test_no_attachable_member_is_an_error(demo_fleet: Path, config: QueryConfig) -> None:
    with pytest.raises(QueryError) as caught:
        run(demo_fleet, config, [ENCRYPTED, CORRUPT], "select 1")
    assert caught.value.kind == "other"
    assert "none of the databases could be attached" in caught.value.message
    assert "encrypted" in caught.value.message


def test_an_unknown_member_is_a_sql_error(demo_fleet: Path, config: QueryConfig) -> None:
    with pytest.raises(QueryError) as caught:
        run_fleet_query(
            select("nope.duckdb", F1[0]), paths_of(demo_fleet, F1[0]), "select 1", config
        )
    assert caught.value.kind == "sql" and "nope.duckdb" in caught.value.message


# ---------------------------------------------------------------- the lockdown


@pytest.mark.parametrize(
    "sql",
    [
        "COPY events TO 'duckfleet_should_not_exist.csv'",
        "ATTACH 'duckfleet_other.duckdb' AS other",
        "SELECT * FROM read_csv('duckfleet_nothing.csv')",
        "SELECT * FROM read_parquet('duckfleet_nothing.parquet')",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SET threads = 8",
        "SET enable_external_access = true",
        "SET memory_limit = '99GB'",
        "SET lock_configuration = false",
        "EXPORT DATABASE 'duckfleet_export_dir'",
    ],
)
def test_after_the_lockdown_files_extensions_and_settings_are_blocked(
    demo_fleet: Path, config: QueryConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql: str
) -> None:
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    with pytest.raises(QueryError) as caught:
        run(demo_fleet, config, F1, sql)
    assert caught.value.kind in ("blocked", "read_only"), caught.value
    if sql.startswith(("COPY", "ATTACH", "SELECT * FROM read", "LOAD", "INSTALL", "SET")):
        assert caught.value.kind == "blocked", caught.value
    assert list(work.iterdir()) == []  # nothing was written


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO exp_01_run_01_metrics.main.events VALUES (NULL, 1.0, 1)",
        "UPDATE exp_01_run_01_metrics.main.events SET value = 0",
        "DELETE FROM exp_01_run_01_metrics.main.events",
        "DROP TABLE exp_01_run_01_metrics.main.events",
        "CREATE TABLE exp_01_run_01_metrics.main.fresh (i INTEGER)",
        "INSERT INTO events VALUES ('x', NULL, 1.0, 1, NULL)",
        "UPDATE events SET value = 0",
        "DELETE FROM events",
    ],
)
def test_writes_are_read_only_errors_and_nothing_changes(
    demo_fleet: Path, config: QueryConfig, sql: str
) -> None:
    before = {p: p.stat().st_mtime_ns for p in demo_fleet.rglob("*") if p.is_file()}
    with pytest.raises(QueryError) as caught:
        run(demo_fleet, config, F1, sql)
    assert caught.value.kind == "read_only", caught.value
    assert {p: p.stat().st_mtime_ns for p in demo_fleet.rglob("*") if p.is_file()} == before
    assert run(demo_fleet, config, F1, "select count(*) from events")[0].rows == [(3000,)]


def test_a_temp_table_lives_and_dies_with_the_connection(
    demo_fleet: Path, config: QueryConfig
) -> None:
    both = run(
        demo_fleet, config, F1, "create temp table z as select 41 + 1 as a; select a from z"
    )[0]
    assert both.rows == [(42,)]
    with pytest.raises(QueryError) as caught:
        run(demo_fleet, config, F1, "select a from z")
    assert caught.value.kind == "sql"


def test_nothing_is_written_beside_any_member(demo_fleet: Path, config: QueryConfig) -> None:
    """Spill files of a fleet query go to the fleet's own temp folder, not beside the files."""

    def listing() -> list[str]:
        return sorted(str(p) for p in demo_fleet.rglob("*"))

    before = listing()
    small = config.model_copy(update={"memory_limit_mb": 64, "timeout_s": 120})
    sql = "select count(*) from (select hash(i) h from range(20000000) t(i) order by h)"
    assert run(demo_fleet, small, F1, sql)[0].rows == [(20_000_000,)]
    assert listing() == before


# ---------------------------------------------------------------- the connection itself


def test_every_fleet_connection_has_its_own_temp_folder_that_is_removed(
    demo_fleet: Path, config: QueryConfig
) -> None:
    root = Path(config.resolved_temp_dir())
    sel, paths = select(*F1), paths_of(demo_fleet, *F1)
    with open_fleet(sel, paths, config) as one, open_fleet(sel, paths, config) as two:
        assert one.con is not None and two.con is not None
        folders = []
        for fleet in (one, two):
            (setting,) = fleet.con.execute("select current_setting('temp_directory')").fetchone()
            folders.append(Path(setting))
        assert folders[0] != folders[1] and all(f.is_dir() for f in folders)
        assert all(f.parent == root and f.name.startswith("fleet-") for f in folders)
    assert not any(f.exists() for f in folders)  # removed when the connection closed


def test_the_connection_is_closed_after_an_error_and_the_next_call_works(
    demo_fleet: Path, config: QueryConfig
) -> None:
    root = Path(config.resolved_temp_dir())
    with pytest.raises(QueryError) as caught:
        run(demo_fleet, config, F1, "selec 1")
    assert caught.value.kind == "sql"
    assert not list(root.glob("fleet-*"))
    assert run(demo_fleet, config, F1, "select 1")[0].rows == [(1,)]
    # a member can be opened on its own with another config afterwards: no connection is left
    other = QueryConfig(threads=1, memory_limit_mb=700, temp_dir=config.temp_dir)
    assert run(demo_fleet, other, [F1[0]], "select count(*) from events")[0].rows == [(1000,)]


def test_the_timeout_applies_and_the_next_query_works(
    demo_fleet: Path, config: QueryConfig
) -> None:
    quick = config.model_copy(update={"timeout_s": 0.5})
    with pytest.raises(QueryError) as caught:
        run(demo_fleet, quick, F1, CROSS_JOIN)
    assert caught.value.kind == "timeout"
    assert run(demo_fleet, config, F1, "select 7")[0].rows == [(7,)]


def test_max_rows_truncates(demo_fleet: Path, config: QueryConfig) -> None:
    result, _summary = run(demo_fleet, config, F1, "select * from events", max_rows=5)
    assert len(result.rows) == 5 and result.truncated
    assert not run(demo_fleet, config, F1, "select * from events", max_rows=3000)[0].truncated


def test_a_member_open_elsewhere_in_the_process_can_be_attached(
    demo_fleet: Path, config: QueryConfig
) -> None:
    from duckfleet_core.query import open_readonly

    with open_readonly(demo_fleet / F1[0], config) as direct:
        assert direct.execute("select count(*) from events").fetchone() == (1000,)
        result, summary = run(demo_fleet, config, F1[:2], "select count(*) from events")
    assert result.rows == [(2000,)] and not summary.failures


def test_two_fleets_spilling_at_once_in_one_process_survive(tmp_path: Path) -> None:
    """Fleet connections are DuckDB instances too: each needs a temp folder of its own (W-01)."""
    script = Path(__file__).with_name("_spill_threads.py")
    done = subprocess.run(
        [sys.executable, str(script), str(tmp_path), "fleet"],
        capture_output=True,
        text=True,
        timeout=280,
    )
    detail = (done.returncode, done.stdout, done.stderr)
    assert done.returncode == 0 and done.stdout.strip() == "ok", detail


# ---------------------------------------------------------------- type conflicts and names


def test_columns_with_types_that_cannot_be_unified_are_cast_to_text(
    tmp_path: Path, config: QueryConfig
) -> None:
    """The view is created whatever the types; reading it would fail (D-044), so cast up front."""
    one = make_db(
        tmp_path / "f" / "one.duckdb",
        "CREATE TABLE m AS SELECT 1 AS value, 7 AS n, DATE '2026-01-01' AS d, 'a' AS only_one",
    )
    two = make_db(
        tmp_path / "f" / "two.duckdb",
        "CREATE TABLE m AS SELECT 'x' AS value, 2.5::DOUBLE AS n, "
        "TIMESTAMP '2026-01-02 03:04:05' AS d",
    )
    rels = ["f/one.duckdb", "f/two.duckdb"]
    paths = {"f/one.duckdb": str(one), "f/two.duckdb": str(two)}
    with open_fleet(FleetSelection(rel_paths=rels), paths, config) as fleet:
        view = fleet.view("main", "m")
        assert view is not None
        assert view.casts == ["value"]  # INTEGER vs VARCHAR; numeric and temporal pairs unify
        types = dict(view.columns)
        assert types["value"] == "VARCHAR" and types["n"] == "DOUBLE" and types["d"] == "TIMESTAMP"
        assert fleet.con is not None
        rows = fleet.con.execute(
            "select database, value, n, d, only_one from m order by 1"
        ).fetchall()
    assert rows[0][:2] == ("f/one.duckdb", "1") and rows[1][:2] == ("f/two.duckdb", "x")
    assert rows[0][2] == 7.0 and rows[1][2] == 2.5 and rows[0][4] == "a" and rows[1][4] is None


@pytest.mark.parametrize(
    ("first", "second", "cast"),
    [
        ("1", "'a'", True),
        ("1", "[1, 2]", True),
        ("[1]", "['a']", True),
        ("{'a': 1}", "{'a': 'x'}", True),
        ("true", "1", True),
        ("1", "2::BIGINT", False),
        ("1", "1.5::FLOAT", False),
        ("1::HUGEINT", "1.25::DECIMAL(10,2)", False),
        ("DATE '2026-01-01'", "TIMESTAMP '2026-01-01 00:00:00'", False),
        ("TIMESTAMP '2026-01-01 00:00:00'", "TIMESTAMPTZ '2026-01-01 00:00:00+00'", False),
        ("'a'", "'b'", False),
        ("[1]", "[2]", False),
    ],
)
def test_the_cast_rule_matches_what_duckdb_can_read(
    tmp_path: Path, config: QueryConfig, first: str, second: str, cast: bool
) -> None:
    a = make_db(tmp_path / "a.duckdb", f"CREATE TABLE t AS SELECT {first} AS v")
    b = make_db(tmp_path / "b.duckdb", f"CREATE TABLE t AS SELECT {second} AS v")
    rels = ["a.duckdb", "b.duckdb"]
    with open_fleet(
        FleetSelection(rel_paths=rels), {"a.duckdb": str(a), "b.duckdb": str(b)}, config
    ) as fleet:
        view = fleet.view("main", "t")
        assert view is not None and (view.casts == ["v"]) is cast
        assert fleet.con is not None
        assert len(fleet.con.execute("select * from t").fetchall()) == 2  # readable either way


def test_a_member_column_named_database_moves_the_label_column(
    tmp_path: Path, config: QueryConfig
) -> None:
    """The union binder refuses two columns of one name, so that view labels with another name."""
    a = make_db(tmp_path / "a.duckdb", "CREATE TABLE t AS SELECT 1 AS \"Database\", 2 AS v",
                "CREATE TABLE u AS SELECT 3 AS w")  # fmt: skip
    b = make_db(tmp_path / "b.duckdb", "CREATE TABLE t AS SELECT 4 AS v",
                "CREATE TABLE u AS SELECT 5 AS w")  # fmt: skip
    paths = {"a.duckdb": str(a), "b.duckdb": str(b)}
    with open_fleet(FleetSelection(rel_paths=list(paths)), paths, config) as fleet:
        t, u = fleet.view("main", "t"), fleet.view("main", "u")
        assert t is not None and u is not None
        assert t.label_column == FALLBACK_LABEL_COLUMN and u.label_column == "database"
        assert [name for name, _t in t.columns][:3] == [FALLBACK_LABEL_COLUMN, "Database", "v"]
        assert fleet.con is not None
        rows = fleet.con.execute(f"select {FALLBACK_LABEL_COLUMN}, v from t order by 1").fetchall()
        assert rows == [("a.duckdb", 2), ("b.duckdb", 4)]
        assert fleet.con.execute("select database from u order by 1").fetchall() == [
            ("a.duckdb",), ("b.duckdb",),
        ]  # fmt: skip


def test_other_schemas_and_member_views_become_union_views(
    tmp_path: Path, config: QueryConfig
) -> None:
    a = make_db(
        tmp_path / "a.duckdb",
        "CREATE SCHEMA s", "CREATE TABLE s.t AS SELECT 1 AS v", "CREATE TABLE t AS SELECT 10 AS v",
        "CREATE VIEW vv AS SELECT v + 1 AS w FROM t",
    )  # fmt: skip
    b = make_db(
        tmp_path / "b.duckdb",
        "CREATE SCHEMA s", "CREATE TABLE s.t AS SELECT 2 AS v", "CREATE VIEW vv AS SELECT 5 AS w",
    )  # fmt: skip
    paths = {"a.duckdb": str(a), "b.duckdb": str(b)}
    with open_fleet(FleetSelection(rel_paths=list(paths)), paths, config) as fleet:
        names = {(v.schema_name, v.table_name): v.members for v in fleet.views}
        assert names == {
            ("s", "t"): ["a.duckdb", "b.duckdb"],
            ("main", "t"): ["a.duckdb"],
            ("main", "vv"): ["a.duckdb", "b.duckdb"],
        }
        assert fleet.con is not None
        assert fleet.con.execute("select database, v from s.t order by 1").fetchall() == [
            ("a.duckdb", 1), ("b.duckdb", 2),
        ]  # fmt: skip
        assert fleet.con.execute("select database, w from vv order by 1").fetchall() == [
            ("a.duckdb", 11), ("b.duckdb", 5),
        ]  # fmt: skip


def test_hostile_names_are_quoted(tmp_path: Path, config: QueryConfig) -> None:
    weird_schema, weird_table = 'we"ird s', 't;x "q" ü'
    a = make_db(
        tmp_path / "it's.duckdb",
        'CREATE SCHEMA "we""ird s"',
        'CREATE TABLE "we""ird s"."t;x ""q"" ü" AS SELECT 1 AS "a b", 2 AS "é"',
    )
    b = make_db(
        tmp_path / "né.duckdb",
        'CREATE SCHEMA "we""ird s"',
        'CREATE TABLE "we""ird s"."t;x ""q"" ü" AS SELECT 3 AS "a b"',
    )
    paths = {"it's.duckdb": str(a), "né.duckdb": str(b)}
    with open_fleet(FleetSelection(rel_paths=list(paths)), paths, config) as fleet:
        view = fleet.view(weird_schema, weird_table)
        assert view is not None and view.members == ["it's.duckdb", "né.duckdb"]
        assert fleet.con is not None
        rows = fleet.con.execute(
            'select database, "a b", "é" from "we""ird s"."t;x ""q"" ü" order by 1'
        ).fetchall()
    assert rows == [("it's.duckdb", 1, 2), ("né.duckdb", 3, None)]


def test_export_fleet_query_builds_csv_and_parquet(demo_fleet: Path, config: QueryConfig) -> None:
    pytest.importorskip("pyarrow")
    sel, paths = select(*F1), paths_of(demo_fleet, *F1)
    data, rows, truncated, summary = export_fleet_query(
        sel, paths, "select database, count(*) n from events group by 1 order by 1", "csv", config
    )
    assert rows == 3 and not truncated and summary.failures == []
    assert data.decode().splitlines()[0] == '"database","n"'
    data, rows, truncated, _summary = export_fleet_query(
        sel, paths, "select * from events", "parquet", config, max_rows=10
    )
    assert rows == 10 and truncated and data[:4] == b"PAR1"
    with pytest.raises(ValueError):
        export_fleet_query(sel, paths, "select 1", "xlsx", config)  # type: ignore[arg-type]
    with pytest.raises(QueryError) as caught:
        export_fleet_query(sel, paths, "selec 1", "csv", config)
    assert caught.value.kind == "sql"
