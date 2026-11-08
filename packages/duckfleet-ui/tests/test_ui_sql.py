"""The SQL page with Streamlit's AppTest (plan v0.2 Phases 5 and 6).

The page runs through the wrapper ``_sql_app.py`` (no ``st.navigation``) against a
scanned demo fleet. Page switching itself is not exercised by AppTest (plan Section 10
item 3); the hand-off from the Database page is asserted through the session state.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.demo import make_demo_fleet
from duckfleet_core.query import QueryConfig
from duckfleet_ui import query_access

APP = str(Path(__file__).with_name("_sql_app.py"))
DB = "exp_01/run_01/metrics.duckdb"
OTHER_DB = "exp_02/run_01/metrics.duckdb"
CROSS_JOIN = "select sum(a.i * b.i) from range(200000) a(i), range(200000) b(i)"


@pytest.fixture(scope="module")
def scanned(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("sqlpage")
    fleet = make_demo_fleet(base / "fleet é")
    con = duckdb.connect(str(fleet / "tz.duckdb"))
    con.execute(
        "CREATE TABLE ev AS SELECT TIMESTAMPTZ '2026-02-01 00:00:00+00' + to_hours(i) AS at "
        "FROM range(5) r(i)"
    )
    con.close()
    cache = base / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    return fleet, cache


@pytest.fixture
def pointed_at(scanned: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
    fleet, cache = scanned
    monkeypatch.setenv("DUCKFLEET_ROOT", str(fleet))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))
    return fleet, cache


def start(sql: str | None = None, rel_path: str = DB) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["selected_rel_path"] = rel_path
    if sql is not None:
        at.session_state["sql_text"] = sql
    return at.run()


def run_sql(at: AppTest, sql: str) -> AppTest:
    at.text_area(key="sql_text").set_value(sql)
    at.button[0].click()  # Run
    return at.run()


def labels(at: AppTest) -> list[str]:
    return [b.label for b in at.button]


def shown_frames(at: AppTest) -> list[pd.DataFrame]:
    return [d.value for d in at.dataframe]


def caption(at: AppTest) -> str:
    return " ".join(str(c.value) for c in at.caption)


# ---------------------------------------------------------------- the page


def test_the_page_renders_its_controls(pointed_at) -> None:
    at = start()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value == "SQL"
    assert at.selectbox(key="selected_rel_path").value == DB
    assert labels(at) == ["Run", "Clear"]
    assert at.button[0].proto.type == "primary"
    assert at.number_input(key="sql_max_rows").value == 10_000
    assert at.number_input(key="sql_timeout").value == 60
    assert [e.label for e in at.expander] == ["Schema", "Options"]
    schema = shown_frames(at)[0]
    assert {"main.events", "main.config"} <= set(schema["table"])
    events = schema.loc[schema["table"] == "main.events", "column"]
    assert events.tolist() == ["ts", "value", "run_id"]
    assert "sql_result" not in at.session_state


def test_run_shows_a_result_with_a_caption(pointed_at) -> None:
    at = run_sql(start(), "select count(*) as n from events")
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error
    result = next(f for f in shown_frames(at) if list(f.columns) == ["n"])
    assert result["n"].tolist() == [1000]
    assert "1 rows shown" in caption(at)
    assert not at.warning
    assert at.session_state["sql_history"] == ["select count(*) as n from events"]


def test_a_blank_query_asks_for_text(pointed_at) -> None:
    at = run_sql(start(), "   ")
    assert any("Type a query first" in w.value for w in at.warning)
    assert "sql_result" not in at.session_state


def test_multiple_statements_say_so(pointed_at) -> None:
    at = run_sql(start(), "create temp table x as select 7 as v; select v from x")
    assert not at.exception and not at.error
    assert "2 statements" in caption(at)


def test_a_result_survives_reruns_caused_by_other_widgets(pointed_at) -> None:
    at = run_sql(start(), "select 41 + 1 as answer")
    at.text_area(key="sql_text").set_value("select 2").run()  # an edit reruns the page
    assert not at.exception
    assert any(list(f.columns) == ["answer"] for f in shown_frames(at))
    assert at.session_state["sql_result"]["rows"] == 1


# ---------------------------------------------------------------- errors


def test_an_insert_shows_the_read_only_hint(pointed_at) -> None:
    at = run_sql(start(), "insert into events values (now(), 1.0, 1)")
    assert not at.exception, [e.value for e in at.exception]
    (error,) = at.error
    assert error.value.startswith("read_only:") and "read-only mode" in error.value
    assert "duckfleet opens databases read-only" in error.value
    assert not any(list(f.columns) == ["n"] for f in shown_frames(at)[1:])


@pytest.mark.parametrize(
    "sql",
    ["copy events to 'duckfleet_out.csv'", "attach 'other.db'", "set threads = 8", "load httpfs"],
)
def test_file_access_and_settings_show_the_blocked_hint(
    pointed_at, sql: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    at = run_sql(start(), sql)
    assert not at.exception, [e.value for e in at.exception]
    (error,) = at.error
    assert error.value.startswith("blocked:")
    assert "File access, extensions and settings are disabled on this page" in error.value
    assert list(tmp_path.iterdir()) == []


def test_a_syntax_error_shows_duckdb_s_message(pointed_at) -> None:
    at = run_sql(start(), "selec 1")
    (error,) = at.error
    assert error.value.startswith("sql:") and "syntax error" in error.value.lower()


def test_the_timeout_option_stops_a_runaway_query_and_the_next_one_runs(pointed_at) -> None:
    at = start()
    assert at.button[0].proto.form_id  # Run submits the Options inputs together with the click
    at.number_input(key="sql_timeout").set_value(1)  # V-10: set and run in one go
    started = time.perf_counter()
    at = run_sql(at, CROSS_JOIN)
    assert time.perf_counter() - started < 30  # about 1 s, not the default 60
    assert not at.exception, [e.value for e in at.exception]
    (error,) = at.error
    assert error.value.startswith("timeout:")
    assert "Raise the timeout in Options or narrow the query" in error.value
    at = run_sql(at, "select count(*) as n from events")
    assert not at.error and not at.exception
    assert any(f["n"].tolist() == [1000] for f in shown_frames(at) if list(f.columns) == ["n"])


# ---------------------------------------------------------------- row cap


def test_max_rows_five_gives_a_truncated_result(pointed_at) -> None:
    at = start()
    at.number_input(key="sql_max_rows").set_value(5)
    at = run_sql(at, "select * from events order by ts")
    assert not at.exception, [e.value for e in at.exception]
    grid = next(f for f in shown_frames(at) if list(f.columns) == ["ts", "value", "run_id"])
    assert len(grid) == 5
    assert any("cut at 5 rows" in w.value for w in at.warning)
    assert "5 rows shown" in caption(at)


def test_a_result_that_fits_has_no_truncation_warning(pointed_at) -> None:
    at = start()
    at.number_input(key="sql_max_rows").set_value(5)
    at = run_sql(at, "select * from config")  # 3 rows
    assert not at.warning


# ---------------------------------------------------------------- downloads


def test_csv_and_parquet_downloads(pointed_at) -> None:
    pq = pytest.importorskip("pyarrow.parquet")
    at = run_sql(start(), "select ts, value, run_id from events order by ts")
    assert labels(at) == ["Run", "Clear", "Prepare CSV", "Prepare Parquet"]
    assert not at.get("download_button")

    at.button[2].click().run()
    assert not at.exception, [e.value for e in at.exception]
    csv = at.session_state["download"]
    assert csv["name"].startswith("metrics_") and csv["name"].endswith(".csv")
    assert csv["mime"] == "text/csv" and csv["rows"] == 1000 and not csv["truncated"]
    assert len(csv["data"]) > 0 and csv["data"].startswith(b'"ts","value","run_id"')
    assert len(at.get("download_button")) == 1

    at.button[3].click().run()
    assert not at.exception, [e.value for e in at.exception]
    parquet = at.session_state["download"]
    assert parquet["name"].endswith(".parquet") and len(parquet["data"]) > 0
    assert pq.read_table(io.BytesIO(parquet["data"])).num_rows == 1000
    assert len(at.get("download_button")) == 1


def test_the_download_belongs_to_the_text_it_was_made_from(pointed_at) -> None:
    pytest.importorskip("pyarrow")
    at = run_sql(start(), "select * from config")
    at.button[2].click().run()
    assert len(at.get("download_button")) == 1
    at.text_area(key="sql_text").set_value("select * from events").run()
    assert not at.get("download_button")  # another text: run, then prepare again
    at.button[0].click().run()  # running again drops the old download
    assert "download" not in at.session_state


def test_a_truncated_export_warns_with_the_cap(
    pointed_at, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("pyarrow")
    monkeypatch.setattr(query_access, "query_config", lambda: QueryConfig(max_download_rows=100))
    at = run_sql(start(), "select * from events")
    at.button[2].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["download"]["rows"] == 100 and at.session_state["download"]["truncated"]
    assert any("export was cut at 100 rows" in w.value for w in at.warning)


def test_an_export_that_fails_shows_the_error(pointed_at) -> None:
    pytest.importorskip("pyarrow")
    at = run_sql(start(), "select [1, 2] as nested")
    at.button[2].click().run()  # Prepare CSV: the Arrow CSV writer has no list type
    assert any(e.value.startswith("other:") and "Parquet" in e.value for e in at.error)
    assert "download" not in at.session_state


def test_prepare_is_disabled_while_the_editor_differs_from_the_result(pointed_at) -> None:
    """V-12: the grid shows the last run; the downloads must not export other text."""
    pytest.importorskip("pyarrow")
    at = run_sql(start(), "select 1 as ok")
    assert [b.disabled for b in at.button[2:]] == [False, False] and not caption_mentions(at)
    at.text_area(key="sql_text").set_value("insert into events values (now(), 1, 1)").run()
    assert [b.disabled for b in at.button[2:]] == [True, True]
    assert "differs from the query behind the result" in caption(at)
    at.text_area(key="sql_text").set_value("select 1 as ok").run()  # back to the shown text
    assert [b.disabled for b in at.button[2:]] == [False, False]


def caption_mentions(at: AppTest) -> bool:
    return "differs from the query" in caption(at)


# ---------------------------------------------------------------- history, clear, database change


def test_history_keeps_20_distinct_queries_newest_first(pointed_at) -> None:
    at = start()
    for i in range(23):
        at = run_sql(at, f"select {i} as v")
    at = run_sql(at, "select 22 as v")  # a repeat moves to the front, no duplicate
    history = at.session_state["sql_history"]
    assert len(history) == 20 and history[0] == "select 22 as v" and history[1] == "select 21 as v"
    assert len(set(history)) == 20 and "select 2 as v" not in history


def test_history_selectbox_loads_a_query_into_the_editor(pointed_at) -> None:
    at = start()
    at = run_sql(at, "select 1 as first_one")
    at = run_sql(at, "select 2 as second_one")
    box = at.selectbox(key="sql_history_choice")
    assert box.options == ["select 2 as second_one", "select 1 as first_one"]
    box.select("select 1 as first_one").run()
    assert at.text_area(key="sql_text").value == "select 1 as first_one"
    assert at.selectbox(key="sql_history_choice").value is None  # can be picked again


def test_clear_empties_the_editor_and_the_result(pointed_at) -> None:
    at = run_sql(start(), "select 1 as n")
    assert "sql_result" in at.session_state
    at.button[1].click().run()
    assert not at.exception
    assert at.text_area(key="sql_text").value == ""
    assert "sql_result" not in at.session_state
    assert at.session_state["sql_history"] == ["select 1 as n"]  # history is kept


def test_the_result_is_cleared_when_the_database_changes(pointed_at) -> None:
    at = run_sql(start(), "select count(*) as n from events")
    assert "sql_result" in at.session_state
    at.selectbox(key="selected_rel_path").select(OTHER_DB).run()
    assert not at.exception, [e.value for e in at.exception]
    assert "sql_result" not in at.session_state
    assert not any(list(f.columns) == ["n"] for f in shown_frames(at))


def test_queries_run_on_the_selected_database(pointed_at) -> None:
    at = start(rel_path=OTHER_DB)
    at = run_sql(at, "select typeof(value) as t from events limit 1")
    grid = next(f for f in shown_frames(at) if list(f.columns) == ["t"])
    assert grid["t"].tolist() == ["FLOAT"]  # exp_02 stores FLOAT, exp_01 DOUBLE


# ---------------------------------------------------------------- hand-off, odd cases


def test_the_query_handed_over_by_open_in_sql_runs(pointed_at) -> None:
    at = start('SELECT * FROM "main"."events" LIMIT 100')
    assert at.text_area(key="sql_text").value == 'SELECT * FROM "main"."events" LIMIT 100'
    at.button[0].click().run()
    assert not at.exception and not at.error
    grid = next(f for f in shown_frames(at) if list(f.columns) == ["ts", "value", "run_id"])
    assert len(grid) == 100


def test_timestamptz_values_display(pointed_at) -> None:
    at = run_sql(start(rel_path="tz.duckdb"), 'select "at" from ev order by "at"')
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error
    grid = next(f for f in shown_frames(at) if list(f.columns) == ["at"])
    assert grid["at"][0] == pd.Timestamp("2026-02-01 00:00:00", tz="UTC")  # the same instant


def test_duplicate_column_names_are_made_unique(pointed_at) -> None:
    at = run_sql(start(), "select 1 as a, 2 as a")
    assert not at.exception and not at.error
    assert any(list(f.columns) == ["a", "a_2"] for f in shown_frames(at))


def test_a_database_that_failed_in_the_scan_shows_the_error(pointed_at) -> None:
    at = start(rel_path="broken/corrupt.duckdb")
    assert any("corrupt" in e.value for e in at.error)
    assert not at.text_area


def test_a_missing_file_disables_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fleet = make_demo_fleet(tmp_path / "fleet")
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(tmp_path / "cache")))
    monkeypatch.setenv("DUCKFLEET_ROOT", str(fleet))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(tmp_path / "cache"))
    (fleet / "legacy" / "old.db").unlink()
    at = start("select 1", rel_path="legacy/old.db")
    assert not at.exception
    assert any("missing since the last scan" in w.value for w in at.warning)
    assert at.button[0].disabled


def test_nothing_is_loaded_without_a_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DUCKFLEET_ROOT", str(tmp_path / "never scanned"))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(tmp_path / "empty cache"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert any("No fleet is loaded" in i.value for i in at.info)
    assert not at.text_area
