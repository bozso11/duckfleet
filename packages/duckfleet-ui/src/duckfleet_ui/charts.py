"""Plotly figures for ``PlotData`` (plan v0.3 Section 3.4, decision D-032).

Pure functions, no Streamlit widgets: ``figure`` turns the reduced data of
``duckfleet_core.plotdata`` into a ``plotly.graph_objects.Figure``. The rules are fixed:

1. Eight categorical colour slots per surface, assigned to series in the order of
   ``PlotData.series`` (or of ``spec.y`` when the series are the y columns) and never
   cycled; ``(other)`` is neutral grey, ``(null)`` the same grey at 60 % opacity.
2. One y axis. Columns that differ in scale by more than 100x, a colour column with
   several y columns and a histogram of several columns get one panel per y column.
3. 2 px lines, 8 px markers, the min to max band at 20 % opacity without an outline,
   hairline grid, bars with a gap and without borders.
4. A legend for two or more series, none for one; ``x unified`` hover on line charts.
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from duckfleet_core.plotdata import NULL_SERIES, OTHER, PlotData

Layout = Literal["one", "panels"]

# Eight slots, validated for colour-vision deficiency on their surface (D-032).
LIGHT_PALETTE = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948",
]  # fmt: skip
DARK_PALETTE = [
    "#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767",
]  # fmt: skip
NEUTRAL = "#898781"
NULL_OPACITY = 0.6
BAND_OPACITY = 0.2
PANEL_RATIO = 100.0  # TUNING(panel-ratio): scale ratio from which y columns get their own panels

_SURFACE = {
    "light": {"text": "#3d3d3a", "grid": "#e1e0d9", "axis": "#c3c2b7"},
    "dark": {"text": "#c3c2b7", "grid": "#2c2c2a", "axis": "#383835"},
}
_PANEL_HEIGHT = 300
_SINGLE_HEIGHT = 440
# TUNING(chart-render): scatter points from which Scattergl is used, the largest line that
# also shows its markers, and the point count above which scatter markers turn translucent
_WEBGL_FROM = 2_000
_MARKERS_UP_TO = 60
_OPAQUE_UP_TO = 1_000
_TRANSLUCENT = 0.7


def _mode(mode: str) -> str:
    return "dark" if mode == "dark" else "light"


def palette(mode: str) -> list[str]:
    """The eight series colours of the ``light`` or ``dark`` surface."""
    return list(DARK_PALETTE if _mode(mode) == "dark" else LIGHT_PALETTE)


def _rgba(hex_color: str, opacity: float) -> str:
    value = hex_color.lstrip("#")
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({red},{green},{blue},{opacity})"


def series_color(name: str | None, index: int, mode: str) -> str:
    """Colour of series number ``index`` (0-based among the real series); never cycled."""
    if name == OTHER:
        return NEUTRAL
    if name == NULL_SERIES:
        return _rgba(NEUTRAL, NULL_OPACITY)
    slots = palette(mode)
    return slots[index] if index < len(slots) else NEUTRAL


def template(mode: str) -> dict[str, Any]:
    """Plotly layout template: transparent surfaces, hairline grid, axis line, text colour."""
    colors = _SURFACE[_mode(mode)]
    axis = {
        "showgrid": True,
        "gridcolor": colors["grid"],
        "gridwidth": 1,
        "zeroline": False,
        "showline": True,
        "linecolor": colors["axis"],
        "linewidth": 1,
        "ticks": "outside",
        "tickcolor": colors["axis"],
        "automargin": True,
    }
    return {
        "layout": {
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "font": {"color": colors["text"]},
            "colorway": palette(mode),
            "xaxis": dict(axis),
            "yaxis": dict(axis),
            "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
            "hoverlabel": {"namelength": -1},
            "bargap": 0.1,
            "bargroupgap": 0.05,
            "margin": {"l": 10, "r": 10, "t": 30, "b": 10},
        }
    }


# ---------------------------------------------------------------- values


def _num(value: Any) -> Any:
    """Plain floats for Plotly: Decimal and NaN/infinity (a gap) are mapped."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _largest(values: list[Any]) -> float:
    """Largest absolute value among the plottable numbers (NULL, NaN and infinity are gaps)."""
    numbers = [abs(float(v)) for v in map(_num, values) if isinstance(v, int | float)]
    return max(numbers, default=0.0)


def _y_scales(data: PlotData) -> list[float]:
    """Largest absolute value per y column (or per measure), from the plotted rows."""
    spec = data.spec
    has_s = 1 if spec.color else 0
    if data.strategy in ("time_bucket", "numeric_bucket"):
        scales = []
        for j in range(len(spec.y)):
            base = 1 + has_s + 4 * j
            low = _largest([r[base + 1] for r in data.rows])
            high = _largest([r[base + 2] for r in data.rows])
            scales.append(max(low, high))
        return scales
    if data.strategy in ("raw", "sample"):
        return [_largest([r[1 + has_s + j] for r in data.rows]) for j in range(len(spec.y))]
    if data.strategy == "bar":
        count = max(1, len(spec.y))
        return [_largest([r[1 + has_s + j] for r in data.rows]) for j in range(count)]
    return []


def needs_panels(data: PlotData) -> bool:
    """Whether the y columns need one panel each (rule 2 of the chart rules)."""
    spec = data.spec
    columns = max(1, len(spec.y))
    if columns < 2:
        return False
    if spec.color or spec.kind == "histogram":
        return True  # a colour column with several y columns; histograms have one x axis per column
    positive = [s for s in _y_scales(data) if s > 0]
    # TUNING(panel-ratio)
    return len(positive) >= 2 and max(positive) / min(positive) > PANEL_RATIO


def to_frame(data: PlotData) -> pd.DataFrame:
    """The plotted rows as a DataFrame (table view and CSV download)."""
    frame = pd.DataFrame(data.rows, columns=data.columns)
    for name in frame.columns:
        column = frame[name]
        if column.dtype == object and len(column):
            first = next((v for v in column if v is not None), None)
            if isinstance(first, Decimal):
                frame[name] = column.map(lambda v: None if v is None else float(v))
            elif isinstance(first, datetime):
                frame[name] = pd.to_datetime(column)
    return frame


# ---------------------------------------------------------------- building blocks


def _canvas(panels: int, mode: str, shared_x: bool = True) -> go.Figure:
    if panels > 1:
        fig = make_subplots(
            rows=panels, cols=1, shared_xaxes=shared_x, vertical_spacing=min(0.12, 0.5 / panels)
        )
        height = _PANEL_HEIGHT * panels + 100
    else:
        fig = go.Figure()
        height = _SINGLE_HEIGHT
    base = template(mode)
    # The surface colours are also set on the layout itself: Streamlit's frontend fills in the
    # background and font of a figure whose own layout leaves them out, whatever its template says
    # (found in the browser, even with theme=None).
    fig.update_layout(
        template=base,
        height=height,
        paper_bgcolor=base["layout"]["paper_bgcolor"],
        plot_bgcolor=base["layout"]["plot_bgcolor"],
        font={"color": base["layout"]["font"]["color"]},
    )
    return fig


def _legend(fig: go.Figure, series: int) -> None:
    fig.update_layout(showlegend=series >= 2)


def _add(fig: go.Figure, trace: go.BaseTraceType, row: int, panels: int) -> None:
    if panels > 1:
        fig.add_trace(trace, row=row, col=1)
    else:
        fig.add_trace(trace)


def _axis_titles(fig: go.Figure, x_title: str | None, y_titles: list[str | None]) -> None:
    """Axis titles; the shared x axis is titled on the bottom panel only."""
    panels = len(y_titles)
    for index, title in enumerate(y_titles, start=1):
        if title:
            if panels > 1:
                fig.update_yaxes(title_text=title, row=index, col=1)
            else:
                fig.update_yaxes(title_text=title)
    if x_title:
        if panels > 1:
            fig.update_xaxes(title_text=x_title, row=panels, col=1)
        else:
            fig.update_xaxes(title_text=x_title)


def _split(rows: list[tuple], has_series: bool, names: list[str]) -> dict[str | None, list[tuple]]:
    """Rows per series name, each list in query order."""
    if not has_series:
        return {None: rows}
    grouped: dict[str | None, list[tuple]] = {name: [] for name in names}
    for row in rows:
        grouped.setdefault(row[1], []).append(row)
    return grouped


# ---------------------------------------------------------------- line and scatter


def _line_traces(fig: go.Figure, data: PlotData, mode: str, layout: Layout) -> tuple[int, int]:
    """Add the traces of a line or scatter chart; returns ``(panels, series count)``."""
    spec = data.spec
    has_s = bool(spec.color)
    bucketed = data.strategy in ("time_bucket", "numeric_bucket")
    y_columns = list(spec.y)
    panels = len(y_columns) if layout == "panels" else 1
    groups = _split(data.rows, has_s, data.series)
    # series of the chart: the colour values, or the y columns when there is no colour column
    series_names: list[str | None] = list(data.series) if has_s else [None]
    legend_series = len(series_names) if has_s else len(y_columns)

    for panel, panel_y in enumerate(y_columns if panels > 1 else [None], start=1):
        wanted = [(j, name) for j, name in enumerate(y_columns) if panel_y in (None, name)]
        for j, y_name in wanted:
            for index, series in enumerate(series_names):
                rows = groups.get(series, [])
                if not rows:
                    continue
                label = series if has_s else y_name
                color_index = index if has_s else j
                color = series_color(label if has_s else None, color_index, mode)
                base = 1 + (1 if has_s else 0) + (4 * j if bucketed else j)
                x = [r[0] for r in rows]
                show = legend_series >= 2 and (panel == 1 or not has_s)
                common = {"legendgroup": str(label), "showlegend": show, "name": str(label)}
                if spec.kind == "scatter":
                    y = [_num(r[base]) for r in rows]
                    # TUNING(chart-render)
                    trace_type = go.Scattergl if len(rows) >= _WEBGL_FROM else go.Scatter
                    # overplotting shows as density
                    opacity = _TRANSLUCENT if len(rows) > _OPAQUE_UP_TO else 1.0
                    marker = {"size": 8, "color": color, "opacity": opacity}
                    trace = trace_type(x=x, y=y, mode="markers", marker=marker, **common)
                    _add(fig, trace, panel, panels)
                    continue
                if bucketed:
                    low = [_num(r[base + 1]) for r in rows]
                    high = [_num(r[base + 2]) for r in rows]
                    mean = [_num(r[base]) for r in rows]
                    off = {
                        "hoverinfo": "skip",
                        "showlegend": False,
                        "legendgroup": common["legendgroup"],
                    }
                    lower = go.Scatter(x=x, y=low, mode="lines", line={"width": 0}, **off)
                    _add(fig, lower, panel, panels)
                    _add(
                        fig,
                        go.Scatter(
                            x=x, y=high, mode="lines", line={"width": 0}, fill="tonexty",
                            fillcolor=_rgba(_hex(color), BAND_OPACITY), **off,
                        ),  # fmt: skip
                        panel,
                        panels,
                    )
                    extra = {
                        "customdata": list(zip(low, high, strict=True)),
                        "hovertemplate": (
                            "%{y:.4g} (min %{customdata[0]:.4g}, max %{customdata[1]:.4g})"
                        ),
                    }
                else:
                    mean = [_num(r[base]) for r in rows]
                    extra = {}
                points = len(rows)
                _add(
                    fig,
                    go.Scatter(
                        x=x, y=mean, line={"width": 2, "color": color},
                        # TUNING(chart-render)
                        mode="lines+markers" if points <= _MARKERS_UP_TO else "lines",
                        marker={"size": 8, "color": color}, connectgaps=False, **common, **extra,
                    ),  # fmt: skip
                    panel,
                    panels,
                )
    return panels, legend_series


def _hex(color: str) -> str:
    """The solid colour behind a series colour, for the band fill (rgba keeps its RGB)."""
    if color.startswith("#"):
        return color
    parts = color.removeprefix("rgba(").removesuffix(")").split(",")
    return "#" + "".join(f"{int(p):02x}" for p in parts[:3])


# ---------------------------------------------------------------- histogram and bar


def _histogram_traces(fig: go.Figure, data: PlotData, mode: str, layout: Layout) -> tuple[int, int]:
    spec = data.spec
    has_s = bool(spec.color)
    columns = list(spec.y)
    panels = len(columns) if layout == "panels" else 1
    names = list(data.series) if has_s else [None]
    # series of the chart: the colour values, or the y columns when there is no colour column
    # (a histogram of two columns has a legend like a line chart of two columns, W-13)
    legend_series = len(names) if has_s else len(columns)
    for panel, column in enumerate(columns if panels > 1 else columns[:1], start=1):
        rows = [r for r in data.rows if r[0] == column]
        groups: dict[str | None, list[tuple]] = {n: [] for n in names}
        for row in rows:
            groups.setdefault(row[3] if has_s else None, []).append(row)
        for index, series in enumerate(names):
            part = groups.get(series, [])
            if not part:
                continue
            color_index = index if has_s else columns.index(column)
            color = series_color(series, color_index, mode)
            widths = [(r[2] - r[1]) * 0.9 for r in part]  # a 10 % gap between the bars
            fig.add_trace(
                go.Bar(
                    x=[(r[1] + r[2]) / 2 for r in part],
                    y=[r[-1] for r in part],
                    width=widths,
                    marker={"color": color, "line": {"width": 0}},
                    name=str(series) if has_s else column,
                    legendgroup=str(series) if has_s else column,
                    showlegend=legend_series >= 2 and (panel == 1 or not has_s),
                    customdata=[(r[1], r[2]) for r in part],
                    hovertemplate="%{customdata[0]:.4g} to %{customdata[1]:.4g}: %{y:,}",
                ),
                row=panel if panels > 1 else None,
                col=1 if panels > 1 else None,
            )
    fig.update_layout(barmode="stack")
    return panels, legend_series


def _bar_traces(fig: go.Figure, data: PlotData, mode: str, layout: Layout) -> tuple[int, int]:
    spec = data.spec
    has_s = bool(spec.color)
    measures = max(1, len(spec.y))
    measure_names = data.columns[(2 if has_s else 1) :]
    panels = measures if layout == "panels" else 1
    categories: list[str] = []
    for row in data.rows:
        if row[0] not in categories:
            categories.append(row[0])
    names = list(data.series) if has_s else [None]
    legend_series = len(names) if has_s else measures
    for panel in range(1, panels + 1):
        for j in range(measures):
            if panels > 1 and j != panel - 1:
                continue
            for index, series in enumerate(names):
                values: dict[str, Any] = {}
                for row in data.rows:
                    if (row[1] if has_s else None) == series:
                        values[row[0]] = _num(row[(2 if has_s else 1) + j])
                if not values:
                    continue
                label = series if has_s else measure_names[j]
                color_index = index if has_s else j
                color = series_color(series if has_s else None, color_index, mode)
                colors = [NEUTRAL if (c == OTHER and not has_s) else color for c in categories]
                fig.add_trace(
                    go.Bar(
                        x=categories,
                        y=[values.get(c) for c in categories],
                        name=str(label),
                        marker={"color": colors, "line": {"width": 0}},
                        legendgroup=str(label),
                        showlegend=legend_series >= 2 and (panel == 1 or not has_s),
                    ),
                    row=panel if panels > 1 else None,
                    col=1 if panels > 1 else None,
                )
    fig.update_layout(barmode="group")
    fig.update_xaxes(type="category")
    return panels, legend_series


# ---------------------------------------------------------------- figure


def effective_layout(data: PlotData, layout: Layout) -> Layout:
    """``layout`` after the rules that fix it (colour with several y columns, histograms)."""
    spec = data.spec
    if len(spec.y) > 1 and (spec.color or spec.kind == "histogram"):
        return "panels"  # fixed by the chart rules, whatever the page asked for
    return layout if len(spec.y) > 1 else "one"


def figure(data: PlotData, mode: str, layout: Layout = "one") -> go.Figure:
    """The Plotly figure of ``data`` on the ``light`` or ``dark`` surface."""
    layout = effective_layout(data, layout)
    spec = data.spec
    if spec.kind == "bar":
        measures = max(1, len(spec.y))
        panels = measures if layout == "panels" else 1
    else:
        panels = len(spec.y) if layout == "panels" else 1
    fig = _canvas(panels, mode, shared_x=spec.kind != "histogram")
    if spec.kind == "histogram":
        used, series = _histogram_traces(fig, data, mode, layout)
        _axis_titles(fig, None, ["count"] * used)
        if used > 1:
            for panel, column in enumerate(spec.y, start=1):
                fig.update_xaxes(title_text=column, row=panel, col=1)
        else:
            fig.update_xaxes(title_text=spec.y[0])
    elif spec.kind == "bar":
        used, series = _bar_traces(fig, data, mode, layout)
        names = data.columns[(2 if spec.color else 1) :]
        titles = list(names) if used > 1 else [names[0] if len(names) == 1 else None]
        _axis_titles(fig, data.columns[0], titles)
    else:
        used, series = _line_traces(fig, data, mode, layout)
        if used > 1:
            titles: list[str | None] = list(spec.y)
        else:
            titles = [spec.y[0] if len(spec.y) == 1 else None]
        _axis_titles(fig, data.columns[0], titles)
        if spec.kind == "line":
            fig.update_layout(hovermode="x unified")
    _legend(fig, series)
    return fig
