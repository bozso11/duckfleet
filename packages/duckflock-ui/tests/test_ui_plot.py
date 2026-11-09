"""The Plot page with Streamlit's AppTest (plan v0.3 Phase 3).

The page runs through the wrapper ``_plot_app.py`` (no ``st.navigation``) against a scanned
demo flock plus a database with several runs. The hand-off from the Database page runs
through ``_pages_app.py``, which keeps one session state across pages.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from duckflock_core.config import ScanConfig
from duckflock_core.coordinator import run_scan
from duckflock_core.demo import make_demo_flock
from duckflock_core.plotdata import PlotSpec, fetch_plot_data
from duckflock_core.query import QueryConfig, QueryError
from duckflock_ui import charts, query_access
from duckflock_ui.views import plot as plot_view

APP = str(Path(__file__).with_name("_plot_app.py"))
PAGES_APP = str(Path(__file__).with_name("_pages_app.py"))
DB = "exp_01/run_01/metrics.duckdb"
MULTI = "extra/multi.duckdb"
KEY = f"{DB}|main.events"


@pytest.fixture(scope="module")
def scanned(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("plotpage")
    flock = make_demo_flock(base / "flock é")
    (flock / "extra").mkdir()
    con = duckdb.connect(str(flock / MULTI))
    con.execute(
        "CREATE TABLE events AS SELECT TIMESTAMP '2026-03-01 00:00:00' + to_minutes(i) AS ts, "
        "sin(i / 50.0) AS value, (i % 4) + 1 AS run_id, i * 1000.0 AS big FROM range(4000) r(i)"
    )
    columns = ", ".join(f"i * {k} AS c{k}" for k in range(1, 11))
    con.execute(f"CREATE TABLE wide AS SELECT {columns} FROM range(50) r(i)")  # ten numeric columns
    con.close()
    cache = base / "cache"
    run_scan(flock, ScanConfig(workers=1, cache_dir=str(cache)))
    return flock, cache


@pytest.fixture
def pointed_at(scanned: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
    flock, cache = scanned
    monkeypatch.setenv("DUCKFLOCK_ROOT", str(flock))
    monkeypatch.setenv("DUCKFLOCK_CACHE_DIR", str(cache))
    return flock, cache


def start(rel_path: str = DB, table: str = "main.events", kind: str | None = None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["selected_rel_path"] = rel_path
    at.session_state["selected_table"] = table
    if kind:
        at.session_state["plot_kind"] = kind
    return at.run()


def submit(at: AppTest) -> AppTest:
    at.button[0].click()  # Plot
    return at.run()


def figure_of(at: AppTest) -> dict:
    (chart,) = at.get("plotly_chart")
    return json.loads(chart.proto.spec)


def captions(at: AppTest) -> str:
    return " | ".join(str(c.value) for c in at.caption)


def base(rel_path: str, table: str, kind: str) -> str:
    return f"{rel_path}|{table}|{kind}"


def ok(at: AppTest) -> None:
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


# ---------------------------------------------------------------- the page


def test_the_page_renders_its_controls_without_plotting(pointed_at) -> None:
    at = start()
    ok(at)
    assert at.title[0].value == "Plot"
    assert at.selectbox(key="selected_rel_path").value == DB
    assert at.selectbox(key="selected_table").value == "main.events"
    assert at.radio(key="plot_kind").options == ["line", "scatter", "histogram", "bar"]
    assert at.radio(key="plot_kind").value == "line"
    assert [b.label for b in at.button] == ["Plot"] and at.button[0].proto.type == "primary"
    assert at.button[0].proto.form_id == "plot"
    x = at.selectbox(key=f"plot_x|{base(DB, 'main.events', 'line')}")
    assert x.options == ["ts", "value", "run_id", "(row order)"]  # temporal, numeric, row order
    assert x.value == "ts"
    y = at.multiselect(key=f"plot_y|{base(DB, 'main.events', 'line')}")
    assert y.options == ["value", "run_id"] and y.value == ["value"]
    colour = at.selectbox(key=f"plot_color|{base(DB, 'main.events', 'line')}")
    assert colour.options[0] == "(none)" and colour.value == "(none)"
    assert at.number_input(key="plot_max_points").value == 50_000
    assert at.number_input(key="plot_bins").value == 50
    assert [e.label for e in at.expander] == ["Options"]
    assert not at.get("plotly_chart")


def test_the_first_table_shown_is_one_with_a_time_window(pointed_at) -> None:
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["selected_rel_path"] = DB
    at.run()
    ok(at)
    assert at.selectbox(key="selected_table").value == "main.events"  # not main.config
    assert at.multiselect(key=f"plot_y|{KEY}|line").value == ["value"]


def test_a_line_chart_of_one_thousand_points(pointed_at) -> None:
    at = submit(start())
    ok(at)
    figure = figure_of(at)
    assert len(figure["data"]) == 1 and figure["data"][0]["mode"].startswith("lines")
    assert len(figure["data"][0]["x"]) == 1000
    assert "1,000 points, every one of 1,000 rows" in captions(at)
    assert [e.label for e in at.expander] == ["Options", "Plotted data", "SQL"]
    assert figure["layout"]["hovermode"] == "x unified"


def test_a_small_budget_buckets_the_line_and_shows_the_band(pointed_at) -> None:
    at = start()
    at.number_input(key="plot_max_points").set_value(100)
    at = submit(at)
    ok(at)
    traces = figure_of(at)["data"]
    assert len(traces) == 3 and traces[1]["fill"] == "tonexty"
    assert traces[0]["hoverinfo"] == "skip" and traces[2]["name"] == "value"
    assert len(traces[2]["x"]) < 100
    text = captions(at)
    assert "bucketed to 3 hours" in text and "mean with min to max band" in text
    assert "from 1,000 rows" in text
    assert any("time_bucket" in str(c.proto.code_text) for c in at.get("code"))


def test_a_scatter_with_series_by_colour(pointed_at) -> None:
    at = start(MULTI, kind="scatter")
    ok(at)
    b = base(MULTI, "main.events", "scatter")
    at.selectbox(key=f"plot_x|{b}").select("value")
    at.multiselect(key=f"plot_y|{b}").set_value(["big"])
    at.selectbox(key=f"plot_color|{b}").select("run_id")
    at = submit(at)
    ok(at)
    traces = figure_of(at)["data"]
    # equal row counts: by name; three series and the folded rest (D-032)
    assert [t["name"] for t in traces] == ["1", "2", "3", "(other)"]
    assert all(t["mode"] == "markers" and t["marker"]["size"] == 8 for t in traces)
    assert all(t["type"] in ("scatter", "scattergl") for t in traces)


def test_a_scatter_is_capped_at_three_series_plus_other(pointed_at) -> None:
    at = start(MULTI, kind="scatter")
    b = base(MULTI, "main.events", "scatter")
    at.selectbox(key=f"plot_color|{b}").select("run_id")
    at.number_input(key="plot_max_points").set_value(1000)  # fewer than the 4,000 rows: sampled
    at = submit(at)
    ok(at)
    traces = figure_of(at)["data"]
    assert len(traces) == 4 and traces[-1]["name"] == "(other)"
    assert traces[-1]["marker"]["color"] == "#898781"
    assert "reservoir sample of" in captions(at) and "3 of 4 series shown" in captions(at)


def test_a_line_chart_with_series_by_colour(pointed_at) -> None:
    at = start(MULTI)
    b = base(MULTI, "main.events", "line")
    at.selectbox(key=f"plot_color|{b}").select("run_id")
    at.number_input(key="plot_max_points").set_value(400)
    at = submit(at)
    ok(at)
    traces = figure_of(at)["data"]
    assert len(traces) == 12  # four series: min, max and mean each
    legend = [t for t in traces if t.get("showlegend")]
    assert [t["name"] for t in legend] == ["1", "2", "3", "4"]
    assert [t["line"]["color"] for t in legend] == charts.palette("light")[:4]
    assert figure_of(at)["layout"]["showlegend"] is True


def test_a_histogram_of_value(pointed_at) -> None:
    at = start(kind="histogram")
    ok(at)
    assert not any(s.key.startswith("plot_x|") for s in at.selectbox)  # no x axis
    at.number_input(key="plot_bins").set_value(20)
    at = submit(at)
    ok(at)
    (trace,) = figure_of(at)["data"]
    assert trace["type"] == "bar" and sum(trace["y"]) == 1000 and len(trace["y"]) <= 20
    assert "1,000 rows in 20 equal-width bins" in captions(at)


def test_a_bar_chart_of_the_config_keys(pointed_at) -> None:
    at = start(table="main.config", kind="bar")
    ok(at)
    b = base(DB, "main.config", "bar")
    assert at.selectbox(key=f"plot_x|{b}").options[:2] == ["key", "value"]
    at.selectbox(key=f"plot_x|{b}").select("key")
    at = submit(at)
    ok(at)
    (trace,) = figure_of(at)["data"]
    assert trace["type"] == "bar"
    assert sorted(trace["x"]) == ["batch_size", "learning_rate", "optimizer"]
    assert trace["y"] == [1, 1, 1]
    assert "3 categories from 3 rows" in captions(at)


def test_a_table_without_numeric_columns_can_only_be_counted(pointed_at) -> None:
    at = start(table="main.config")
    ok(at)
    assert any("no numeric column" in i.value for i in at.info)
    assert not at.button
    at.radio(key="plot_kind").set_value("bar").run()
    ok(at)
    assert [b.label for b in at.button] == ["Plot"]


# ---------------------------------------------------------------- table view and CSV


def test_the_plotted_data_are_shown_and_offered_as_csv(pointed_at) -> None:
    at = start()
    at.number_input(key="plot_max_points").set_value(100)
    at = submit(at)
    ok(at)
    frames = [d.value for d in at.dataframe]
    table = next(f for f in frames if "value_mean" in f.columns)
    assert list(table.columns) == ["ts", "value_mean", "value_min", "value_max", "value_count"]
    assert 0 < len(table) < 100 and table["value_count"].sum() == 1000
    (button,) = at.get("download_button")
    assert button.proto.label == "Download CSV"


def test_the_csv_is_utf8_with_lf_line_ends(scanned: tuple[Path, Path]) -> None:
    flock, _cache = scanned
    spec = PlotSpec(schema_name="main", table_name="events", kind="line", x="ts", y=["value"],
                    max_points=100)  # fmt: skip
    data = fetch_plot_data(flock / DB, spec, QueryConfig())
    raw = plot_view.csv_bytes(data)
    text = raw.decode("utf-8")
    assert "\r" not in text
    lines = text.split("\n")
    assert lines[0] == "ts,value_mean,value_min,value_max,value_count"
    assert len(lines) == len(data.rows) + 2 and lines[-1] == ""
    assert lines[1].startswith("2026-01-05 00:00:00,")


# ---------------------------------------------------------------- errors


def test_a_bad_filter_shows_the_sql_error(pointed_at) -> None:
    at = start()
    at.text_input(key=f"plot_where|{KEY}|line").set_value("no_such_column > 1")
    at = submit(at)
    assert not at.exception
    (error,) = at.error
    assert error.value.startswith("sql:") and "no_such_column" in error.value
    assert not at.get("plotly_chart")


def test_a_syntax_error_in_the_filter(pointed_at) -> None:
    at = start()
    at.text_input(key=f"plot_where|{KEY}|line").set_value("value >")
    at = submit(at)
    (error,) = at.error
    assert error.value.startswith("sql:") and "syntax error" in error.value.lower()


def test_a_filter_that_matches_nothing_says_so(pointed_at) -> None:
    at = start()
    at.text_input(key=f"plot_where|{KEY}|line").set_value("value > 1000")
    at = submit(at)
    ok(at)
    assert any("Nothing to plot" in i.value for i in at.info)
    assert not at.get("plotly_chart")


def test_a_filter_narrows_the_plot(pointed_at) -> None:
    at = start()
    at.text_input(key=f"plot_where|{KEY}|line").set_value("value > 0.5")
    at = submit(at)
    ok(at)
    (trace,) = figure_of(at)["data"]
    assert 0 < len(trace["x"]) < 1000 and min(trace["y"]) > 0.5


def test_the_timeout_hint_is_shown(pointed_at, monkeypatch: pytest.MonkeyPatch) -> None:
    def too_slow(*_args):
        raise QueryError("timeout", "the plot ran longer than 60 s")

    monkeypatch.setattr(query_access, "plot_data", too_slow)
    at = submit(start())
    (error,) = at.error
    assert error.value.startswith("timeout:") and "lower the point budget" in error.value


# ---------------------------------------------------------------- reruns


def test_the_chart_survives_a_rerun_without_asking_again(
    pointed_at, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = query_access.plot_data.__wrapped__
    calls = []

    def counting(*args):
        calls.append(args)
        return real(*args)

    query_access.plot_data.clear()
    monkeypatch.setattr(query_access, "plot_data", counting)
    at = submit(start())
    ok(at)
    at.run()  # a rerun caused by anything else
    ok(at)
    assert figure_of(at)["data"] and len(calls) == 2  # drawn again, not remembered as an image
    monkeypatch.undo()


def test_a_failed_request_keeps_its_error_instead_of_running_again(
    pointed_at, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def failing(*args):
        calls.append(args)
        raise QueryError("sql", "boom")

    monkeypatch.setattr(query_access, "plot_data", failing)
    at = submit(start())
    assert [e.value for e in at.error] == ["sql: boom"]
    at.run()
    assert [e.value for e in at.error] == ["sql: boom"] and len(calls) == 1


def test_another_table_hides_the_chart_of_the_first(pointed_at) -> None:
    at = submit(start())
    assert at.get("plotly_chart")
    at.selectbox(key="selected_table").select("main.config").run()
    ok(at)
    assert not at.get("plotly_chart")
    at.selectbox(key="selected_table").select("main.events").run()
    assert at.get("plotly_chart")  # the request is remembered for its own table


# ---------------------------------------------------------------- layout, theme, options


def test_several_scales_go_to_panels_and_the_page_says_so(pointed_at) -> None:
    at = start(MULTI)
    b = base(MULTI, "main.events", "line")
    at.multiselect(key=f"plot_y|{b}").set_value(["value", "big"])  # |value| <= 1, big up to 4e6
    at = submit(at)
    ok(at)
    layout = figure_of(at)["layout"]
    assert "yaxis2" in layout and not layout["yaxis2"].get("overlaying")
    assert "differ by more than 100x" in captions(at)
    at.radio(key="plot_layout").set_value("one")
    at = submit(at)
    assert "yaxis2" not in figure_of(at)["layout"]


def test_a_colour_column_with_two_y_columns_uses_panels(pointed_at) -> None:
    at = start(MULTI)
    b = base(MULTI, "main.events", "line")
    at.multiselect(key=f"plot_y|{b}").set_value(["value", "big"])
    at.selectbox(key=f"plot_color|{b}").select("run_id")
    at.radio(key="plot_layout").set_value("one")  # the chart rules win
    at = submit(at)
    ok(at)
    assert "yaxis2" in figure_of(at)["layout"]
    assert "colour column is used with several y columns" in captions(at)


def test_the_dark_theme_uses_the_dark_slots(pointed_at) -> None:
    at = start()
    at.selectbox(key="plot_theme").select("dark")
    at = submit(at)
    ok(at)
    assert figure_of(at)["data"][0]["line"]["color"] == charts.palette("dark")[0]


def test_theme_mode_prefers_the_choice_then_streamlit_then_light() -> None:
    assert plot_view.theme_mode("dark") == "dark" and plot_view.theme_mode("light") == "light"
    assert plot_view.theme_mode("auto") == "light"  # no Streamlit theme outside a session


def test_the_x_axis_can_be_the_row_order(pointed_at) -> None:
    at = start()
    at.selectbox(key=f"plot_x|{KEY}|line").select("(row order)")
    at = submit(at)
    ok(at)
    assert figure_of(at)["layout"]["xaxis"]["title"]["text"] == "(row order)"
    assert figure_of(at)["data"][0]["x"][:3] == [1, 2, 3]


# ---------------------------------------------------------------- describing the reduction


def test_describe_each_strategy(scanned: tuple[Path, Path]) -> None:
    flock, _cache = scanned

    def describe(**fields) -> str:
        spec = PlotSpec(schema_name="main", table_name="events", **fields)
        return plot_view.describe(fetch_plot_data(flock / DB, spec, QueryConfig()))

    assert describe(kind="line", x="ts", y=["value"]) == "1,000 points, every one of 1,000 rows"
    assert describe(kind="line", x="ts", y=["value"], max_points=100) == (
        "56 points from 1,000 rows: bucketed to 3 hours, mean with min to max band"
    )
    assert describe(kind="scatter", x="ts", y=["value"], max_points=100) == (
        "reservoir sample of 100 of 1,000 rows"
    )
    assert describe(kind="histogram", y=["value"], bins=10).startswith("1,000 rows in 10 equal")
    assert describe(kind="bar", x="run_id").startswith("1 categories from 1,000 rows")


# ---------------------------------------------------------------- hand-off from the Database page


def test_plot_this_table_lands_on_the_plot_page_with_the_table_preselected(pointed_at) -> None:
    at = AppTest.from_file(PAGES_APP, default_timeout=60)
    at.session_state["page"] = "database"
    at.session_state["selected_rel_path"] = DB
    at.session_state["selected_table"] = "main.config"
    at.run()
    ok(at)
    at.selectbox(key="selected_table").select("main.events").run()
    at.button(key="plot_this_table").click()
    at.run()  # st.switch_page itself is not exercised by AppTest; the state it hands over is
    assert at.session_state["selected_table"] == "main.events"
    assert at.session_state["plot_prefill"] == {
        "rel_path": DB, "table": "main.events", "x": "ts", "y": "value",
    }  # fmt: skip

    at.session_state["page"] = "plot"
    at.run()
    ok(at)
    assert at.title[0].value == "Plot"
    assert at.selectbox(key="selected_table").value == "main.events"
    assert at.radio(key="plot_kind").value == "line"
    assert at.selectbox(key=f"plot_x|{KEY}|line").value == "ts"
    assert at.multiselect(key=f"plot_y|{KEY}|line").value == ["value"]
    assert "plot_prefill" not in at.session_state  # used once
    submit(at)
    assert at.get("plotly_chart")


def test_the_hand_off_of_a_table_without_dates_uses_the_row_order(pointed_at) -> None:
    at = AppTest.from_file(PAGES_APP, default_timeout=60)
    at.session_state["page"] = "database"
    at.session_state["selected_rel_path"] = "legacy/old.db"
    at.run()
    at.button(key="plot_this_table").click()
    at.run()
    prefill = at.session_state["plot_prefill"]
    assert prefill["table"] == "main.samples" and prefill["x"] == "t" and prefill["y"] == "x"

    at.session_state["page"] = "plot"
    at.run()
    ok(at)
    assert at.selectbox(key="selected_table").value == "main.samples"


# ---------------------------------------------------------------- odd cases


def test_a_missing_file_disables_plotting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    flock = make_demo_flock(tmp_path / "flock")
    run_scan(flock, ScanConfig(workers=1, cache_dir=str(tmp_path / "cache")))
    monkeypatch.setenv("DUCKFLOCK_ROOT", str(flock))
    monkeypatch.setenv("DUCKFLOCK_CACHE_DIR", str(tmp_path / "cache"))
    (flock / "legacy" / "old.db").unlink()
    at = start("legacy/old.db", "main.samples")
    assert not at.exception
    assert any("missing since the last scan" in w.value for w in at.warning)
    assert at.button[0].disabled


def test_a_database_that_failed_in_the_scan_shows_the_error(pointed_at) -> None:
    at = start("broken/corrupt.duckdb")
    assert any("corrupt" in e.value for e in at.error)
    assert not at.button


def test_nothing_is_loaded_without_a_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DUCKFLOCK_ROOT", str(tmp_path / "never scanned"))
    monkeypatch.setenv("DUCKFLOCK_CACHE_DIR", str(tmp_path / "empty cache"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert any("No flock is loaded" in i.value for i in at.info)
    assert not at.button


# ---------------------------------------------------------------- review fixes (0.3.1)


def test_at_most_eight_y_columns_can_be_selected(pointed_at) -> None:
    """W-03: the multiselect stops at eight (one colour each); the spec says the same."""
    at = start(MULTI, "main.wide")
    ok(at)
    y = at.multiselect(key=f"plot_y|{base(MULTI, 'main.wide', 'line')}")
    assert y.proto.max_selections == 8 and len(y.options) == 10
    y.set_value([f"c{k}" for k in range(1, 9)])  # eight are fine
    at = submit(at)
    ok(at)
    assert not at.warning and len(figure_of(at)["data"]) >= 8


def test_switching_the_kind_hides_the_chart_of_the_previous_kind(pointed_at) -> None:
    """W-04: the remembered request belongs to its kind until Plot is pressed again."""
    at = submit(start())
    assert at.get("plotly_chart")
    at.radio(key="plot_kind").set_value("bar").run()
    ok(at)
    assert not at.get("plotly_chart") and "1,000 points" not in captions(at)
    at.radio(key="plot_kind").set_value("line").run()
    assert at.get("plotly_chart")  # the line request is still the remembered one
    at.radio(key="plot_kind").set_value("histogram").run()
    assert not at.get("plotly_chart")
    at = submit(at)
    assert len(figure_of(at)["data"]) == 1 and figure_of(at)["data"][0]["type"] == "bar"
    at.radio(key="plot_kind").set_value("line").run()
    assert not at.get("plotly_chart")  # now the histogram is the remembered request


def test_a_problem_with_the_choices_is_a_warning_without_a_kind(pointed_at) -> None:
    """W-15: no ``sql:`` in front of "choose at least one y column"."""
    at = start()
    at.multiselect(key=f"plot_y|{KEY}|line").set_value([])
    at = submit(at)
    assert not at.exception and not at.error
    assert [w.value for w in at.warning] == ["choose at least one y column for a line chart"]
    assert not at.get("plotly_chart")
    at.run()  # the warning is remembered with the request
    assert [w.value for w in at.warning] == ["choose at least one y column for a line chart"]


@pytest.fixture(scope="module")
def histogram_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("histcaption") / "h.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE t AS SELECT 5.0 AS const, "
        "CASE WHEN i % 10 = 0 THEN NULL ELSE i::DOUBLE END AS gappy, "
        "CASE WHEN i < 2990 THEN 0.0 ELSE 100.0 END AS lumpy FROM range(3000) r(i)"
    )
    con.close()
    return path


def caption_of(path: Path, **fields) -> str:
    spec = PlotSpec(schema_name="main", table_name="t", kind="histogram", **fields)
    return plot_view.describe(fetch_plot_data(path, spec, QueryConfig()))


def test_the_histogram_caption_counts_drawn_bins_and_binned_rows(histogram_db: Path) -> None:
    """W-14: a constant column is one bin; NULLs are not binned; empty bins are not drawn."""
    assert caption_of(histogram_db, y=["const"]).startswith("3,000 rows in 1 equal-width bin ")
    assert caption_of(histogram_db, y=["gappy"], bins=20).startswith(
        "2,700 of 3,000 rows in 20 equal-width bins "
    )
    lumpy = caption_of(histogram_db, y=["lumpy"], bins=20)
    assert lumpy.startswith("3,000 rows in 2 equal-width bins (20 requested; empty bins")
    two = caption_of(histogram_db, y=["const", "lumpy"], bins=20)
    assert two.startswith("3,000 rows in 3 equal-width bins over 2 columns")
