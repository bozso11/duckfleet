"""Plot page: a chart of one table of the fleet, reduced inside DuckDB to a point budget.

The chart kind sits above the form because the choices below it depend on it; the rest of
the controls share one form, so **Plot** submits every value together. The request (not
the data) is kept in the session state: a rerun caused by another widget or a download
draws the chart again from Streamlit's cache (``query_access.plot_data``, keyed by the
file's live size and mtime and the spec), and a failed request keeps its error instead of
running the query again. Colours and layout come from ``charts`` (decision D-032).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from duckfleet_core.plotdata import ROW_ORDER, PlotData, PlotSpec
from duckfleet_core.profiler import is_numeric_type, is_temporal_type
from duckfleet_core.query import QueryError
from duckfleet_ui import catalog_access as access
from duckfleet_ui import charts, common
from duckfleet_ui import query_access as queries

KINDS = ["line", "scatter", "histogram", "bar"]
AGGS = ["mean", "sum", "min", "max", "count"]
NONE = "(none)"
LAYOUTS = {"auto": "Automatic", "one": "One axis", "panels": "One panel per column"}
THEMES = {"auto": "Automatic", "light": "Light", "dark": "Dark"}
MIN_POINTS, MAX_POINTS, DEFAULT_POINTS = 100, 500_000, 50_000
MIN_BINS, MAX_BINS, DEFAULT_BINS = 5, 500, 50
MAX_SERIES_CAP = 8
LOW_CARDINALITY = 50  # colour candidates: columns with at most this many distinct values
HINTS = {
    "read_only": "duckfleet opens databases read-only.",
    "blocked": "File access, extensions and settings are disabled here.",
    "timeout": "Narrow the filter, lower the point budget or raise the timeout (SQL page).",
}
SCALE_NOTE = (
    "Shown in one panel per column: the largest values of the columns differ by more than "
    f"{charts.PANEL_RATIO:.0f}x, and a second y axis would suggest a relation that is not there."
)


# ---------------------------------------------------------------- pure helpers


def theme_mode(choice: str = "auto") -> str:
    """``light`` or ``dark``: the page option, else Streamlit's theme, else light."""
    if choice in ("light", "dark"):
        return choice
    try:
        detected = st.context.theme.type
    except Exception:  # an old Streamlit or no theme information yet
        detected = None
    return detected if detected in ("light", "dark") else "light"


def column_choices(columns: pd.DataFrame, kind: str) -> dict[str, list[str]]:
    """The x, y and colour options of a table for a chart kind.

    x of a line or scatter chart: temporal columns first, numeric next, then the row order;
    any column for a bar chart (few distinct values first); y: numeric columns; colour:
    ``(none)``, the columns with few distinct values (``approx_distinct`` of the scan), the rest.
    """
    names = columns["column_name"].tolist()
    types = dict(zip(names, columns["data_type"].tolist(), strict=True))
    distinct = dict(zip(names, columns["approx_distinct"].tolist(), strict=True))

    def few(name: str) -> bool:
        value = distinct.get(name)
        return value is not None and not pd.isna(value) and value <= LOW_CARDINALITY

    temporal = [n for n in names if is_temporal_type(types[n])]
    numeric = [n for n in names if is_numeric_type(types[n])]
    by_cardinality = [n for n in names if few(n)] + [n for n in names if not few(n)]
    return {
        "x": by_cardinality if kind == "bar" else [*temporal, *numeric, ROW_ORDER],
        "y": numeric,
        "color": [NONE, *by_cardinality],
    }


def describe(data: PlotData) -> str:
    """What the chart shows and how the data was reduced, in words (the caption)."""
    rows = f"{data.rows_scanned:,}"
    shown = len(data.rows)
    if data.strategy == "raw":
        every = "every one of" if shown == data.rows_scanned else f"{shown:,} of"
        text = f"{data.points:,} points, {every} {rows} rows"
    elif data.strategy in ("time_bucket", "numeric_bucket"):
        text = (
            f"{data.points:,} points from {rows} rows: bucketed to {data.bucket}, "
            "mean with min to max band"
        )
    elif data.strategy == "sample":
        text = f"reservoir sample of {shown:,} of {rows} rows"
    elif data.strategy == "histogram":
        text = (
            f"{rows} rows in {data.spec.bins} equal-width bins "
            "(bins and series are bounded by their own limits, not by the point budget)"
        )
    else:
        text = f"{shown:,} categories from {rows} rows"
        if data.folded_categories:
            text += f", {data.folded_categories:,} folded into (other)"
        text += " (categories are bounded by their own limit, not by the point budget)"
    if data.folded_series:
        real = [s for s in data.series if s not in ("(other)", "(null)")]
        text += f"; {len(real)} of {len(real) + data.folded_series} series shown, "
        text += f"{data.folded_series} folded into (other)"
    return text


def csv_bytes(data: PlotData) -> bytes:
    """The plotted data as UTF-8 CSV with LF line ends."""
    return charts.to_frame(data).to_csv(index=False, lineterminator="\n").encode("utf-8")


def default_table(tables: pd.DataFrame, labels: list[str]) -> str:
    """The table shown first: one with a time window and rows (worth plotting), else the first."""
    for label, rows, start in zip(labels, tables["row_count"], tables["time_min"], strict=True):
        if not pd.isna(start) and not pd.isna(rows) and rows > 0:
            return label
    return labels[0]


def _file_stem(rel_path: str, table: str) -> str:
    return re.sub(r"[^\w.-]+", "_", f"{Path(rel_path).stem}_{table}") or "plot"


# ---------------------------------------------------------------- the form


def _default_selection(kind: str, choices: dict[str, list[str]]) -> dict[str, Any]:
    x_options, y_options = choices["x"], choices["y"]
    return {
        "x": x_options[0] if x_options else None,
        "y": y_options[:1] if kind != "bar" else [],
        "color": NONE,
        "where": "",
    }


def _drop_stale(base: str, choices: dict[str, list[str]]) -> None:
    """Forget remembered selections that are no longer among the options (after a rescan)."""
    state = st.session_state
    for field in ("x", "color"):
        if state.get(f"plot_{field}|{base}", choices[field][0]) not in choices[field]:
            del state[f"plot_{field}|{base}"]
    key = f"plot_y|{base}"
    if key in state:
        state[key] = [name for name in state[key] if name in choices["y"]]


def _form(
    kind: str, base: str, choices: dict[str, list[str]], available: bool
) -> tuple[bool, dict[str, Any]]:
    """The controls and the submit button; returns ``(submitted, values)``."""
    state = st.session_state
    _drop_stale(base, choices)
    for field, value in _default_selection(kind, choices).items():
        state.setdefault(f"plot_{field}|{base}", value)
    state.setdefault("plot_max_points", DEFAULT_POINTS)
    state.setdefault("plot_bins", DEFAULT_BINS)
    state.setdefault("plot_agg", "mean")
    state.setdefault("plot_layout", "auto")
    state.setdefault("plot_max_series", 0)
    state.setdefault("plot_theme", "auto")
    with st.form("plot"):
        left, middle, right = st.columns([2, 3, 2])
        if kind != "histogram":
            label, help_text = (
                ("Category (x)", "Any column; the most frequent categories are kept.")
                if kind == "bar"
                else ("X axis", "Temporal columns first, then numeric ones, then the row order.")
            )
            left.selectbox(
                label, choices["x"], key=f"plot_x|{base}", persist_state="session", help=help_text
            )
        middle.multiselect(
            "Y columns" if kind != "bar" else "Y columns (optional: count rows when empty)",
            choices["y"],
            key=f"plot_y|{base}",
            persist_state="session",
        )
        right.selectbox(
            "Colour (series)",
            choices["color"],
            key=f"plot_color|{base}",
            persist_state="session",
            help="Columns with few distinct values first.",
        )
        st.text_input(
            "Filter (SQL condition)",
            key=f"plot_where|{base}",
            persist_state="session",
            placeholder="value > 0 AND run_id = 3",
        )
        with st.expander("Options"):
            one, two, three = st.columns(3)
            one.number_input(
                "Max points",
                min_value=MIN_POINTS,
                max_value=MAX_POINTS,
                step=1000,
                key="plot_max_points",
                persist_state="session",
                help="The browser never receives more points than this; DuckDB reduces the data.",
            )
            two.number_input(
                "Histogram bins",
                min_value=MIN_BINS,
                max_value=MAX_BINS,
                key="plot_bins",
                persist_state="session",
            )
            three.selectbox(
                "Bar measure", AGGS, key="plot_agg", persist_state="session",
                help="Applied to the first y column; without y columns rows are counted.",
            )  # fmt: skip
            one.selectbox(
                "Layout",
                list(LAYOUTS),
                format_func=LAYOUTS.get,
                key="plot_layout",
                persist_state="session",
                help="Several y columns with very different scales get one panel each.",
            )
            two.number_input(
                "Series cap (0 = default)",
                min_value=0,
                max_value=MAX_SERIES_CAP,
                key="plot_max_series",
                persist_state="session",
                help="Default 8, or 3 for a scatter chart; the rest is folded into (other).",
            )
            three.selectbox(
                "Chart theme",
                list(THEMES),
                format_func=THEMES.get,
                key="plot_theme",
                persist_state="session",
            )
        submitted = st.form_submit_button("Plot", type="primary", disabled=not available)
    values = {
        "x": state.get(f"plot_x|{base}") if kind != "histogram" else None,
        "y": list(state.get(f"plot_y|{base}") or []),
        "color": state.get(f"plot_color|{base}"),
        "where": state.get(f"plot_where|{base}") or "",
    }
    return submitted, values


def _message(exc: ValueError) -> str:
    """The plain text of a spec problem (a pydantic error carries one entry per field)."""
    if isinstance(exc, ValidationError):
        return "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
    return str(exc)


def _spec(kind: str, schema: str, table: str, values: dict[str, Any]) -> PlotSpec:
    state = st.session_state
    x = values["x"]
    color = values["color"]
    return PlotSpec(
        schema_name=schema,
        table_name=table,
        kind=kind,  # type: ignore[arg-type]
        x=None if x in (None, ROW_ORDER) else x,
        y=values["y"],
        color=None if color in (None, NONE) else color,
        where=values["where"] or None,
        max_points=int(state["plot_max_points"]),
        bins=int(state["plot_bins"]),
        agg=state["plot_agg"],
        max_series=int(state["plot_max_series"]) or None,
    )


# ---------------------------------------------------------------- showing a result


def _show_error(kind: str, message: str) -> None:
    hint = HINTS.get(kind)
    st.error(f"{kind}: {message}" + (f"\n\n{hint}" if hint else ""))


def _panel_note(data: PlotData, choice: str) -> str:
    spec = data.spec
    if spec.color:
        return "One panel per y column, because a colour column is used with several y columns."
    if spec.kind == "histogram":
        return "One panel per column: every column has its own value axis."
    if choice == "panels":
        return "One panel per y column, as chosen in the options."
    return SCALE_NOTE


def _show_data(data: PlotData, info: dict[str, Any], table_label: str) -> None:
    if not data.rows:
        st.info("Nothing to plot: the table is empty or the filter matched no rows.")
        return
    choice = st.session_state.get("plot_layout", "auto")
    layout = ("panels" if charts.needs_panels(data) else "one") if choice == "auto" else choice
    figure = charts.figure(data, theme_mode(st.session_state.get("plot_theme", "auto")), layout)
    st.plotly_chart(figure, theme=None, width="stretch", key="plot_chart",
                    config={"displaylogo": False})  # fmt: skip
    if charts.effective_layout(data, layout) == "panels":
        st.caption(_panel_note(data, choice))
    st.caption(f"{describe(data)}  |  {data.elapsed_s:.2f} s")
    for note in data.notes:
        st.caption(note)
    with st.expander("Plotted data"):
        frame = charts.to_frame(data)
        st.dataframe(frame, hide_index=True, width="stretch")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        st.download_button(
            "Download CSV",
            data=csv_bytes(data),
            file_name=f"{_file_stem(info['rel_path'], table_label)}_{data.spec.kind}_{stamp}.csv",
            mime="text/csv",
            key="plot_csv",
            on_click="ignore",
        )
    with st.expander("SQL"):
        st.code(data.sql, language="sql")


def _show_request(
    request: dict[str, Any], info: dict[str, Any], live: tuple[int, int] | None
) -> None:
    if request.get("error"):
        _show_error(*request["error"])
        return
    if live is None:
        st.warning("The file is missing since the last scan, so nothing can be plotted.")
        return
    try:
        with st.spinner("Plotting..."):
            data = queries.plot_data(info["abs_path"], live[0], live[1], request["spec_json"])
    except QueryError as error:
        request["error"] = (error.kind, error.message)  # not re-run by the next rerun
        _show_error(error.kind, error.message)
        return
    _show_data(data, info, request["table"])


# ---------------------------------------------------------------- page


def _apply_prefill(rel_path: str, table_label: str, choices_by_kind: dict[str, dict]) -> None:
    """Preselect what the **Plot this table** button of the Database page asked for."""
    prefill = st.session_state.get("plot_prefill")
    if not prefill:
        return
    if prefill.get("rel_path") == rel_path and prefill.get("table") == table_label:
        base = f"{rel_path}|{table_label}|line"
        st.session_state["plot_kind"] = "line"
        if prefill.get("x") in choices_by_kind["line"]["x"]:
            st.session_state[f"plot_x|{base}"] = prefill["x"]
        if prefill.get("y") in choices_by_kind["line"]["y"]:
            st.session_state[f"plot_y|{base}"] = [prefill["y"]]
    st.session_state.pop("plot_prefill", None)


def render() -> None:
    """Render the Plot page."""
    from duckfleet_ui import nav  # here, not at module level: nav imports this module

    cache = common.cache_dir()
    common.init_state(cache)
    st.title("Plot")

    root = st.session_state.get("active_root")
    key = access.catalog_key(common.catalog_path(cache, root)) if root else None
    if key is None:
        st.info("No fleet is loaded. Scan a root folder or load a catalog on the Overview page.")
        st.page_link(nav.PAGES["overview"], label="Open the Overview")
        return
    path, mtime = key

    rel_path = common.pick_database(access.databases(path, mtime))
    if rel_path is None:
        st.info("The catalog holds no database.")
        return
    info = access.database_info(path, mtime, rel_path)
    if not info:
        st.warning(f"{rel_path} is not in the catalog any more. Scan again.")
        return
    state, live = common.file_state(info)
    if info["status"] != "ok":
        st.error(
            f"This database could not be read in the last scan: {info['error_type']}: "
            f"{info['error_message']}"
        )
        return
    if state == common.MISSING:
        st.warning(
            "The file is missing since the last scan, so nothing can be plotted. "
            "Rescan on the Overview page."
        )
    elif state == common.CHANGED:
        st.caption("Changed since the last scan: the plot reads the file as it is now.")

    tables = access.tables_frame(path, mtime, rel_path)
    if tables.empty:
        st.info("This database holds no tables or views.")
        return
    labels = [f"{s}.{t}" for s, t in zip(tables["schema_name"], tables["table_name"], strict=True)]
    common.keep_valid("selected_table", labels)
    st.session_state.setdefault("selected_table", default_table(tables, labels))
    chosen = st.selectbox(
        "Table", labels, key="selected_table", persist_state="session",
        help="Shared with the Database page.",
    )  # fmt: skip
    position = labels.index(chosen)
    schema, table = tables.loc[position, "schema_name"], tables.loc[position, "table_name"]
    columns = access.columns_frame(path, mtime, rel_path, schema, table)
    if columns.empty:
        st.info("The scan recorded no columns for this table.")
        return

    choices_by_kind = {kind: column_choices(columns, kind) for kind in KINDS}
    _apply_prefill(rel_path, chosen, choices_by_kind)
    kind = st.radio("Chart", KINDS, horizontal=True, key="plot_kind", persist_state="session")
    choices = choices_by_kind[kind]
    if kind != "bar" and not choices["y"]:
        st.info("This table has no numeric column to plot. A bar chart can count its rows.")
        return

    base = f"{rel_path}|{chosen}|{kind}"
    submitted, values = _form(kind, base, choices, available=state != common.MISSING)
    if submitted:
        try:
            spec = _spec(kind, schema, table, values)
            request = {"rel_path": rel_path, "table": chosen, "spec_json": spec.model_dump_json()}
        except (ValidationError, ValueError) as exc:
            message = _message(exc)
            request = {"rel_path": rel_path, "table": chosen, "error": ("sql", message)}
        st.session_state["plot_request"] = request

    request = st.session_state.get("plot_request")
    if request and request["rel_path"] == rel_path and request["table"] == chosen:
        _show_request(request, info, live)
