"""The Database page with Streamlit's AppTest (plan v0.2 Phases 4 and 6).

The page runs through the wrapper ``_database_app.py`` (no ``st.navigation``) against
a scanned copy of the demo fleet plus two extra databases (time zones, odd types).
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.demo import make_demo_fleet
from duckfleet_core.query import QueryConfig, QueryResult
from duckfleet_core.store import SummaryStore
from duckfleet_ui import query_access

APP = str(Path(__file__).with_name("_database_app.py"))
RUN1 = "exp_01/run_01/metrics.duckdb"
TZ_DB = "extra/tz.duckdb"
TYPES_DB = "extra/types.duckdb"
SLOW_DB = "extra/slow.duckdb"


def build_fleet(base: Path) -> tuple[Path, Path]:
    fleet = make_demo_fleet(base / "fleet é")
    (fleet / "extra").mkdir()
    con = duckdb.connect(str(fleet / TZ_DB))
    con.execute(
        "CREATE TABLE ev AS SELECT TIMESTAMPTZ '2026-02-01 00:00:00+00' + to_hours(i) AS at, "
        "i AS n FROM range(30) r(i)"
    )
    con.close()
    con = duckdb.connect(str(fleet / TYPES_DB))
    con.execute(
        "CREATE TABLE odd AS SELECT uuid() AS id, "
        "170141183460469231731687303715884105727::HUGEINT AS big, "
        "DATE 'infinity' AS far, [1, 2] AS l, {'a': 1, 'b': 'x'} AS s, "
        "INTERVAL 3 DAY AS iv, 'x'::BLOB AS b FROM range(3) r(i)"
    )
    con.close()
    con = duckdb.connect(str(fleet / SLOW_DB))
    con.execute(
        "CREATE VIEW slow AS SELECT sum(a.i * b.i) AS s "
        "FROM range(200000) a(i), range(200000) b(i)"
    )
    con.close()
    cache = base / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    return fleet, cache


@pytest.fixture(scope="module")
def scanned(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return build_fleet(tmp_path_factory.mktemp("dbpage"))


@pytest.fixture
def pointed_at(scanned: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
    fleet, cache = scanned
    monkeypatch.setenv("DUCKFLEET_ROOT", str(fleet))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))
    return fleet, cache


def run_app(
    rel_path: str | None = RUN1, table: str | None = None, tab: str | None = None
) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    if rel_path:
        at.session_state["selected_rel_path"] = rel_path
    if table:
        at.session_state["selected_table"] = table
    if tab:
        at.session_state["database_tabs"] = tab
    return at.run()


def frames(at: AppTest) -> list[pd.DataFrame]:
    return [d.value for d in at.dataframe]


def frame_with(at: AppTest, column: str) -> pd.DataFrame:
    return next(f for f in frames(at) if column in f.columns)


def sig(rel_path: str, table: str) -> str:
    return f"{rel_path}|{table}"


# ---------------------------------------------------------------- the page


def test_renders_header_tables_and_columns(pointed_at) -> None:
    at = run_app()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    assert at.title[0].value == "Database"
    assert at.subheader[0].value == RUN1
    facts = " ".join(str(c.value) for c in at.caption)
    assert "strict family F1" in facts and "loose family L1" in facts and "MB" in facts
    assert "Tags: exp=01, run=01" in facts and "Health: wal_present" in facts

    tables = frame_with(at, "table_name")
    assert tables["table_name"].tolist() == ["config", "events"]
    assert tables["row_count"].tolist() == [3, 1000]
    assert tables["n_columns"].tolist() == [2, 3]
    assert tables["schema_name"].tolist() == ["main", "main"]

    assert at.selectbox(key="selected_table").value == "main.config"  # first table
    config_columns = frame_with(at, "column_name")
    assert config_columns["column_name"].tolist() == ["key", "value"]
    assert [t.label for t in at.tabs] == ["Columns", "Summary", "Preview"]
    assert at.session_state["database_tabs"] == "Columns"
    assert not at.button or [b.label for b in at.button] == ["Open in SQL"]  # lazy: no summary yet


def test_columns_tab_of_the_events_table(pointed_at) -> None:
    at = run_app(table="main.events")
    assert not at.exception, [e.value for e in at.exception]
    columns = frame_with(at, "column_name")
    assert columns["column_name"].tolist() == ["ts", "value", "run_id"]
    assert columns["data_type"].tolist() == ["TIMESTAMP", "DOUBLE", "INTEGER"]
    assert columns["null_share"].tolist() == [0.0, 0.0, 0.0]
    assert columns["min_value"][0] == "2026-01-05T00:00:00"
    assert columns["approx_distinct"][2] == 1
    assert columns["sampled"].tolist() == [False, False, False]


def test_database_choice_lists_ok_databases_first(pointed_at) -> None:
    at = run_app()
    options = at.selectbox(key="selected_rel_path").options
    assert options[0] != "empty/empty.duckdb"  # a database with tables is the default
    assert options.index(RUN1) < options.index("empty/empty.duckdb")  # empty ones after those
    assert options.index("empty/empty.duckdb") < options.index("broken/corrupt.duckdb")
    failed = {"broken/corrupt.duckdb", "broken/highbit.duckdb", "secure/encrypted.duckdb"}
    assert set(options[-3:]) == failed
    assert set(options[:-3]).isdisjoint(failed) and RUN1 in options


# ---------------------------------------------------------------- summary


def test_summary_is_computed_on_request_and_cached(pointed_at) -> None:
    fleet, cache = pointed_at
    table = "main.events"
    key = sig(RUN1, table)
    at = run_app(table=table, tab="Summary")
    assert "Compute summary" in [b.label for b in at.button]
    assert not any("computed 20" in str(c.value) for c in at.caption)

    at.button(key=f"compute_summary|{key}").click().run()
    assert not at.exception, [e.value for e in at.exception]
    # V-09: the run that computed the summary already offers "Recompute"
    assert "Compute summary" not in [b.label for b in at.button]
    assert "Recompute" in [b.label for b in at.button]
    summary = frame_with(at, "approx_unique")
    assert summary["column_name"].tolist() == ["ts", "value", "run_id"]
    assert summary["count"].tolist() == [1000, 1000, 1000]
    assert summary["min"][2] == "1" and summary["max"][2] == "1"
    assert any("computed 20" in str(c.value) and "all rows" in str(c.value) for c in at.caption)
    sidecar = SummaryStore(cache, fleet).path(RUN1, "main", "events")
    assert sidecar.is_file()

    again = run_app(table=table, tab="Summary")  # a new session finds the cached summary at once
    assert "Compute summary" not in [b.label for b in again.button]
    assert "Recompute" in [b.label for b in again.button]
    assert frame_with(again, "approx_unique")["count"].tolist() == [1000, 1000, 1000]

    again.button(key=f"recompute_summary|{key}").click().run()
    assert not again.exception, [e.value for e in again.exception]
    assert frame_with(again, "approx_unique")["column_name"].tolist() == ["ts", "value", "run_id"]


def test_a_failing_summary_shows_the_error_and_is_not_reused(
    pointed_at, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(query_access, "query_config", lambda: QueryConfig(timeout_s=0.5))
    key = sig(SLOW_DB, "main.slow")
    at = run_app(SLOW_DB, "main.slow", tab="Summary")
    assert not at.exception, [e.value for e in at.exception]
    at.button(key=f"compute_summary|{key}").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("The summary failed: timeout:" in e.value for e in at.error)
    # V-09: no rerun after a failure, so the error stays on screen and the button stays
    assert f"compute_summary|{key}" in [b.key for b in at.button]
    again = run_app(SLOW_DB, "main.slow", tab="Summary")  # the error is not served from the cache
    assert f"compute_summary|{key}" in [b.key for b in again.button]


# ---------------------------------------------------------------- preview


def test_preview_shows_the_first_page(pointed_at) -> None:
    at = run_app(table="main.events", tab="Preview")
    assert not at.exception, [e.value for e in at.exception]
    preview = next(f for f in frames(at) if list(f.columns) == ["ts", "value", "run_id"])
    assert len(preview) == 1000
    note = " ".join(str(c.value) for c in at.caption)
    assert "rows 1 to 1,000 of 1,000" in note


def test_preview_shows_rows_added_after_the_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V-05: the preview cache is keyed by the file's live size and mtime, not the scan's."""
    fleet, cache = build_fleet(tmp_path)
    monkeypatch.setenv("DUCKFLEET_ROOT", str(fleet))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))
    rel_path = "exp_01/run_02/metrics.duckdb"
    key = sig(rel_path, "main.events")
    at = run_app(rel_path, table="main.events", tab="Preview")
    at.selectbox(key=f"preview_sort|{key}").select("ts")
    at.checkbox(key=f"preview_desc|{key}").check()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    before = next(f for f in frames(at) if list(f.columns) == ["ts", "value", "run_id"])
    assert len(before) == 1000 and 42.0 not in before["value"].tolist()

    con = duckdb.connect(str(fleet / rel_path))
    con.execute("INSERT INTO events VALUES (TIMESTAMP '2030-01-01 00:00:00', 42.0, 2)")
    con.close()
    at.run()  # the same session, the same widgets: only the file changed
    assert not at.exception, [e.value for e in at.exception]
    after = next(f for f in frames(at) if list(f.columns) == ["ts", "value", "run_id"])
    assert after["value"][0] == 42.0  # newest first: the new row is on top
    assert any("Changed since the last scan" in str(c.value) for c in at.caption)


def test_preview_page_two_sorted_descending(pointed_at, monkeypatch: pytest.MonkeyPatch) -> None:
    fleet, _ = pointed_at
    monkeypatch.setattr(query_access, "query_config", lambda: QueryConfig(preview_rows=100))
    key = sig(RUN1, "main.events")
    at = run_app(table="main.events", tab="Preview")
    assert at.number_input(key=f"preview_page|{key}").max == 10  # 1000 rows, 100 per page
    at.selectbox(key=f"preview_sort|{key}").select("ts")
    at.checkbox(key=f"preview_desc|{key}").check()
    at.number_input(key=f"preview_page|{key}").set_value(2)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    preview = next(f for f in frames(at) if list(f.columns) == ["ts", "value", "run_id"])
    con = duckdb.connect(str(fleet / RUN1), read_only=True)
    expected = con.execute("select ts from events order by ts desc limit 100 offset 100").df()
    con.close()
    assert len(preview) == 100
    assert pd.to_datetime(preview["ts"]).tolist() == pd.to_datetime(expected["ts"]).tolist()
    assert pd.to_datetime(preview["ts"]).is_monotonic_decreasing
    assert "rows 101 to 200 of 1,000" in " ".join(str(c.value) for c in at.caption)


def test_preview_column_subset(pointed_at) -> None:
    key = sig(RUN1, "main.events")
    at = run_app(table="main.events", tab="Preview")
    at.multiselect(key=f"preview_columns|{key}").unselect("value")
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(list(f.columns) == ["ts", "run_id"] for f in frames(at))
    at.multiselect(key=f"preview_columns|{key}").set_value([])
    at.run()
    assert any("Choose at least one column" in i.value for i in at.info)


def test_preview_of_a_timestamptz_table(pointed_at) -> None:
    """Acceptance 7: TIMESTAMP WITH TIME ZONE rows fetch (pytz) and display."""
    at = run_app(TZ_DB, "main.ev", tab="Preview")
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    preview = next(f for f in frames(at) if list(f.columns) == ["at", "n"])
    assert len(preview) == 30
    first = preview["at"][0]
    assert first == pd.Timestamp("2026-02-01 00:00:00", tz="UTC")  # same instant, any zone
    assert preview["n"].tolist() == list(range(30))


def test_preview_of_odd_types_never_breaks_the_grid(pointed_at) -> None:
    at = run_app(TYPES_DB, "main.odd", tab="Preview")
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    preview = next(f for f in frames(at) if "big" in f.columns and "iv" in f.columns)
    assert len(preview) == 3
    note = " ".join(str(c.value) for c in at.caption)
    assert "shown as text" in note and "big" in note  # HUGEINT beyond 64 bits


# ---------------------------------------------------------------- states of the file


def test_a_database_that_failed_in_the_scan_shows_the_error_and_stops(pointed_at) -> None:
    at = run_app("secure/encrypted.duckdb")
    assert not at.exception, [e.value for e in at.exception]
    assert any("encrypted" in e.value for e in at.error)
    assert not at.tabs and not at.button


def test_a_missing_file_is_explained_instead_of_failing(tmp_path: Path, monkeypatch) -> None:
    fleet, cache = build_fleet(tmp_path)
    monkeypatch.setenv("DUCKFLEET_ROOT", str(fleet))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))
    (fleet / RUN1).unlink()
    (fleet / (RUN1 + ".wal")).unlink()
    at = run_app(table="main.events")
    assert not at.exception, [e.value for e in at.exception]
    assert any("missing since the last scan" in w.value for w in at.warning)
    assert not at.error
    assert frame_with(at, "table_name")["table_name"].tolist() == ["config", "events"]  # catalog
    assert frame_with(at, "column_name")["column_name"].tolist() == ["ts", "value", "run_id"]
    assert at.button(key="open_in_sql").disabled
    for tab in ("Summary", "Preview"):
        other = run_app(table="main.events", tab=tab)
        assert not other.exception and not other.error
        assert any("file is missing" in i.value for i in other.info), tab


def test_a_file_changed_since_the_scan_is_noted(tmp_path: Path, monkeypatch) -> None:
    fleet, cache = build_fleet(tmp_path)
    monkeypatch.setenv("DUCKFLEET_ROOT", str(fleet))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(cache))
    con = duckdb.connect(str(fleet / RUN1))
    con.execute("INSERT INTO config VALUES ('extra', 'row')")
    con.close()
    at = run_app(table="main.config")
    assert not at.exception, [e.value for e in at.exception]
    assert any("Changed since the last scan" in str(c.value) for c in at.caption)
    tables = frame_with(at, "table_name")
    assert tables["row_count"].tolist() == [3, 1000]  # counts from the scan


def test_nothing_is_loaded_without_a_catalog(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DUCKFLEET_ROOT", str(tmp_path / "never scanned"))
    monkeypatch.setenv("DUCKFLEET_CACHE_DIR", str(tmp_path / "empty cache"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert any("No fleet is loaded" in i.value for i in at.info)
    assert not at.tabs


# ---------------------------------------------------------------- hand-off to the SQL page


def test_open_in_sql_prepares_the_query(pointed_at) -> None:
    at = run_app(table="main.events")
    at.button(key="open_in_sql").click()
    at.run()
    # Page switching itself is not exercised by AppTest (plan Section 10 item 3): assert the state.
    assert at.session_state["sql_text"] == 'SELECT * FROM "main"."events" LIMIT 100'
    assert at.session_state["selected_rel_path"] == RUN1


# ---------------------------------------------------------------- result frames


def test_frame_from_result_handles_unusual_values() -> None:
    result = QueryResult(
        columns=["a", "a", "when", "far", "big", "uid", "lst", "dec"],
        types=["INTEGER", "INTEGER", "TIMESTAMP", "DATE", "HUGEINT", "UUID", "INTEGER[]",
               "DECIMAL(18,3)"],  # fmt: skip
        rows=[
            (1, 2, dt.datetime(2026, 1, 1), dt.date(2026, 1, 1), 2**100, uuid.uuid4(), [1], 1.5),
            (3, 4, None, dt.date.max, None, None, None, None),
        ],
        truncated=False,
        elapsed_s=0.0,
        statements=1,
    )
    frame, as_text = query_access.frame_from_result(result)
    assert list(frame.columns) == ["a", "a_2", "when", "far", "big", "uid", "lst", "dec"]
    assert pd.api.types.is_datetime64_any_dtype(frame["when"])
    assert frame["lst"][0] == [1]
    assert "big" in as_text  # an integer beyond 64 bits does not fit an Arrow column
    assert frame["big"][0] == str(2**100)
    assert frame["uid"][0] is not None
    assert pd.isna(frame["big"][1])
    empty, none_text = query_access.frame_from_result(
        QueryResult(["x"], ["INTEGER"], [], False, 0.0, 1)
    )
    assert list(empty.columns) == ["x"] and empty.empty and none_text == []
