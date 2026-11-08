"""The UI fixes of the v0.4 review (0.4.1): X-04, X-08 to X-12, with Streamlit's AppTest."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.demo import make_demo_fleet
from duckfleet_core.fleet import FleetSummary, UnionView
from duckfleet_ui import catalog_access as access
from duckfleet_ui import scope as scopes
from duckfleet_ui.views import overview, sql

OVERVIEW_APP = str(Path(__file__).with_name("_overview_app.py"))
SQL_APP = str(Path(__file__).with_name("_sql_app.py"))
PLOT_APP = str(Path(__file__).with_name("_plot_app.py"))
PAGES_APP = str(Path(__file__).with_name("_pages_app.py"))
EXP_1 = [f"exp_01/run_0{r}/metrics.duckdb" for r in (1, 2, 3)]
EXP_3 = [f"exp_03/run_0{r}/metrics.duckdb" for r in (1, 2)]
ORPHANS = ["a.duckdb", "b.duckdb", "c.duckdb"]


def make_db(path: Path, *statements: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()


def point_at(monkeypatch: pytest.MonkeyPatch, fleet: Path, cache: Path) -> None:
    monkeypatch.setenv("DUCKFLEET_ROOT", str(fleet))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))


def ok(at: AppTest) -> None:
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def start(app: str, rel_path: str, selected: list[str], **state) -> AppTest:
    """The page in scope several databases with ``selected`` chosen."""
    at = AppTest.from_file(app, default_timeout=60)
    at.session_state["selected_rel_path"] = rel_path
    at.session_state["scope_mode"] = "many"
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    at.multiselect(key="scope_rel_paths").set_value(selected).run()
    return at


@pytest.fixture(scope="module")
def orphan_fleet(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Three files with ``vw AS SELECT v FROM base``; ``base`` is gone in ``c.duckdb`` (X-03)."""
    base = tmp_path_factory.mktemp("review041_orphans")
    fleet = base / "fleet"
    for index, rel in enumerate(ORPHANS, start=1):
        make_db(
            fleet / rel,
            f"CREATE TABLE base AS SELECT {index} AS v",
            "CREATE VIEW vw AS SELECT v FROM base",
        )
    make_db(fleet / "c.duckdb", "DROP TABLE base")
    cache = base / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    return fleet, cache


@pytest.fixture(scope="module")
def own_label_fleet(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Two files whose ``events`` has a column named ``database`` of its own (D-044)."""
    base = tmp_path_factory.mktemp("review041_label")
    fleet = base / "fleet"
    for rel, value in (("a.duckdb", 1), ("b.duckdb", 2)):
        make_db(fleet / rel, f"CREATE TABLE events AS SELECT 'own' AS database, {value} AS v")
    cache = base / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    return fleet, cache


@pytest.fixture(scope="module")
def demo_catalog(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("review041_demo")
    fleet = make_demo_fleet(base / "fleet")
    cache = base / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    return fleet, cache


# ---------------------------------------------------------------- X-04: an older catalog


def test_the_overview_of_an_older_catalog_shows_an_info_and_the_rest_of_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet = make_demo_fleet(tmp_path / "fleet")
    cache = tmp_path / "cache"
    summary = run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    con = duckdb.connect(str(summary.catalog_path))
    try:
        con.execute("ALTER TABLE tables DROP COLUMN content_hash")  # as 0.3.x wrote it
        con.execute("CHECKPOINT")
    finally:
        con.close()
    point_at(monkeypatch, fleet, cache)
    at = AppTest.from_file(OVERVIEW_APP, default_timeout=60).run()
    ok(at)  # no exception: the page used to die after the section header
    assert [s.value for s in at.subheader] == [
        "Fleet", "Schema families", "Table variants", "Identical content", "Compare",
        "Time windows", "Health and errors", "Rejected files",
    ]  # fmt: skip
    infos = [i.value for i in at.info]
    assert any(i.startswith("older catalog:") and "`duckfleet scan`" in i for i in infos)
    assert {m.label for m in at.metric} >= {"Candidates", "OK", "Strict families"}
    assert not [d for d in at.dataframe if "databases per variant" in list(d.value.columns)]


# ---------------------------------------------------------------- X-12: the cap of the scan


def test_the_caption_and_the_empty_state_use_the_cap_of_the_scan() -> None:
    assert access.content_hash_cap({"config_json": '{"content_hash_rows": 500}'}) == 500
    assert access.content_hash_cap({"config_json": '{"content_hash_rows": 0}'}) == 0
    assert access.content_hash_cap({"config_json": "{}"}) is None
    assert access.content_hash_cap({"config_json": None}) is None
    assert access.content_hash_cap({}) is None
    assert "up to 10,000 rows" in overview.identical_caption(10_000)
    assert "up to 25 rows" in overview.identical_caption(25)
    assert "up to" not in overview.identical_caption(None)
    assert "up to 25 rows" in overview.identical_empty(25)
    assert "switched off" in overview.identical_empty(0)
    for cap in (None, 0, 25):
        assert "scan again" not in overview.identical_empty(cap).lower()


def test_the_overview_says_what_the_scan_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet = tmp_path / "fleet"
    make_db(fleet / "a.duckdb", "CREATE TABLE big AS SELECT i FROM range(100) r(i)")
    make_db(fleet / "b.duckdb", "CREATE TABLE big AS SELECT i FROM range(100) r(i)")
    for cap in (10, 0):  # every table above the cap; the comparison switched off
        cache = tmp_path / f"cache{cap}"
        run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache), content_hash_rows=cap))
        point_at(monkeypatch, fleet, cache)
        at = AppTest.from_file(OVERVIEW_APP, default_timeout=60).run()
        ok(at)
        captions = " | ".join(str(c.value) for c in at.caption)
        infos = " | ".join(i.value for i in at.info)
        if cap:
            assert f"Small tables (up to {cap:,} rows) are compared" in captions
            assert f"none of the base tables has up to {cap:,} rows" in infos
        else:
            assert "switched off in the last scan" in infos
        assert "scan again" not in infos.lower()


def test_the_default_caption_names_ten_thousand_rows(
    demo_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    point_at(monkeypatch, *demo_catalog)
    at = AppTest.from_file(OVERVIEW_APP, default_timeout=60).run()
    ok(at)
    assert any("Small tables (up to 10,000 rows) are compared" in str(c.value) for c in at.caption)


# ---------------------------------------------------------------- X-08: view failures on the pages


def test_the_view_warning_text() -> None:
    assert scopes.view_warning([]) is None
    one = scopes.view_warning([("main.vw", "c.duckdb: Catalog Error:\n  no table base")])
    assert one is not None and one.startswith("1 view could not be used;")
    assert "- main.vw: c.duckdb: Catalog Error: no table base" in one  # one line per failure
    two = scopes.view_warning([("main.vw", "a"), ("s.t", "b")])
    assert two is not None and two.startswith("2 views could not be used;")
    long = scopes.view_warning([("main.vw", "x" * 1000)])
    assert long is not None and len(long) < 400


def test_the_sql_page_warns_about_a_member_view_that_does_not_bind(
    orphan_fleet, monkeypatch: pytest.MonkeyPatch
) -> None:
    point_at(monkeypatch, *orphan_fleet)
    at = start(SQL_APP, "a.duckdb", ORPHANS)
    ok(at)
    warnings = [w.value for w in at.warning]
    (warning,) = [w for w in warnings if "view could not be used" in w]
    assert "main.vw: c.duckdb:" in warning and "base" in warning
    at.text_area(key="sql_text").set_value("select database, v from vw order by 1")
    at.button[0].click()
    at.run()
    ok(at)
    assert [list(r) for _i, r in at.dataframe[-1].value.iterrows()] == [
        ["a.duckdb", 1], ["b.duckdb", 2],
    ]  # fmt: skip  (nothing under c.duckdb)
    assert len([w for w in at.warning if "view could not be used" in w.value]) == 2  # + the result


def test_the_plot_page_warns_and_lists_the_surviving_union_views(
    orphan_fleet, monkeypatch: pytest.MonkeyPatch
) -> None:
    point_at(monkeypatch, *orphan_fleet)
    at = start(PLOT_APP, "a.duckdb", ORPHANS)
    ok(at)
    assert any("1 view could not be used" in w.value and "c.duckdb" in w.value for w in at.warning)
    assert at.selectbox(key="selected_table").options == ["main.base", "main.vw"]


# ---------------------------------------------------------------- X-09: Open in SQL


def test_open_in_sql_works_on_the_one_database_whatever_the_scope(
    demo_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X-09: with the scope on several databases the template ran on the union view."""
    point_at(monkeypatch, *demo_catalog)
    at = AppTest.from_file(PAGES_APP, default_timeout=60)
    at.session_state["page"] = "database"
    at.session_state["selected_rel_path"] = EXP_1[0]
    at.session_state["scope_mode"] = "many"
    at.run()
    ok(at)
    at.session_state["selected_table"] = "main.events"
    at.run()
    next(b for b in at.button if b.label == "Open in SQL").click()
    at.run()
    assert at.session_state["scope_mode"] == "one"
    assert at.session_state["sql_text"] == 'SELECT * FROM "main"."events" LIMIT 100'
    at.session_state["page"] = "sql"
    at.run()
    ok(at)
    assert at.radio(key="scope_mode").value == "one"
    at.button[0].click()
    at.run()
    ok(at)
    frame = at.dataframe[-1].value
    assert len(frame) == 100 and "database" not in frame.columns  # the file's table, not the union


# ---------------------------------------------------------------- X-10: the label column


def summary_of(*views: UnionView) -> FleetSummary:
    return FleetSummary({}, [], list(views))


def view(schema: str, table: str, label: str = "database") -> UnionView:
    return UnionView(schema, table, ["a"], [], [(label, "VARCHAR")], label)


def test_the_placeholder_follows_the_label_column_and_the_view() -> None:
    assert sql.placeholder_many(None) == sql.PLACEHOLDER_MANY
    assert sql.placeholder_many(summary_of()) == sql.PLACEHOLDER_MANY
    assert sql.placeholder_many(summary_of(view("main", "events"))) == sql.PLACEHOLDER_MANY
    labelled = summary_of(view("main", "a"), view("main", "events", "duckfleet_database"))
    assert sql.placeholder_many(labelled) == (
        "SELECT duckfleet_database, count(*) FROM events GROUP BY 1"
    )
    assert sql.placeholder_many(summary_of(view("main", "runs"))) == (
        "SELECT database, count(*) FROM runs GROUP BY 1"
    )  # no events: the first view
    assert sql.placeholder_many(summary_of(view("lab", "my table"))) == (
        'SELECT database, count(*) FROM lab."my table" GROUP BY 1'
    )
    assert "a `database` column" in sql.label_text(summary_of(view("main", "events")))
    mixed = sql.label_text(summary_of(view("main", "a"), view("main", "b", "duckfleet_database")))
    assert "`database`" in mixed and "`duckfleet_database`" in mixed


def test_the_sql_page_names_the_fallback_label_column_in_the_expander_and_placeholder(
    own_label_fleet, demo_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    point_at(monkeypatch, *own_label_fleet)
    at = start(SQL_APP, "a.duckdb", ["a.duckdb", "b.duckdb"])
    ok(at)
    assert at.text_area(key="sql_text").proto.placeholder == (
        "SELECT duckfleet_database, count(*) FROM events GROUP BY 1"
    )
    text = " ".join(m.value for m in at.markdown)
    assert "with a `duckfleet_database` column" in text and "with a `database` column" not in text
    at.text_area(key="sql_text").set_value(at.text_area(key="sql_text").proto.placeholder)
    at.button[0].click()
    at.run()
    ok(at)
    frame = at.dataframe[-1].value
    assert sorted(frame["duckfleet_database"]) == ["a.duckdb", "b.duckdb"]
    # the demo's views still say `database`
    point_at(monkeypatch, *demo_catalog)
    plain = start(SQL_APP, EXP_1[0], EXP_1)
    assert plain.text_area(key="sql_text").proto.placeholder == sql.PLACEHOLDER_MANY
    assert "with a `database` column" in " ".join(m.value for m in plain.markdown)


# ---------------------------------------------------------------- X-11: the caption counts members


def test_the_plot_caption_counts_the_databases_that_hold_the_table(
    demo_catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three databases chosen, ``notes`` is in two of them."""
    point_at(monkeypatch, *demo_catalog)
    at = start(PLOT_APP, EXP_1[0], [EXP_1[0], *EXP_3], plot_kind="bar")
    at.selectbox(key="selected_table").select("main.notes").run()
    ok(at)
    at.button[0].click()
    at.run()
    ok(at)
    captions = " | ".join(str(c.value) for c in at.caption)
    assert "2 databases  |" in captions and "3 databases  |" not in captions
    # a table in every chosen database says so
    at.selectbox(key="selected_table").select("main.events").run()
    at.button[0].click()
    at.run()
    assert "3 databases  |" in " | ".join(str(c.value) for c in at.caption)
