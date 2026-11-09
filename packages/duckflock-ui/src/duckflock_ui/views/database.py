"""Database page: one database of the scanned flock, its tables, columns, summary and preview.

Everything shown before a button press comes from the catalog (no query runs);
the summary and the preview open the database read-only through the query layer
(``query_access``), under the timeout of ``QueryConfig``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from duckflock_core.plotdata import ROW_ORDER
from duckflock_core.profiler import is_numeric_type, is_temporal_type
from duckflock_core.query import QueryError
from duckflock_core.sqlutil import qualified_name
from duckflock_ui import catalog_access as access
from duckflock_ui import common
from duckflock_ui import query_access as queries

# ---------------------------------------------------------------- helpers


def _header(info: dict[str, Any], state: str) -> None:
    st.subheader(info["rel_path"])
    size_mb = info["size_bytes"] / 1_000_000
    facts = [f"{size_mb:,.2f} MB"]
    for label, key in (("strict family", "family_strict"), ("loose family", "family_loose")):
        if info.get(key):
            facts.append(f"{label} {info[key]}")
    if info.get("time_min") is not None and info.get("time_max") is not None:
        facts.append(f"{info['time_min']:%Y-%m-%d %H:%M} to {info['time_max']:%Y-%m-%d %H:%M}")
    st.caption("  |  ".join(facts))
    if info["tags"]:
        st.caption("Tags: " + ", ".join(f"{k}={v}" for k, v in info["tags"].items()))
    if info["health"]:
        st.caption("Health: " + ", ".join(info["health"]))
    if state == common.CHANGED:
        st.caption(
            "Changed since the last scan: row counts and statistics below come from the scan."
        )


def _tables_table(tables: pd.DataFrame) -> None:
    st.dataframe(
        tables,
        hide_index=True,
        width="stretch",
        column_config={
            "schema_name": st.column_config.TextColumn("schema"),
            "table_name": st.column_config.TextColumn("name"),
            "row_count": st.column_config.NumberColumn("rows", format="localized"),
            "n_columns": st.column_config.NumberColumn("columns"),
            "time_min": st.column_config.DatetimeColumn("time min", format="YYYY-MM-DD HH:mm"),
            "time_max": st.column_config.DatetimeColumn("time max", format="YYYY-MM-DD HH:mm"),
        },
    )


def _show_error(error: QueryError) -> None:
    st.error(f"{error.kind}: {error.message}")


# ---------------------------------------------------------------- plot hand-off


def _plot_prefill(rel_path: str, table_label: str, columns: pd.DataFrame) -> dict[str, Any]:
    """What the Plot page preselects: x is the first temporal column (else the row order), y the
    first numeric column.
    """
    types = dict(zip(columns["column_name"], columns["data_type"], strict=True))
    temporal = [name for name, data_type in types.items() if is_temporal_type(data_type)]
    numeric = [name for name, data_type in types.items() if is_numeric_type(data_type)]
    return {
        "rel_path": rel_path,
        "table": table_label,
        "x": temporal[0] if temporal else ROW_ORDER,
        "y": numeric[0] if numeric else None,
    }


def _prepare_sql(text: str) -> None:
    """Button callback: the query for the SQL page, which then works on this one database."""
    st.session_state["sql_text"] = text
    st.session_state["scope_mode"] = "one"  # the template is about this database's table (X-09)


def _prepare_plot(prefill: dict[str, Any]) -> None:
    """Button callback (runs before the rerun, so widget state may still be set)."""
    st.session_state["selected_table"] = prefill["table"]
    st.session_state["plot_prefill"] = prefill
    st.session_state["scope_mode"] = "one"  # the hand-off is about this database's table


# ---------------------------------------------------------------- tabs


def _columns_tab(path: str, mtime: int, rel_path: str, schema: str, table: str) -> None:
    columns = access.columns_frame(path, mtime, rel_path, schema, table)
    if columns.empty:
        st.info("The scan recorded no columns for this table.")
        return
    st.dataframe(
        columns,
        hide_index=True,
        width="stretch",
        column_config={
            "ordinal": st.column_config.NumberColumn("#"),
            "column_name": st.column_config.TextColumn("column"),
            "data_type": st.column_config.TextColumn("type"),
            "null_share": st.column_config.ProgressColumn(
                "null share", format="percent", min_value=0.0, max_value=1.0
            ),
            "min_value": st.column_config.TextColumn("min"),
            "max_value": st.column_config.TextColumn("max"),
            "approx_distinct": st.column_config.NumberColumn("approx distinct"),
        },
    )
    st.caption(
        "Cheap statistics from the scan. Where 'sampled' is set they describe a sample "
        "(the null share is relative to the sample size)."
    )


def _show_summary(summary: Any) -> None:
    if summary.error:
        st.error(f"The summary failed: {summary.error}")
        return
    st.dataframe(queries.summary_frame(summary), hide_index=True, width="stretch")
    parts = [f"computed {summary.computed_at.replace('+00:00', ' UTC')}",
             f"{summary.elapsed_s:.2f} s"]  # fmt: skip
    if summary.sampled:
        parts.append(f"sampled: {summary.sample_rows:,} of {summary.row_count:,} rows")
    else:
        parts.append("all rows")
    st.caption("  |  ".join(parts))


def _summary_tab(
    cache: Path, root: str, info: dict[str, Any], schema: str, table: str, row_count: int | None,
    available: bool,
) -> None:  # fmt: skip
    if not available:
        st.info("The database file is missing; the summary needs the file.")
        return
    args = (cache, root, info["rel_path"], info["abs_path"], schema, table)
    summary = queries.cached_summary(*args)
    if summary is None:
        st.caption(
            "A summary reads every row of the table (a sample above the sampling threshold). "
            "It is computed on request and cached until the file changes."
        )
        if st.button("Compute summary", key=f"compute_summary|{info['rel_path']}|{schema}.{table}"):
            with st.spinner("Computing the summary..."):
                summary = queries.compute_summary(*args, row_count)
            if not summary.error:
                st.rerun()  # the button now reads "Recompute" (V-09); a failure stays on screen
    elif st.button("Recompute", key=f"recompute_summary|{info['rel_path']}|{schema}.{table}"):
        with st.spinner("Computing the summary..."):
            summary = queries.compute_summary(*args, row_count)
    if summary is not None:
        _show_summary(summary)


def _preview_tab(
    info: dict[str, Any], live: tuple[int, int] | None, schema: str, table: str,
    column_names: list[str], row_count: int | None, available: bool,
) -> None:  # fmt: skip
    if not available or live is None:
        st.info("The database file is missing; the preview needs the file.")
        return
    config = queries.query_config()
    sig = f"{info['rel_path']}|{schema}.{table}"
    default_columns = column_names[: config.preview_max_columns]
    left, middle, right = st.columns([3, 2, 1])
    chosen = left.multiselect(
        "Columns", column_names, default=default_columns, key=f"preview_columns|{sig}"
    )
    sort_by = middle.selectbox(
        "Sort by", ["(none)", *column_names], key=f"preview_sort|{sig}"
    )
    descending = right.checkbox("Descending", key=f"preview_desc|{sig}")
    if len(column_names) > config.preview_max_columns:
        st.caption(
            f"Showing the first {config.preview_max_columns} of {len(column_names)} columns by "
            "default; add more in the column selector."
        )
    pages = max(1, math.ceil(row_count / config.preview_rows)) if row_count is not None else None
    page = st.number_input(
        "Page", min_value=1, max_value=pages or 100_000, value=1, step=1, key=f"preview_page|{sig}"
    )
    if not chosen:
        st.info("Choose at least one column.")
        return
    offset = (int(page) - 1) * config.preview_rows
    try:
        frame, more, as_text, elapsed = queries.preview_page(
            info["abs_path"], live[0], live[1], schema, table,
            tuple(chosen), None if sort_by == "(none)" else sort_by, bool(descending),
            offset, config.preview_rows,
        )  # fmt: skip
    except QueryError as error:
        _show_error(error)
        return
    first, last = offset + 1, offset + len(frame)
    shown = f"rows {first:,} to {last:,}" if len(frame) else "no rows on this page"
    total = f" of {row_count:,}" if row_count is not None else (" (more follow)" if more else "")
    st.dataframe(frame, hide_index=True, width="stretch")
    note = f"{shown}{total}  |  {elapsed:.2f} s"
    if as_text:
        note += "  |  shown as text: " + ", ".join(as_text)
    st.caption(note)


# ---------------------------------------------------------------- page


def render() -> None:
    """Render the Database page."""
    from duckflock_ui import nav  # here, not at module level: nav imports this module

    cache = common.cache_dir()
    common.init_state(cache)
    st.title("Database")

    root = st.session_state.get("active_root")
    key = access.catalog_key(common.catalog_path(cache, root)) if root else None
    if key is None:
        st.info("No flock is loaded. Scan a root folder or load a catalog on the Overview page.")
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
    _header(info, state)

    if info["status"] != "ok":
        st.error(
            f"This database could not be read in the last scan: {info['error_type']}: "
            f"{info['error_message']}"
        )
        return
    if state == common.MISSING:
        st.warning(
            "The file is missing since the last scan, so nothing can be queried. "
            "Rescan on the Overview page."
        )

    tables = access.tables_frame(path, mtime, rel_path)
    if tables.empty:
        st.info("This database holds no tables or views.")
        return
    _tables_table(tables)

    labels = [f"{s}.{t}" for s, t in zip(tables["schema_name"], tables["table_name"], strict=True)]
    common.keep_valid("selected_table", labels)
    chosen = st.selectbox("Table", labels, key="selected_table", persist_state="session")
    position = labels.index(chosen)
    schema, table = tables.loc[position, "schema_name"], tables.loc[position, "table_name"]
    row_count = tables.loc[position, "row_count"]
    row_count = None if pd.isna(row_count) else int(row_count)

    columns = access.columns_frame(path, mtime, rel_path, schema, table)
    available = state != common.MISSING
    # Lazy tabs: only the selected tab runs, so a preview query never starts unasked.
    columns_tab, summary_tab, preview_tab = st.tabs(
        ["Columns", "Summary", "Preview"], key="database_tabs", on_change="rerun"
    )
    with columns_tab:
        if columns_tab.open:
            _columns_tab(path, mtime, rel_path, schema, table)
    with summary_tab:
        if summary_tab.open:
            _summary_tab(cache, root, info, schema, table, row_count, available)
    with preview_tab:
        if preview_tab.open:
            _preview_tab(
                info, live, schema, table, columns["column_name"].tolist(), row_count, available
            )

    sql_col, plot_col, _ = st.columns([1, 1, 6])
    if sql_col.button(
        "Open in SQL",
        key="open_in_sql",
        disabled=not available,
        on_click=_prepare_sql,
        args=(f"SELECT * FROM {qualified_name(schema, table)} LIMIT 100",),
    ):
        st.switch_page(nav.PAGES["sql"])
    if plot_col.button(
        "Plot this table",
        key="plot_this_table",
        disabled=not available,
        on_click=_prepare_plot,
        args=(_plot_prefill(rel_path, chosen, columns),),
    ):
        st.switch_page(nav.PAGES["plot"])
