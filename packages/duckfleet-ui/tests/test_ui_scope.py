"""Scope on the SQL and Plot pages (plan v0.4 Phase 4), with Streamlit's AppTest.

The pages run through the wrappers ``_sql_app.py`` and ``_plot_app.py`` against a scanned demo
fleet; the hand-off from the Database page runs through ``_pages_app.py`` (one session state
across pages).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from duckfleet_core.catalog import CatalogReader
from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.demo import make_demo_fleet
from duckfleet_ui import scope as scopes

SQL_APP = str(Path(__file__).with_name("_sql_app.py"))
PLOT_APP = str(Path(__file__).with_name("_plot_app.py"))
PAGES_APP = str(Path(__file__).with_name("_pages_app.py"))
EXP_1 = [f"exp_01/run_0{r}/metrics.duckdb" for r in (1, 2, 3)]
EXP_2 = [f"exp_02/run_0{r}/metrics.duckdb" for r in (1, 2, 3)]
EXP_3 = [f"exp_03/run_0{r}/metrics.duckdb" for r in (1, 2)]
SIX = [*EXP_1, *EXP_2]
PLACEHOLDER = "SELECT database, count(*) FROM events GROUP BY 1"


@pytest.fixture(scope="module")
def scanned(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("scopepage")
    fleet = make_demo_fleet(base / "fleet é")
    cache = base / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    return fleet, cache


@pytest.fixture
def pointed_at(scanned: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
    fleet, cache = scanned
    monkeypatch.setenv("DUCKFLEET_ROOT", str(fleet))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))
    return fleet, cache


def family_of(scanned: tuple[Path, Path], rel_path: str) -> list[str]:
    fleet, cache = scanned
    reader = CatalogReader(next(cache.glob("roots/*/catalog.duckdb")))
    row = reader.database(rel_path)
    assert row is not None and row["family_strict"]
    return [m["rel_path"] for m in reader.family_members("strict", row["family_strict"])]


def start(app: str, rel_path: str = EXP_1[0], mode: str = "many", **state) -> AppTest:
    at = AppTest.from_file(app, default_timeout=60)
    at.session_state["selected_rel_path"] = rel_path
    at.session_state["scope_mode"] = mode
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def ok(at: AppTest) -> None:
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def captions(at: AppTest) -> str:
    return " | ".join(str(c.value) for c in at.caption)


# ---------------------------------------------------------------- the scope control


def test_the_scope_defaults_to_one_database_and_changes_nothing(pointed_at) -> None:
    at = AppTest.from_file(SQL_APP, default_timeout=60)
    at.session_state["selected_rel_path"] = EXP_1[0]
    at.run()
    ok(at)
    assert at.radio(key="scope_mode").value == "one"
    assert [o for o in at.radio(key="scope_mode").options] == ["One database", "Several databases"]
    assert not [m for m in at.multiselect if m.key == "scope_rel_paths"]  # no selection in "one"
    assert at.text_area(key="sql_text").proto.placeholder == "SELECT * FROM ..."


def test_the_selection_defaults_to_the_strict_family_of_the_selected_database(
    pointed_at, scanned
) -> None:
    at = start(SQL_APP)
    ok(at)
    family = family_of(scanned, EXP_1[0])
    assert sorted(family) == EXP_1  # exp_01 has DOUBLE values, exp_02 FLOAT: two strict families
    chosen = at.multiselect(key="scope_rel_paths")
    assert sorted(chosen.value) == sorted(family) and len(chosen.value) == 3
    assert "3 of 10 readable databases selected." in captions(at)  # failed files are not offered
    assert "secure/encrypted.duckdb" not in chosen.options and EXP_3[0] in chosen.options
    assert at.session_state["scope_note"] is None


def test_a_database_without_a_family_is_its_own_default(pointed_at) -> None:
    at = start(SQL_APP, rel_path="legacy/old.db")
    ok(at)
    assert at.multiselect(key="scope_rel_paths").value == ["legacy/old.db"]


def test_the_selection_follows_the_selected_database_and_keeps_edits(pointed_at, scanned) -> None:
    at = start(SQL_APP)
    at.multiselect(key="scope_rel_paths").set_value(EXP_1[:2]).run()
    assert at.multiselect(key="scope_rel_paths").value == EXP_1[:2]  # an edit stays
    at.run()
    assert at.multiselect(key="scope_rel_paths").value == EXP_1[:2]
    at.selectbox(key="selected_rel_path").select(EXP_3[0]).run()
    assert sorted(at.multiselect(key="scope_rel_paths").value) == sorted(
        family_of(scanned, EXP_3[0])
    )


def test_the_family_selectbox_fills_the_selection(pointed_at, scanned) -> None:
    at = start(SQL_APP)
    family_ids = list(at.selectbox(key="scope_family").options)
    assert len(family_ids) == 4  # F1 .. F4 of the demo (3, 3, 2 and 1 members)
    target = next(f for f in family_ids if "(2)" in f)
    at.selectbox(key="scope_family").select_index(family_ids.index(target)).run()
    ok(at)
    assert sorted(at.multiselect(key="scope_rel_paths").value) == sorted(EXP_3)
    assert at.selectbox(key="scope_family").value is None  # the same family can be chosen again


def test_an_empty_selection_asks_for_a_database(pointed_at) -> None:
    at = start(SQL_APP)
    at.multiselect(key="scope_rel_paths").set_value([]).run()
    ok(at)
    assert any("Choose at least one database" in i.value for i in at.info)
    plot = start(PLOT_APP)
    plot.multiselect(key="scope_rel_paths").set_value([]).run()
    assert any("Choose at least one database" in i.value for i in plot.info)


def test_more_than_fifty_family_members_are_capped_with_a_caption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fleet"
    for i in range(60):
        path = root / f"d{i:02d}" / "x.duckdb"
        path.parent.mkdir(parents=True)
        con = duckdb.connect(str(path))
        con.execute(f"CREATE TABLE t AS SELECT {i} AS i")
        con.close()
    cache = tmp_path / "cache"
    run_scan(root, ScanConfig(workers=1, cache_dir=str(cache)))
    monkeypatch.setenv("DUCKFLEET_ROOT", str(root))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))
    at = start(SQL_APP, rel_path="d07/x.duckdb")
    ok(at)
    chosen = at.multiselect(key="scope_rel_paths")
    assert len(chosen.value) == 50 and chosen.proto.max_selections == 50
    assert chosen.value == sorted(chosen.value) and chosen.value[0] == "d00/x.duckdb"
    assert "Family F1 has 60 readable databases; the first 50 are selected (limit L-12)" in (
        captions(at)
    )
    assert "50 of 60 readable databases selected." in captions(at)
    # filling from the family says the same; an edit clears the note
    at.selectbox(key="scope_family").select("F1").run()
    assert len(at.multiselect(key="scope_rel_paths").value) == 50
    assert "(limit L-12)" in captions(at)
    at.multiselect(key="scope_rel_paths").set_value(["d01/x.duckdb"]).run()
    assert "(limit L-12)" not in captions(at)
    run = at.run()
    assert run.multiselect(key="scope_rel_paths").value == ["d01/x.duckdb"]


def test_scope_keys_and_default_selection_helpers() -> None:
    assert scopes.Scope("one", ["a.duckdb"]).key == "a.duckdb"
    assert scopes.Scope("one", []).key == ""
    first = scopes.Scope("many", ["a", "b"]).key
    assert first.startswith("many:2:") and first == scopes.Scope("many", ["a", "b"]).key
    assert first != scopes.Scope("many", ["b", "a"]).key != scopes.Scope("many", ["a"]).key
    assert scopes.attach_warning([]) is None
    text = scopes.attach_warning([{"rel_path": "x.db", "kind": "locked", "message": "held"}])
    assert text is not None and text.startswith("1 database could not be attached and is left out")
    two = scopes.attach_warning([{"rel_path": "a", "kind": "k", "message": "m"}] * 2)
    assert two is not None and two.startswith("2 databases could not be attached and are left out")
    assert "- x.db (locked): held" in text
    dbs = pd.DataFrame(
        {"rel_path": ["a", "b", "c"], "status": ["ok", "error", "ok"],
         "family_strict": ["F1", "F1", None]}
    )  # fmt: skip
    families = pd.DataFrame({"family_id": ["F1"], "members": [["a", "b", "z"]]})
    assert scopes.default_selection("a", dbs, families) == (["a"], None)  # b failed, z unknown
    assert scopes.default_selection("c", dbs, families) == (["c"], None)
    assert scopes.default_selection("b", dbs, families) == (["a"], None)
    assert scopes.default_selection(None, dbs, families) == ([], None)


# ---------------------------------------------------------------- the SQL page


def test_the_sql_page_in_scope_many_shows_the_union_views_and_the_placeholder(pointed_at) -> None:
    at = start(SQL_APP)
    ok(at)
    assert at.text_area(key="sql_text").proto.placeholder == PLACEHOLDER
    views = next(d.value for d in at.dataframe if "view" in d.value.columns)
    by_view = {row["view"]: row for _i, row in views.iterrows()}
    assert set(by_view) == {"main.config", "main.events"}
    assert by_view["main.events"]["databases"] == 3
    assert by_view["main.events"]["columns"].startswith("database VARCHAR, ts TIMESTAMP")
    aliases = next(d.value for d in at.dataframe if list(d.value.columns) == ["alias", "database"])
    assert len(aliases) == 3 and "exp_01_run_01_metrics" in set(aliases["alias"])
    assert any("alias.schema.table" in m.value for m in at.markdown)


def test_the_placeholder_query_of_the_family_returns_three_rows(pointed_at) -> None:
    at = start(SQL_APP, sql_text=PLACEHOLDER)
    at.button[0].click()
    at.run()
    ok(at)
    frame = at.dataframe[-1].value
    assert list(frame.columns) == ["database", "count_star()"] and len(frame) == 3
    assert list(frame["count_star()"]) == [1000, 1000, 1000]
    assert at.session_state["sql_result"]["rel_path"].startswith("many:3:")


def test_a_query_across_six_files_returns_six_groups(pointed_at) -> None:
    at = start(SQL_APP)
    at.multiselect(key="scope_rel_paths").set_value(SIX).run()
    at.text_area(key="sql_text").set_value("select database, count(*) n from events group by 1")
    at.button[0].click()
    at.run()
    ok(at)
    frame = at.dataframe[-1].value
    assert sorted(frame["database"]) == SIX and set(frame["n"]) == {1000}


def test_aliases_work_next_to_the_union_views(pointed_at) -> None:
    sql = (
        "select (select count(*) from exp_01_run_01_metrics.main.events) one, "
        "(select count(*) from events) all_of_them"
    )
    at = start(SQL_APP, sql_text=sql, scope_rel_paths=EXP_1)
    at.button[0].click()
    at.run()
    ok(at)
    assert at.dataframe[-1].value.iloc[0].tolist() == [1000, 3000]


def test_a_write_in_scope_many_is_a_read_only_error(pointed_at) -> None:
    at = start(SQL_APP, sql_text="insert into events values ('x', NULL, 1.0, 1, NULL)")
    at.button[0].click()
    at.run()
    (error,) = at.error
    assert (
        error.value.startswith("read_only:")
        and "duckfleet opens databases read-only" in error.value
    )


def test_a_file_that_vanishes_is_an_attach_warning_above_the_result(
    scanned: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet, cache = scanned
    copy = tmp_path / "copy"
    shutil.copytree(fleet, copy)
    run_scan(copy, ScanConfig(workers=1, cache_dir=str(tmp_path / "cache")))
    (copy / EXP_1[2]).unlink()  # gone after the scan
    monkeypatch.setenv("DUCKFLEET_ROOT", str(copy))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(tmp_path / "cache"))
    at = start(SQL_APP, sql_text=PLACEHOLDER)
    at.button[0].click()
    at.run()
    ok(at)
    warning = at.warning[-1]  # the first one is in the Schema expander, this one the result's
    assert len(at.warning) == 2
    assert warning.value.startswith("1 database could not be attached and is left out")
    assert f"{EXP_1[2]} (missing)" in warning.value
    assert len(at.dataframe[-1].value) == 2  # the other two files still answer


def test_switching_the_scope_drops_the_stale_result(pointed_at) -> None:
    at = start(SQL_APP, mode="one", sql_text="select 1 as one")
    at.button[0].click()
    at.run()
    assert "sql_result" in at.session_state
    at.radio(key="scope_mode").set_value("many").run()
    assert "sql_result" not in at.session_state
    at.radio(key="scope_mode").set_value("one").run()
    assert "sql_result" not in at.session_state


def test_downloads_in_scope_many_export_the_fleet_query(pointed_at) -> None:
    pytest.importorskip("pyarrow")
    at = start(SQL_APP, sql_text=PLACEHOLDER)
    at.button[0].click()
    at.run()
    prepare = next(b for b in at.button if b.label == "Prepare CSV")
    prepare.click()
    at.run()
    ok(at)
    download = at.session_state["download"]
    assert download["name"].startswith("fleet_") and download["name"].endswith(".csv")
    assert download["rows"] == 3 and download["rel_path"].startswith("many:3:")
    assert download["data"].decode().splitlines()[0] == '"database","count_star()"'


# ---------------------------------------------------------------- the Plot page


def figure_of(at: AppTest) -> dict:
    (chart,) = at.get("plotly_chart")
    return json.loads(chart.proto.spec)


def test_the_plot_page_lists_the_union_views_and_colours_by_database(pointed_at) -> None:
    at = start(PLOT_APP)
    ok(at)
    table = at.selectbox(key="selected_table")
    assert table.options == ["main.config", "main.events"] and table.value == "main.events"
    colour = next(s for s in at.selectbox if s.key.startswith("plot_color|many:"))
    assert colour.value == "database" and colour.options[0] == "(none)"
    assert "database" in colour.options[:3]
    assert not at.get("plotly_chart")


def test_a_fleet_plot_of_events_coloured_by_database_has_six_series(pointed_at) -> None:
    at = start(PLOT_APP)
    at.multiselect(key="scope_rel_paths").set_value(SIX).run()
    at.button[0].click()  # Plot
    at.run()
    ok(at)
    traces = figure_of(at)["data"]
    assert len(traces) == 6 and {t["name"] for t in traces} == set(SIX)
    assert [len(t["x"]) for t in traces] == [1000] * 6
    shown = captions(at)
    assert "6 databases" in shown and "points" in shown


def test_the_family_plot_has_three_coloured_series(pointed_at) -> None:
    at = start(PLOT_APP)
    at.button[0].click()
    at.run()
    ok(at)
    traces = figure_of(at)["data"]
    assert len(traces) == 3 and "3 databases" in captions(at)
    colors = [t["line"]["color"] for t in traces]
    assert len(set(colors)) == 3  # one palette slot per file


def test_files_beyond_the_series_cap_are_folded_and_the_caption_says_so(pointed_at) -> None:
    at = start(PLOT_APP)
    at.multiselect(key="scope_rel_paths").set_value(SIX).run()
    at.number_input(key="plot_max_series").set_value(3)
    at.button[0].click()
    at.run()
    ok(at)
    names = [t["name"] for t in figure_of(at)["data"]]
    assert len(names) == 4 and names[-1] == "(other)"
    assert "6 databases" in captions(at)
    assert "3 of 6 databases shown, 3 folded into (other)" in captions(at)


def test_a_fleet_bar_chart_counts_rows_per_database(pointed_at) -> None:
    at = start(PLOT_APP, plot_kind="bar")
    ok(at)
    x = next(s for s in at.selectbox if s.key.startswith("plot_x|many:"))
    assert x.value == "database"  # few distinct values first
    colour = next(s for s in at.selectbox if s.key.startswith("plot_color|many:"))
    assert colour.value == "(none)"  # the categories are the files already
    at.button[0].click()
    at.run()
    ok(at)
    (trace,) = figure_of(at)["data"]
    assert (
        trace["type"] == "bar" and sorted(trace["x"]) == sorted(EXP_1) and trace["y"] == [1000] * 3
    )


def test_a_failed_request_and_a_missing_file_in_scope_many(
    scanned: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet, _cache = scanned
    copy = tmp_path / "copy"
    shutil.copytree(fleet, copy)
    run_scan(copy, ScanConfig(workers=1, cache_dir=str(tmp_path / "cache")))
    (copy / EXP_1[1]).unlink()
    monkeypatch.setenv("DUCKFLEET_ROOT", str(copy))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(tmp_path / "cache"))
    at = start(PLOT_APP)
    ok(at)
    assert any(f"{EXP_1[1]} (missing)" in w.value for w in at.warning)
    at.button[0].click()
    at.run()
    assert len(figure_of(at)["data"]) == 2
    assert any("could not be attached" in n.value for n in at.caption) or any(
        "could not be attached" in w.value for w in at.warning
    )


def test_switching_between_scopes_keeps_each_request_to_its_scope(pointed_at) -> None:
    at = start(PLOT_APP, mode="one", selected_table="main.events")
    at.button[0].click()
    at.run()
    assert len(figure_of(at)["data"]) == 1
    at.radio(key="scope_mode").set_value("many").run()
    assert not at.get("plotly_chart")  # the request belongs to the single database
    at.button[0].click()
    at.run()
    assert len(figure_of(at)["data"]) == 3
    at.radio(key="scope_mode").set_value("one").run()
    assert not at.get("plotly_chart")


def test_plot_this_table_keeps_scope_one(pointed_at) -> None:
    at = AppTest.from_file(PAGES_APP, default_timeout=60)
    at.session_state["page"] = "database"
    at.session_state["selected_rel_path"] = EXP_1[0]
    at.session_state["scope_mode"] = "many"
    at.run()
    ok(at)
    at.session_state["selected_table"] = "main.events"
    at.run()
    button = next(b for b in at.button if b.label == "Plot this table")
    button.click()
    at.run()
    assert at.session_state["scope_mode"] == "one"
    at.session_state["page"] = "plot"
    at.run()
    ok(at)
    assert at.radio(key="scope_mode").value == "one"
    assert at.selectbox(key="selected_table").value == "main.events"
    assert at.radio(key="plot_kind").value == "line"
