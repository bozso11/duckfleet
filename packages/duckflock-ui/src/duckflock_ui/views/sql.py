"""SQL page: a read-only SQL editor on one database of the flock, or on several at once.

The text runs as given on a sandboxed read-only connection (``query_access``,
decision D-021): no writes, no file access, no extensions, no ``SET``; a row cap
and a timeout apply. In scope "several databases" (``scope.py``) it runs on a flock connection
(``duckflock_core.flock``): one union view per table with a ``database`` column and the per-file
aliases. The last result and the prepared download live in the
session state so they survive reruns caused by other widgets.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import streamlit as st

from duckflock_core.flock import (
    FlockSelection,
    FlockSummary,
    describe_flock,
    export_flock_query,
    run_flock_query,
)
from duckflock_core.query import MissingDependencyError, QueryError, export_query, run_query
from duckflock_core.sqlutil import quote_ident
from duckflock_ui import catalog_access as access
from duckflock_ui import common
from duckflock_ui import query_access as queries
from duckflock_ui import scope as scopes

PLACEHOLDER = "SELECT * FROM ..."
PLACEHOLDER_MANY = "SELECT database, count(*) FROM events GROUP BY 1"
_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
HISTORY_SIZE = 20
MIN_ROWS, MAX_ROWS = 1, 100_000
MIN_TIMEOUT_S, MAX_TIMEOUT_S = 1, 3_600
HINTS = {
    "read_only": "duckflock opens databases read-only.",
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


def _run(scope: scopes.Scope, info: dict[str, Any], paths: dict[str, str], sql: str) -> None:
    """Run ``sql`` on the database (``info``) or on the flock of ``scope`` (``paths``)."""
    config = _options_config()
    _remember(sql)
    st.session_state.pop("download", None)
    # "rel_path" is the key of what the result belongs to: the rel_path in scope "one"
    result: dict[str, Any] = {"rel_path": scope.key, "sql": sql}
    try:
        with st.spinner("Running..."):
            if scope.mode == "many":
                outcome, summary = run_flock_query(
                    FlockSelection(rel_paths=scope.rel_paths), paths, sql, config
                )
                result["failures"] = [asdict(f) for f in summary.failures]
                result["view_failures"] = list(summary.view_failures)
            else:
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


def _prepare(
    scope: scopes.Scope,
    info: dict[str, Any],
    paths: dict[str, str],
    sql: str,
    fmt: Literal["csv", "parquet"],
) -> None:
    label, mime, extension = EXPORTS[fmt]
    config = _options_config()
    try:
        with st.spinner(f"Preparing {label}..."):
            if scope.mode == "many":
                data, rows, truncated, _summary = export_flock_query(
                    FlockSelection(rel_paths=scope.rel_paths), paths, sql, fmt, config
                )
            else:
                data, rows, truncated = export_query(info["abs_path"], sql, fmt, config)
    except MissingDependencyError as error:
        st.error(str(error))
        return
    except QueryError as error:
        _show_error(error.kind, error.message)
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    st.session_state["download"] = {
        "name": f"{_file_stem(info['rel_path']) if scope.mode == 'one' else 'flock'}"
        f"_{stamp}.{extension}",
        "mime": mime,
        "data": data,
        "rows": rows,
        "truncated": truncated,
        "rel_path": scope.key,
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
    for warning in (
        scopes.attach_warning(result.get("failures", [])),
        scopes.view_warning(result.get("view_failures", [])),
    ):
        if warning:
            st.warning(warning)
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


def _show_download(scope: scopes.Scope, sql: str) -> None:
    download = st.session_state.get("download")
    if not download or download["rel_path"] != scope.key or download["sql"] != sql:
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


def views_frame(summary: FlockSummary) -> pd.DataFrame:
    """One row per union view: its members, columns and the columns shown as text."""
    rows = [
        {
            "view": view.qualified,
            "databases": len(view.members),
            "columns": ", ".join(f"{name} {kind}" for name, kind in view.columns),
            "as text": ", ".join(view.casts),
        }
        for view in summary.views
    ]
    return pd.DataFrame(rows, columns=["view", "databases", "columns", "as text"])


def aliases_frame(summary: FlockSummary) -> pd.DataFrame:
    return pd.DataFrame(
        {"alias": list(summary.aliases), "database": list(summary.aliases.values())},
        columns=["alias", "database"],
    )


def _name(identifier: str) -> str:
    """The identifier as written in a query: bare when it is plain, else quoted."""
    return identifier if _PLAIN_NAME.match(identifier) else quote_ident(identifier)


def placeholder_many(summary: FlockSummary | None) -> str:
    """The placeholder of the editor in scope several: a count per database of ``events``.

    The view is ``events`` when there is one, else the first union view, and the column is the
    label column of that view, which is ``duckflock_database`` when a member table has a column
    of its own named ``database`` (X-10).
    """
    if summary is None or not summary.views:
        return PLACEHOLDER_MANY
    view = next((v for v in summary.views if v.table_name == "events"), summary.views[0])
    table = _name(view.table_name)
    if view.schema_name != "main":
        table = f"{_name(view.schema_name)}.{table}"
    return f"SELECT {_name(view.label_column)}, count(*) FROM {table} GROUP BY 1"


def label_text(summary: FlockSummary) -> str:
    """How the Schema expander names the label column of the union views (X-10)."""
    labels = sorted({view.label_column for view in summary.views}) or ["database"]
    if len(labels) == 1:
        return f"a `{labels[0]}` column"
    names = ", ".join(f"`{name}`" for name in labels)
    return (
        f"a label column ({names}; `duckflock_database` is used where a member table has a "
        "`database` column of its own, the Columns below say which)"
    )


def _schema_many(selection: FlockSelection, paths: dict[str, str]) -> FlockSummary | None:
    """The union views and aliases of the chosen databases (a flock connection, opened here)."""
    with st.expander("Schema"):
        try:
            summary = describe_flock(selection, paths, _options_config())
        except QueryError as error:
            _show_error(error.kind, error.message)
            return None
        st.markdown(
            "**Union views**: each table name holds the rows of every chosen database that has it, "
            f"with {label_text(summary)} (the file's rel_path); a column a database lacks is NULL "
            "there."
        )
        views = views_frame(summary)
        if views.empty:
            st.caption("No tables.")
        else:
            st.dataframe(views, hide_index=True, width="stretch")
        st.markdown(
            "**Aliases**: query one file as `alias.schema.table`, for example "
            "`SELECT * FROM <alias>.main.events`."
        )
        st.dataframe(aliases_frame(summary), hide_index=True, width="stretch")
        for warning in (
            scopes.attach_warning([asdict(f) for f in summary.failures]),
            scopes.view_warning(summary.view_failures),
        ):
            if warning:
                st.warning(warning)
        return summary


# ---------------------------------------------------------------- page


def render() -> None:
    """Render the SQL page."""
    from duckflock_ui import nav  # here, not at module level: nav imports this module

    cache = common.cache_dir()
    common.init_state(cache)
    config = queries.query_config()
    st.session_state.setdefault("sql_max_rows", config.max_rows)
    st.session_state.setdefault("sql_timeout", int(config.timeout_s))
    st.title("SQL")

    root = st.session_state.get("active_root")
    key = access.catalog_key(common.catalog_path(cache, root)) if root else None
    if key is None:
        st.info("No flock is loaded. Scan a root folder or load a catalog on the Overview page.")
        st.page_link(nav.PAGES["overview"], label="Open the Overview")
        return
    path, mtime = key

    dbs = access.databases(path, mtime)
    rel_path = common.pick_database(dbs)
    if rel_path is None:
        st.info("The catalog holds no database.")
        return
    scope = scopes.scope_control(dbs, access.families(path, mtime, "strict"))
    previous = st.session_state.get("sql_result")
    if previous and previous["rel_path"] != scope.key:  # another scope: the grid is stale
        st.session_state.pop("sql_result")
    info: dict[str, Any] = {}
    paths: dict[str, str] = {}
    flock_summary: FlockSummary | None = None
    if scope.mode == "many":
        if not scope.rel_paths:
            st.info("Choose at least one database.")
            return
        everything = access.abs_paths(path, mtime)
        paths = {rel: everything[rel] for rel in scope.rel_paths if rel in everything}
        selection = FlockSelection(rel_paths=list(paths))
        available = True  # a file that vanished is reported as an attach failure
        flock_summary = _schema_many(selection, paths)
    else:
        info = access.database_info(path, mtime, rel_path)
        if not info:
            st.warning(f"{rel_path} is not in the catalog any more. Scan again.")
            return
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
        placeholder=placeholder_many(flock_summary) if scope.mode == "many" else PLACEHOLDER,
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
            _run(scope, info, paths, sql)
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
                _prepare(scope, info, paths, sql, "csv")
            if parquet_col.button("Prepare Parquet", disabled=not can_export):
                _prepare(scope, info, paths, sql, "parquet")
            _show_download(scope, sql)
