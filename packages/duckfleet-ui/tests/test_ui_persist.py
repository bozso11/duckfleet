"""Widget values survive a visit to another page (review V-11, ``persist_state="session"``).

``_pages_app.py`` runs one of the three pages per run against one session state, which is what
leaving a page and coming back does to the widgets that the other pages do not render.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.demo import make_demo_fleet
from duckfleet_ui import query_access

APP = str(Path(__file__).with_name("_pages_app.py"))
RUN2 = "exp_01/run_02/metrics.duckdb"


@pytest.fixture(scope="module")
def scanned(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("persist")
    fleet = make_demo_fleet(base / "fleet")
    cache = base / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    return fleet, cache


@pytest.fixture
def pointed_at(scanned: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
    fleet, cache = scanned
    monkeypatch.setenv("DUCKFLEET_ROOT", str(fleet))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))
    return fleet, cache


def visit(at: AppTest, page: str) -> AppTest:
    at.session_state["page"] = page
    return at.run()


def test_values_survive_a_visit_to_the_other_pages(pointed_at) -> None:
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]

    # Overview: sidebar inputs and the selected database
    at.text_input(key="root_input").set_value(str(pointed_at[0]))
    at.number_input(key="workers_input").set_value(3)
    at.number_input(key="max_files_input").set_value(77)
    at.checkbox(key="full_input").check()
    at.checkbox(key="sniff_input").check()
    at.selectbox(key="selected_rel_path").select(RUN2)
    at.run()
    assert not at.exception, [e.value for e in at.exception]

    # Database page: the selected database arrives, the table choice is made
    at = visit(at, "database")
    assert not at.exception, [e.value for e in at.exception]
    assert at.selectbox(key="selected_rel_path").value == RUN2
    at.selectbox(key="selected_table").select("main.events").run()
    assert at.selectbox(key="selected_table").value == "main.events"

    # SQL page: editor text and options (the options travel with the Run click)
    at = visit(at, "sql")
    assert not at.exception, [e.value for e in at.exception]
    assert at.selectbox(key="selected_rel_path").value == RUN2
    at.text_area(key="sql_text").set_value("select 7 as seven")
    at.number_input(key="sql_max_rows").set_value(77)
    at.number_input(key="sql_timeout").set_value(5)
    at.button[0].click().run()
    assert at.session_state["sql_result"]["rows"] == 1

    # and back to the start
    at = visit(at, "overview")
    assert not at.exception, [e.value for e in at.exception]
    assert at.text_input(key="root_input").value == str(pointed_at[0])
    assert at.number_input(key="workers_input").value == 3
    assert at.number_input(key="max_files_input").value == 77
    assert at.checkbox(key="full_input").value is True
    assert at.checkbox(key="sniff_input").value is True
    assert at.selectbox(key="selected_rel_path").value == RUN2

    at = visit(at, "database")
    assert at.selectbox(key="selected_rel_path").value == RUN2
    assert at.selectbox(key="selected_table").value == "main.events"

    at = visit(at, "sql")
    assert at.text_area(key="sql_text").value == "select 7 as seven"
    assert at.number_input(key="sql_max_rows").value == 77
    assert at.number_input(key="sql_timeout").value == 5


def test_the_query_config_keeps_duckdb_s_temp_directory_in_the_cache(
    scanned: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """V-01: nothing is spilled beside a scanned file; the folder is ``<cache>/tmp/<pid>``."""
    cache = scanned[1]
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))
    query_access.query_config.cache_clear()
    try:
        temp = Path(query_access.query_config().resolved_temp_dir())
    finally:
        query_access.query_config.cache_clear()  # do not pin this test's cache for the others
    assert temp.parent == cache / "tmp" and temp.name.isdigit()
