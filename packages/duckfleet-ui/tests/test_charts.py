"""Plotly figures of ``PlotData`` (plan v0.3 Phase 3, decision D-032)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from duckfleet_core.plotdata import PlotSpec, fetch_plot_data
from duckfleet_core.query import QueryConfig
from duckfleet_ui import charts

ROWS = 6_000


@pytest.fixture(scope="module")
def cdb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("charts") / "c.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute(
            f"""
            CREATE TABLE m AS SELECT
                TIMESTAMP '2026-01-01 00:00:00' + to_seconds(i * 60) AS ts,
                sin(i / 300.0) * 10 + 20 AS y,
                sin(i / 200.0) * 12 + 18 AS y2,
                cos(i / 500.0) * 1000000 AS big,
                (i % 100) / 10.0 AS x,
                'g' || (i % 3)::VARCHAR AS g,
                'k' || (i % 10)::VARCHAR AS k,
                (i % 5)::SMALLINT AS cat,
                CASE WHEN i % 40 = 0 THEN NULL ELSE 'z' || (i % 2)::VARCHAR END AS maybe,
                CAST(i AS DECIMAL(12, 2)) AS dec
            FROM range({ROWS}) r(i)
            """
        )
    finally:
        con.close()
    return path


def data(path: Path, kind: str = "line", **fields):
    base = {"schema_name": "main", "table_name": "m", "kind": kind}
    if kind != "bar":
        base["y"] = ["y"]
    spec = PlotSpec(**(base | fields))
    return fetch_plot_data(path, spec, QueryConfig())


def y_axes(fig) -> list:
    return [fig.layout[name] for name in fig.layout if name.startswith("yaxis")]


def assert_one_y_axis_per_panel(fig, panels: int) -> None:
    axes = y_axes(fig)
    assert len(axes) == panels
    assert all(not axis.overlaying and axis.side != "right" for axis in axes)


# ---------------------------------------------------------------- palette and template


def test_the_palette_has_eight_slots_per_surface() -> None:
    assert charts.palette("light") == [
        "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948",
    ]  # fmt: skip
    assert charts.palette("dark") == [
        "#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767",
    ]  # fmt: skip
    assert charts.palette("anything else") == charts.palette("light")
    assert len(set(charts.palette("light"))) == len(set(charts.palette("dark"))) == 8
    assert charts.palette("light") is not charts.LIGHT_PALETTE  # a copy


def test_series_colours_follow_the_slots_and_are_never_cycled() -> None:
    light = charts.palette("light")
    assert [charts.series_color(f"s{i}", i, "light") for i in range(8)] == light
    assert charts.series_color("s8", 8, "light") == charts.NEUTRAL  # no ninth hue
    assert charts.series_color("(other)", 0, "light") == "#898781"
    assert charts.series_color("(null)", 0, "light") == "rgba(137,135,129,0.6)"
    assert charts.series_color(None, 1, "dark") == charts.palette("dark")[1]


def test_the_template_has_transparent_surfaces_and_a_hairline_grid() -> None:
    light, dark = charts.template("light")["layout"], charts.template("dark")["layout"]
    assert light["paper_bgcolor"] == light["plot_bgcolor"] == "rgba(0,0,0,0)"
    assert light["xaxis"]["gridcolor"] == "#e1e0d9" and dark["xaxis"]["gridcolor"] == "#2c2c2a"
    assert light["yaxis"]["linecolor"] == "#c3c2b7" and dark["yaxis"]["linecolor"] == "#383835"
    assert light["xaxis"]["gridwidth"] == 1 and light["bargap"] >= 0.1
    assert light["colorway"] == charts.palette("light")
    assert light["font"]["color"] != dark["font"]["color"]


# ---------------------------------------------------------------- line charts


def test_a_raw_line_is_one_two_pixel_trace_without_legend(cdb: Path) -> None:
    fig = charts.figure(data(cdb, x="ts", max_points=50_000), "light")
    assert len(fig.data) == 1 and fig.data[0].mode == "lines"
    assert fig.data[0].line.width == 2 and fig.data[0].line.color == charts.palette("light")[0]
    assert fig.layout.showlegend is False and fig.layout.hovermode == "x unified"
    assert fig.layout.template.layout.xaxis.gridcolor == "#e1e0d9"
    assert_one_y_axis_per_panel(fig, 1)
    assert fig.layout.yaxis.title.text == "y" and fig.layout.xaxis.title.text == "ts"


def test_a_bucketed_line_has_a_band_under_the_mean(cdb: Path) -> None:
    plot = data(cdb, x="ts", max_points=200)
    assert plot.strategy == "time_bucket"
    fig = charts.figure(plot, "light")
    low, high, mean = fig.data
    assert (low.line.width, low.showlegend, low.hoverinfo) == (0, False, "skip")
    assert (high.line.width, high.showlegend, high.hoverinfo) == (0, False, "skip")
    assert high.fill == "tonexty" and low.fill is None
    colour = charts.palette("light")[0]  # #2a78d6 at 20 %
    assert high.fillcolor == "rgba(42,120,214,0.2)" and mean.line.color == colour
    assert mean.line.width == 2 and mean.fill is None
    assert all(lo <= hi for lo, hi in zip(low.y, high.y, strict=True))
    assert len(mean.x) == len(plot.rows) and mean.name == "y"


def test_each_series_gets_its_own_band_in_the_right_order(cdb: Path) -> None:
    plot = data(cdb, x="ts", color="g", max_points=300)
    fig = charts.figure(plot, "light")
    assert len(fig.data) == 9 and plot.series == ["g0", "g1", "g2"]
    slots = charts.palette("light")
    for index, series in enumerate(plot.series):
        low, high, mean = fig.data[3 * index : 3 * index + 3]
        assert (low.fill, high.fill) == (None, "tonexty")  # min, then max filling to it
        assert mean.name == series and mean.line.color == slots[index]
        assert mean.showlegend is True and not low.showlegend and not high.showlegend
        assert high.fillcolor == charts._rgba(slots[index], 0.2)
    assert fig.layout.showlegend is True


def test_folded_and_null_series_are_grey(cdb: Path) -> None:
    plot = data(cdb, x="ts", color="k", max_series=2, max_points=1000)
    assert plot.series[-1] == "(other)"
    fig = charts.figure(plot, "light")
    means = {t.name: t for t in fig.data if t.showlegend}
    assert means["(other)"].line.color == charts.NEUTRAL
    assert means[plot.series[0]].line.color == charts.palette("light")[0]
    with_null = data(cdb, x="ts", color="maybe", max_points=50_000)
    assert with_null.series[-1] == "(null)"
    fig = charts.figure(with_null, "dark")
    nulls = next(t for t in fig.data if t.name == "(null)")
    assert nulls.line.color == "rgba(137,135,129,0.6)"
    first = with_null.series[0]  # the most frequent value takes the first slot
    assert next(t for t in fig.data if t.name == first).line.color == charts.palette("dark")[0]


def test_several_y_columns_are_series_with_a_legend(cdb: Path) -> None:
    plot = data(cdb, x="ts", y=["y", "y2"], max_points=50_000)
    assert not charts.needs_panels(plot)  # the same scale
    fig = charts.figure(plot, "light")
    assert [t.name for t in fig.data] == ["y", "y2"] and fig.layout.showlegend is True
    assert [t.line.color for t in fig.data] == charts.palette("light")[:2]
    assert_one_y_axis_per_panel(fig, 1)
    assert fig.layout.yaxis.title.text is None


def test_different_scales_need_panels_and_never_a_second_axis(cdb: Path) -> None:
    plot = data(cdb, x="ts", y=["y", "big"], max_points=50_000)
    assert charts.needs_panels(plot)  # about 1e6 against 30: far above 100x
    one = charts.figure(plot, "light", "one")  # forced onto one axis on request
    assert_one_y_axis_per_panel(one, 1)
    fig = charts.figure(plot, "light", "panels")
    assert_one_y_axis_per_panel(fig, 2)
    assert [t.yaxis for t in fig.data] == ["y", "y2"]
    assert fig.layout.yaxis.title.text == "y" and fig.layout.yaxis2.title.text == "big"
    assert fig.layout.xaxis2.title.text == "ts"  # the shared x axis is titled once, at the bottom
    assert [t.line.color for t in fig.data] == charts.palette("light")[:2]
    assert fig.layout.showlegend is True and len(fig.data) == 2


def test_the_panel_rule_uses_the_ratio_of_the_largest_values(cdb: Path) -> None:
    # 12 against 10: no panels; the band's min and max count for bucketed data
    assert not charts.needs_panels(data(cdb, x="ts", y=["y", "y2"], max_points=200))
    bucketed = data(cdb, x="ts", y=["y", "big"], max_points=200)
    assert bucketed.strategy == "time_bucket" and charts.needs_panels(bucketed)
    assert not charts.needs_panels(data(cdb, x="ts"))  # one column


def test_a_colour_column_with_several_y_columns_always_has_panels(cdb: Path) -> None:
    plot = data(cdb, x="ts", color="g", y=["y", "y2"], max_points=50_000)
    assert charts.needs_panels(plot)
    fig = charts.figure(plot, "light", "one")  # the chart rules win over the request
    assert_one_y_axis_per_panel(fig, 2)
    legend_entries = [t.name for t in fig.data if t.showlegend]
    assert sorted(legend_entries) == ["g0", "g1", "g2"]  # one legend entry per series
    assert charts.effective_layout(plot, "one") == "panels"


def test_the_dark_surface_uses_the_dark_slots(cdb: Path) -> None:
    fig = charts.figure(data(cdb, x="ts", color="g", max_points=300), "dark")
    colours = [t.line.color for t in fig.data if t.showlegend]
    assert colours == charts.palette("dark")[:3]
    assert fig.layout.template.layout.xaxis.gridcolor == "#2c2c2a"


def test_numeric_x_and_the_row_order(cdb: Path) -> None:
    by_x = charts.figure(data(cdb, x="x", max_points=100), "light")
    assert by_x.layout.xaxis.title.text == "x" and len(by_x.data) == 3
    by_row = charts.figure(data(cdb, max_points=100), "light")
    assert by_row.layout.xaxis.title.text == "(row order)"


def test_decimal_values_become_floats(cdb: Path) -> None:
    plot = data(cdb, x="ts", y=["dec"], max_points=50_000)
    assert type(plot.rows[0][1]).__name__ == "Decimal"
    fig = charts.figure(plot, "light")
    assert all(isinstance(v, float | int) for v in fig.data[0].y if v is not None)


# ---------------------------------------------------------------- scatter, histogram, bar


def test_scatter_markers_are_eight_pixels(cdb: Path) -> None:
    fig = charts.figure(data(cdb, "scatter", x="x", y=["y"], max_points=500), "light")
    assert len(fig.data) == 1 and fig.data[0].mode == "markers" and fig.data[0].marker.size == 8
    assert fig.data[0].type == "scatter" and len(fig.data[0].x) == 500
    assert fig.layout.hovermode != "x unified"
    big = charts.figure(data(cdb, "scatter", x="x", y=["y"], max_points=5_000), "light")
    assert big.data[0].type == "scattergl"


def test_scatter_series_are_capped_at_three_plus_other(cdb: Path) -> None:
    plot = data(cdb, "scatter", x="x", color="k", max_points=500)
    assert len(plot.series) == 4 and plot.series[-1] == "(other)"
    fig = charts.figure(plot, "light")
    assert [t.marker.color for t in fig.data] == [*charts.palette("light")[:3], charts.NEUTRAL]
    assert fig.layout.showlegend is True


def test_histogram_bars_have_a_gap_and_no_border(cdb: Path) -> None:
    plot = data(cdb, "histogram", bins=20)
    fig = charts.figure(plot, "light")
    assert len(fig.data) == 1 and fig.data[0].type == "bar" and fig.data[0].marker.line.width == 0
    assert fig.layout.template.layout.bargap >= 0.1
    bin_width = plot.rows[0][2] - plot.rows[0][1]
    assert fig.data[0].width[0] == pytest.approx(bin_width * 0.9)
    assert sum(fig.data[0].y) == ROWS and fig.layout.xaxis.title.text == "y"
    assert fig.layout.yaxis.title.text == "count" and fig.layout.showlegend is False
    assert_one_y_axis_per_panel(fig, 1)


def test_a_histogram_with_series_is_stacked(cdb: Path) -> None:
    plot = data(cdb, "histogram", color="g", bins=10)
    fig = charts.figure(plot, "light")
    assert fig.layout.barmode == "stack" and len(fig.data) == 3
    assert [t.marker.color for t in fig.data] == charts.palette("light")[:3]
    assert fig.layout.showlegend is True


def test_a_histogram_of_two_columns_has_a_panel_per_column(cdb: Path) -> None:
    plot = data(cdb, "histogram", y=["y", "big"], bins=10)
    assert charts.needs_panels(plot)
    fig = charts.figure(plot, "light", "one")
    assert_one_y_axis_per_panel(fig, 2)
    assert fig.layout.xaxis.title.text == "y" and fig.layout.xaxis2.title.text == "big"


def test_bars_put_other_in_grey(cdb: Path) -> None:
    plot = data(cdb, "bar", x="k", max_categories=4)
    fig = charts.figure(plot, "light")
    bar = fig.data[0]
    assert bar.type == "bar" and list(bar.x)[-1] == "(other)" and len(bar.x) == 5
    assert list(bar.marker.color) == [charts.palette("light")[0]] * 4 + [charts.NEUTRAL]
    assert fig.layout.showlegend is False and bar.marker.line.width == 0
    assert fig.layout.xaxis.type == "category" and fig.layout.yaxis.title.text == "count"
    assert fig.layout.template.layout.bargap >= 0.1


def test_bars_by_series_are_grouped_in_series_order(cdb: Path) -> None:
    plot = data(cdb, "bar", x="cat", color="g")
    fig = charts.figure(plot, "light")
    assert fig.layout.barmode == "group" and [t.name for t in fig.data] == ["g0", "g1", "g2"]
    assert [t.marker.color[0] for t in fig.data] == charts.palette("light")[:3]
    assert all(len(t.x) == 5 for t in fig.data) and fig.layout.showlegend is True


def test_bar_measures_with_different_scales_get_panels(cdb: Path) -> None:
    plot = data(cdb, "bar", x="g", y=["y", "big"], agg="max")
    assert charts.needs_panels(plot)
    fig = charts.figure(plot, "light", "panels")
    assert_one_y_axis_per_panel(fig, 2)
    assert [t.name for t in fig.data] == ["max(y)", "max(big)"]


# ---------------------------------------------------------------- the table view


def test_to_frame_matches_the_plotted_rows(cdb: Path) -> None:
    plot = data(cdb, x="ts", color="g", y=["y", "dec"], max_points=1000)
    frame = charts.to_frame(plot)
    assert list(frame.columns) == plot.columns and len(frame) == len(plot.rows)
    assert pd.api.types.is_datetime64_any_dtype(frame["ts"])
    assert pd.api.types.is_float_dtype(frame["dec_mean"])  # Decimal columns become floats
    assert frame["g"].tolist() == [r[1] for r in plot.rows]
    hist = charts.to_frame(data(cdb, "histogram", bins=10))
    assert list(hist.columns) == ["y_column", "bin_start", "bin_end", "count"]
