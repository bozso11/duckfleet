"""SQL page: a read-only SQL editor on one database of the fleet.

The text runs as given on a sandboxed read-only connection (``query_access``,
decision D-021): no writes, no file access, no extensions, no ``SET``; a row cap
and a timeout apply. The last result and the prepared download live in the
session state so they survive reruns caused by other widgets.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import streamlit as st

from duckfleet_core.query import MissingDependencyError, QueryError, export_query, run_query
from duckfleet_ui import catalog_access as access
from duckfleet_ui import common
from duckfleet_ui import query_access as queries

HISTORY_SIZE = 20
MIN_ROWS, MAX_ROWS = 1, 100_000
MIN_TIMEOUT_S, MAX_TIMEOUT_S = 1, 3_600
HINTS = {
    "read_only": "duckfleet opens databases read-only.",
    "blocked": "File access, extensions and settings are disabled on this page.",
    "timeout": "Raise the timeout in Options or narrow the query.",
}
EXPORTS = {
    "csv": ("CSV", "text/csv", "csv"),
    "parquet": ("Parquet", "application/vnd.apache.parquet", "parquet"),
}


# ---------------------------------------------------------------- state callbacks


def _clear() -> None:
    st.session_state["sql_text"] = ""
    st.session_state.pop("sql_result", None)
    st.session_state.pop("download", None)


def _load_history() -> None:
    chosen = st.session_state.get("sql_history_choice")
    if chosen:
        st.session_state["sql_text"] = chosen
    st.session_state["sql_history_choice"] = None  # the same entry can be loaded again


def _remember(sql: str) -> None:
    history = [q for q in st.session_state.get("sql_history", []) if q != sql]
    st.session_state["sql_history"] = [sql, *history][:HISTORY_SIZE]


def _file_stem(rel_path: str) -> str:
    return re.sub(r"[^\w.-]+", "_", Path(rel_path).stem) or "result"


# ---------------------------------------------------------------- running


def _options_config():
    """The shared ``QueryConfig`` with the page options (threads and memory untouched)."""
    return queries.query_config().model_copy(
        update={
            "max_rows": int(st.session_state["sql_max_rows"]),
            "timeout_s": float(st.session_state["sql_timeout"]),
        }
    )


def _run(info: dict[str, Any], sql: str) -> None:
    config = _options_config()
    _remember(sql)
    st.session_state.pop("download", None)
    result: dict[str, Any] = {"rel_path": info["rel_path"], "sql": sql}
    try:
        with st.spinner("Running..."):
            outcome = run_query(info["abs_path"], sql, config)
    except QueryError as error:
        result["error"] = (error.kind, error.message)
    else:
        frame, as_text = queries.frame_from_result(outcome)
        result.update(
            frame=frame,
            as_text=as_text,
            truncated=outcome.truncated,
            elapsed_s=outcome.elapsed_s,
            statements=outcome.statements,
            rows=len(outcome.rows),
            limit=config.max_rows,
        )
    st.session_state["sql_result"] = result


def _prepare(info: dict[str, Any], sql: str, fmt: Literal["csv", "parquet"]) -> None:
    label, mime, extension = EXPORTS[fmt]
    config = _options_config()
    try:
        with st.spinner(f"Preparing {label}..."):
            data, rows, truncated = export_query(info["abs_path"], sql, fmt, config)
    except MissingDependencyError as error:
        st.error(str(error))
        return
    except QueryError as error:
        _show_error(error.kind, error.message)
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    st.session_state["download"] = {
        "name": f"{_file_stem(info['rel_path'])}_{stamp}.{extension}",
        "mime": mime,
        "data": data,
        "rows": rows,
        "truncated": truncated,
        "rel_path": info["rel_path"],
        "sql": sql,
        "cap": config.max_download_rows,
    }


# ---------------------------------------------------------------- display


def _show_error(kind: str, message: str) -> None:
    hint = HINTS.get(kind)
    st.error(f"{kind}: {message}" + (f"\n\n{hint}" if hint else ""))


def _show_result(result: dict[str, Any]) -> None:
    if "error" in result:
        _show_error(*result["error"])
        return
    frame = result["frame"]
    parts = [f"{result['elapsed_s']:.2f} s", f"{result['rows']:,} rows shown"]
    if result["statements"] > 1:
        parts.append(f"{result['statements']} statements (the last result is shown)")
    st.caption("  |  ".join(parts))
    if result["truncated"]:
        st.warning(
            f"The result was cut at {result['limit']:,} rows. Raise the row limit in Options "
            "or narrow the query."
        )
    if not len(frame.columns):
        st.info("The statement returned no result set.")
        return
    st.dataframe(frame, hide_index=True, width="stretch")
    if result["as_text"]:
        st.caption("Shown as text: " + ", ".join(result["as_text"]))


def _show_download(info: dict[str, Any], sql: str) -> None:
    download = st.session_state.get("download")
    if not download or download["rel_path"] != info["rel_path"] or download["sql"] != sql:
        return
    if download["truncated"]:
        st.warning(f"The export was cut at {download['cap']:,} rows.")
    st.download_button(
        f"Download {download['name']} ({len(download['data']):,} bytes, {download['rows']:,} rows)",
        data=download["data"],
        file_name=download["name"],
        mime=download["mime"],
        key="download_button",
        on_click="ignore",
    )


def _schema(path: str, mtime: int, rel_path: str) -> None:
    with st.expander("Schema"):
        schema = access.schema_frame(path, mtime, rel_path)
        if schema.empty:
            st.caption("No tables.")
        else:
            st.dataframe(schema, hide_index=True, width="stretch")


# ---------------------------------------------------------------- page


def render() -> None:
    """Render the SQL page."""
    from duckfleet_ui import nav  # here, not at module level: nav imports this module

    cache = common.cache_dir()
    common.init_state(cache)
    config = queries.query_config()
    st.session_state.setdefault("sql_max_rows", config.max_rows)
    st.session_state.setdefault("sql_timeout", int(config.timeout_s))
    st.title("SQL")

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
    previous = st.session_state.get("sql_result")
    if previous and previous["rel_path"] != rel_path:  # another database: the grid is stale
        st.session_state.pop("sql_result")
    if info["status"] != "ok":
        st.error(
            f"This database could not be read in the last scan: {info['error_type']}: "
            f"{info['error_message']}"
        )
        return
    available = Path(info["abs_path"]).is_file()
    if not available:
        st.warning("The file is missing since the last scan. Rescan on the Overview page.")

    _schema(path, mtime, rel_path)
    st.text_area(
        "SQL",
        key="sql_text",
        height=220,
        placeholder="SELECT * FROM ...",
        persist_state="session",
    )
    # The options and Run share a form, so Run submits the typed option values with the click
    # instead of racing their commit (V-10).
    with st.form("sql", border=False):
        with st.expander("Options"):
            left, right = st.columns(2)
            left.number_input(
                "Max rows shown",
                min_value=MIN_ROWS,
                max_value=MAX_ROWS,
                step=100,
                key="sql_max_rows",
                persist_state="session",
            )
            right.number_input(
                "Timeout (seconds)",
                min_value=MIN_TIMEOUT_S,
                max_value=MAX_TIMEOUT_S,
                step=1,
                key="sql_timeout",
                persist_state="session",
            )
        run = st.form_submit_button("Run", type="primary", disabled=not available)
    st.button("Clear", on_click=_clear)

    sql = st.session_state.get("sql_text", "")
    if run:
        if sql.strip():
            _run(info, sql)
        else:
            st.warning("Type a query first.")

    history = st.session_state.get("sql_history", [])
    if history:
        st.selectbox(
            "History",
            history,
            index=None,
            placeholder="Load an earlier query",
            key="sql_history_choice",
            on_change=_load_history,
            format_func=lambda text: " ".join(text.split())[:120],
        )
    result = st.session_state.get("sql_result")
    if result:
        _show_result(result)
        if "frame" in result:
            st.markdown("**Download** (runs the query in the editor again, up to the row limit)")
            differs = bool(sql.strip()) and sql != result["sql"]
            if differs:  # V-12: what would be exported is not what the grid shows
                st.caption(
                    "The editor text differs from the query behind the result above. "
                    "Press Run first; downloads export the query that produced the result."
                )
            can_export = available and bool(sql.strip()) and not differs
            csv_col, parquet_col, _ = st.columns([1, 1, 6])
            if csv_col.button("Prepare CSV", disabled=not can_export):
                _prepare(info, sql, "csv")
            if parquet_col.button("Prepare Parquet", disabled=not can_export):
                _prepare(info, sql, "parquet")
            _show_download(info, sql)
