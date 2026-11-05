"""Overview page: choose a root folder, scan it, then explore the fleet.

One page, top to bottom: metrics, fleet table, schema families and the
family-by-table matrix, table variants, a diff of two databases or families,
the time-window timeline, health and errors, rejected files.

The scan blocks the page behind a progress bar (v0.1, decision D-004); the
catalog is only read through ``catalog_access`` (cached per catalog mtime).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from duckfleet_core.config import MAX_POOL_WORKERS, ScanConfig, default_cache_dir
from duckfleet_core.fingerprint import SchemaDiff
from duckfleet_core.store import ProfileStore, recent_roots
from duckfleet_ui import catalog_access as access
from duckfleet_ui.scan_runner import ScanOptions, run_scan_subprocess

ENV_ROOT = "DUCKFLEET_ROOT"
ENV_CACHE_DIR = "DUCKFLEET_CACHE_DIR"


# ---------------------------------------------------------------- helpers


def _cache_dir() -> Path:
    env = os.environ.get(ENV_CACHE_DIR)
    return Path(env) if env else default_cache_dir()


def _clean_root(text: str) -> str:
    """Pasted Windows paths often come with quotes."""
    return text.strip().strip('"').strip("'").strip()


def _catalog_path(cache_dir: Path, root: str) -> Path:
    return ProfileStore(cache_dir, Path(root)).catalog_path


def _keep_valid(key: str, options: list[Any]) -> None:
    """Drop a stored selection that is no longer among the options (after a rescan)."""
    if key in st.session_state and st.session_state[key] not in options:
        del st.session_state[key]


def _init_state(cache_dir: Path) -> None:
    state = st.session_state
    env_root = os.environ.get(ENV_ROOT, "")
    if "root_input" not in state:
        recents = recent_roots(cache_dir)
        state["root_input"] = env_root or (recents[0]["root"] if recents else "")
    if "active_root" not in state:
        state["active_root"] = None
        # Launched with DUCKFLEET_ROOT and a catalog exists: show it without a click.
        if env_root and _catalog_path(cache_dir, _clean_root(env_root)).is_file():
            state["active_root"] = _clean_root(env_root)


def _pick_recent() -> None:
    chosen = st.session_state.get("recent_root")
    if chosen:
        st.session_state["root_input"] = chosen


# ---------------------------------------------------------------- sidebar and scan


def _sidebar(cache_dir: Path) -> tuple[str, ScanOptions, bool, bool]:
    with st.sidebar:
        st.header("Fleet")
        recents = [entry["root"] for entry in recent_roots(cache_dir)]
        st.selectbox(
            "Recent roots",
            ["", *recents],
            key="recent_root",
            on_change=_pick_recent,
            format_func=lambda value: value or "(choose)",
        )
        st.text_input("Root folder", key="root_input", help="Folder tree with DuckDB files")
        st.number_input(
            "Workers",
            min_value=1,
            max_value=MAX_POOL_WORKERS,
            value=ScanConfig().resolved_workers(),
            key="workers_input",
            help="Worker processes; default is all CPU cores but two",
        )
        st.number_input(
            "Max files", min_value=1, value=ScanConfig().max_files, step=100, key="max_files_input"
        )
        st.checkbox("Full rescan", key="full_input", help="Ignore cached profiles")
        st.checkbox("Sniff all files", key="sniff_input", help="Header-check every file")
        scan_clicked = st.button("Scan", type="primary", width="stretch")
        load_clicked = st.button("Load existing catalog", width="stretch")
        st.caption(f"Cache: {cache_dir}")

    options = ScanOptions(
        workers=int(st.session_state["workers_input"]),
        max_files=int(st.session_state["max_files_input"]),
        full=bool(st.session_state["full_input"]),
        sniff_all=bool(st.session_state["sniff_input"]),
        cache_dir=str(cache_dir),
    )
    return _clean_root(st.session_state["root_input"]), options, scan_clicked, load_clicked


def _scan(root: str, options: ScanOptions) -> None:
    """Run the scan behind a progress bar. On success the page reruns with the new catalog."""
    if not root or not Path(root).is_dir():
        st.error(f"Root folder not found: {root or '(empty)'}")
        return

    with st.status("Scanning...", expanded=True) as status:
        bar = st.progress(0.0, text="Discovering files...")

        def on_event(event: dict) -> None:
            kind = event["event"]
            if kind == "discovered":
                bar.progress(0.0, text=f"Found {event['candidates']} candidate files")
            elif kind == "planned":
                text = f"Profiling {event['to_profile']} files ({event['reused']} reused)"
                bar.progress(0.0 if event["to_profile"] else 1.0, text=text)
            elif kind == "profiled":
                fraction = event["done"] / max(event["total"], 1)
                bar.progress(
                    fraction, text=f"{event['done']}/{event['total']}: {event['rel_path']}"
                )
            elif kind == "catalog":
                bar.progress(1.0, text="Catalog built")

        result = run_scan_subprocess(root, options, on_event)
        if result.ok:
            status.update(label="Scan finished", state="complete", expanded=False)
        else:
            status.update(label="Scan failed", state="error", expanded=True)

    if not result.ok:
        if result.fatal:
            message = result.fatal
        elif result.returncode == 0:
            message = "The scan ended without a finished event."
        else:
            message = f"The scan ended with exit code {result.returncode}."
        st.error(message)
        with st.expander("Scan log (stderr)", expanded=True):
            st.code(result.stderr or "(empty)", language="text")
            if result.stray_stdout:
                st.markdown("**Unexpected output on stdout**")
                st.code("\n".join(result.stray_stdout), language="text")
        return

    summary = result.summary or {}
    st.session_state["active_root"] = root
    st.session_state["scan_notice"] = (
        f"Scan finished: {summary.get('files_candidates', 0)} candidates, "
        f"{summary.get('files_profiled', 0)} profiled, {summary.get('files_reused', 0)} reused, "
        f"{summary.get('files_failed', 0)} failed, {summary.get('workers', '?')} workers."
    )
    st.rerun()


def _load(root: str, cache_dir: Path) -> None:
    if root and _catalog_path(cache_dir, root).is_file():
        st.session_state["active_root"] = root
    else:
        st.warning("No catalog for this root yet. Press Scan.")


# ---------------------------------------------------------------- sections


def _metrics(dbs: pd.DataFrame, strict: pd.DataFrame, loose: pd.DataFrame, run: dict) -> None:
    ok = int((dbs["status"] == "ok").sum())
    first, last = dbs["time_min"].min(), dbs["time_max"].max()
    span = "n/a" if pd.isna(first) or pd.isna(last) else f"{first:%Y-%m-%d} to {last:%Y-%m-%d}"
    row1 = st.columns(4)
    row1[0].metric("Candidates", len(dbs))
    row1[1].metric("OK", ok)
    row1[2].metric("Failed", len(dbs) - ok)
    row1[3].metric("Rejected", int(run.get("files_rejected", 0)))
    row2 = st.columns(4)
    row2[0].metric("Strict families", len(strict))
    row2[1].metric("Loose families", len(loose))
    row2[2].metric("Total rows", f"{int(dbs['total_rows'].fillna(0).sum()):,}")
    row2[3].metric("Fleet time span", span)


def _fleet_table(dbs: pd.DataFrame) -> None:
    st.subheader("Fleet")
    st.dataframe(
        dbs,
        hide_index=True,
        width="stretch",
        column_config={
            "size_mb": st.column_config.NumberColumn("size (MB)", format="%.2f"),
            "total_rows": st.column_config.NumberColumn("total rows", format="localized"),
            "n_tables": st.column_config.NumberColumn("tables"),
            "time_min": st.column_config.DatetimeColumn("time min", format="YYYY-MM-DD HH:mm"),
            "time_max": st.column_config.DatetimeColumn("time max", format="YYYY-MM-DD HH:mm"),
        },
        column_order=[
            "rel_path", "size_mb", "n_tables", "total_rows", "family_strict", "family_loose",
            "time_min", "time_max", "tags", "status",
        ],
    )  # fmt: skip
    options = dbs["rel_path"].tolist()
    _keep_valid("selected_rel_path", options)
    st.selectbox(
        "Selected database",
        options,
        index=None,
        placeholder="Choose a database",
        key="selected_rel_path",
        help="Remembered for the database page that comes in a later version.",
    )


def _families(path: str, mtime: int) -> None:
    st.subheader("Schema families")
    st.caption(
        "Strict: same tables, column names and types. Loose: same tables and column names. "
        "Column order is ignored."
    )
    level = st.radio("Level", ["strict", "loose"], horizontal=True, key="family_level")
    families = access.families(path, mtime, level)
    if families.empty:
        st.info("No schema families (no database with tables).")
        return
    st.dataframe(
        families,
        hide_index=True,
        width="stretch",
        column_config={
            "family_id": st.column_config.TextColumn("family"),
            "n_members": st.column_config.NumberColumn("databases"),
            "n_tables": st.column_config.NumberColumn("tables #"),
            "members": st.column_config.ListColumn("members"),
        },
    )
    st.markdown("**Family-by-table matrix** (cell = member databases having the table)")
    st.dataframe(access.family_matrix(path, mtime, level), width="stretch")


def _variants(path: str, mtime: int) -> None:
    st.subheader("Table variants")
    st.caption(
        "Distinct strict structures of each table across the fleet. "
        "More than one variant means the table differs between files."
    )
    variants = access.table_variants(path, mtime)
    if variants.empty:
        st.info("No tables.")
        return
    st.dataframe(
        variants,
        hide_index=True,
        width="stretch",
        column_config={
            "fp_strict": st.column_config.TextColumn("fingerprint"),
            "n_variants": st.column_config.NumberColumn("variants of table"),
            "n_members": st.column_config.NumberColumn("databases"),
            "n_columns": st.column_config.NumberColumn("columns #"),
        },
    )


def _diff_choices(path: str, mtime: int, dbs: pd.DataFrame) -> dict[str, str]:
    """Label -> rel_path; a family stands for its first member."""
    choices: dict[str, str] = {}
    for level in ("strict", "loose"):
        families = access.families(path, mtime, level)
        for family in families.itertuples():
            if family.members:
                label = f"{level} family {family.family_id} ({family.n_members} databases)"
                choices[label] = family.members[0]
    for rel_path in dbs.loc[dbs["status"] == "ok", "rel_path"]:
        choices[f"database {rel_path}"] = rel_path
    return choices


def _diff_frames(diff: SchemaDiff) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    only_a = pd.DataFrame({"table": diff.only_in_a}, columns=["table"])
    only_b = pd.DataFrame({"table": diff.only_in_b}, columns=["table"])
    rows: list[dict[str, str]] = []
    for table in diff.changed:
        rows += [
            {"table": table.table, "column": c, "change": "only in A"}
            for c in table.columns_only_in_a
        ]
        rows += [
            {"table": table.table, "column": c, "change": "only in B"}
            for c in table.columns_only_in_b
        ]
        rows += [
            {
                "table": table.table,
                "column": change.name,
                "change": f"type {change.type_a} -> {change.type_b}",
            }
            for change in table.type_changes
        ]
    return only_a, only_b, pd.DataFrame(rows, columns=["table", "column", "change"])


def _diff(path: str, mtime: int, dbs: pd.DataFrame) -> None:
    st.subheader("Compare")
    choices = _diff_choices(path, mtime, dbs)
    labels = list(choices)
    if not labels:
        st.info("Nothing to compare.")
        return
    _keep_valid("diff_a", labels)
    _keep_valid("diff_b", labels)
    left, right = st.columns(2)
    label_a = left.selectbox("A", labels, key="diff_a")
    label_b = right.selectbox("B", labels, index=min(1, len(labels) - 1), key="diff_b")
    rel_a, rel_b = choices[label_a], choices[label_b]
    st.caption(f"A = {rel_a}   |   B = {rel_b}   (a family is compared through its first member)")
    diff = access.diff(path, mtime, rel_a, rel_b)
    if diff.identical:
        st.success("The table structures are identical.")
    only_a, only_b, columns = _diff_frames(diff)
    col1, col2, col3 = st.columns(3)
    col1.markdown("**Tables only in A**")
    col1.dataframe(only_a, hide_index=True, width="stretch")
    col2.markdown("**Tables only in B**")
    col2.dataframe(only_b, hide_index=True, width="stretch")
    col3.markdown("**Column differences**")
    col3.dataframe(columns, hide_index=True, width="stretch")


def _plot_windows(timeline: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Frame for ``px.timeline`` plus the databases whose window is a single instant.

    A bar with ``time_max == time_min`` has no width and would not show (R-08), so it is
    drawn with the larger of 1 hour and 0.5 % of the fleet span; the hover text says so.
    """
    frame = timeline.copy()
    fmt = "%Y-%m-%d %H:%M:%S"
    frame["window"] = (
        frame["time_min"].dt.strftime(fmt) + " to " + frame["time_max"].dt.strftime(fmt)
    )
    single = frame["time_max"] == frame["time_min"]
    if single.any():
        span = frame["time_max"].max() - frame["time_min"].min()
        minimum = max(pd.Timedelta(hours=1), span * 0.005)
        frame.loc[single, "window"] = "single instant " + frame.loc[single, "time_min"].dt.strftime(
            fmt
        )
        frame.loc[single, "time_max"] = frame.loc[single, "time_min"] + minimum
    return frame, frame.loc[single, "rel_path"].tolist()


def _timeline(path: str, mtime: int, dbs: pd.DataFrame) -> None:
    st.subheader("Time windows")
    timeline = access.timeline(path, mtime)
    if timeline.empty:
        st.info("No database has temporal data.")
    else:
        plot, instants = _plot_windows(timeline)
        order = sorted(plot["family_strict"].unique(), key=lambda f: (f == "(none)", len(f), f))
        figure = px.timeline(
            plot,
            x_start="time_min",
            x_end="time_max",
            y="rel_path",
            color="family_strict",
            category_orders={"family_strict": order},
            labels={"family_strict": "strict family", "rel_path": "database"},
            hover_data={"window": True, "time_min": False, "time_max": False},
        )
        figure.update_yaxes(autorange="reversed", title=None)
        figure.update_layout(height=max(320, 26 * len(plot) + 140), margin={"l": 0, "r": 0})
        st.plotly_chart(figure, width="stretch")
        if instants:
            st.caption(
                "Single-instant windows are drawn with a minimum width: " + ", ".join(instants)
            )
    without = dbs.loc[dbs["time_min"].isna(), ["rel_path", "status", "error_type", "health"]]
    if not without.empty:
        st.markdown(f"**Databases without a time window ({len(without)})**")
        st.dataframe(without, hide_index=True, width="stretch")


def _problems(path: str, mtime: int) -> None:
    st.subheader("Health and errors")
    problems = access.problems(path, mtime)
    if problems.empty:
        st.success("No health flags and no errors.")
    else:
        st.dataframe(problems, hide_index=True, width="stretch")
    st.subheader("Rejected files")
    rejected = access.rejected(path, mtime)
    if rejected.empty:
        st.info("No rejected files.")
    else:
        st.dataframe(rejected, hide_index=True, width="stretch")


# ---------------------------------------------------------------- page


def render() -> None:
    """Render the Overview page."""
    cache_dir = _cache_dir()
    _init_state(cache_dir)
    root, options, scan_clicked, load_clicked = _sidebar(cache_dir)

    st.title("duckfleet")
    if scan_clicked:
        _scan(root, options)  # reruns the page when it succeeds
    if load_clicked:
        _load(root, cache_dir)

    notice = st.session_state.pop("scan_notice", None)
    if notice:
        st.success(notice)

    active = st.session_state.get("active_root")
    if not active:
        st.info("Choose a root folder in the sidebar and press Scan, or load an existing catalog.")
        return
    key = access.catalog_key(_catalog_path(cache_dir, active))
    if key is None:
        st.warning(f"No catalog found for {active}. Press Scan.")
        return
    path, mtime = key

    run = access.scan_run(path, mtime)
    finished = run.get("finished_at")
    finished_text = f"{finished:%Y-%m-%d %H:%M:%S}" if finished is not None else "?"
    st.caption(
        f"Root: {active}  |  last scan finished {finished_text} UTC  |  "
        f"{run.get('workers')} workers  |  duckfleet {run.get('duckfleet_version')}"
    )
    if run.get("truncated"):
        st.warning(
            f"Discovery stopped at the limit of {run.get('files_candidates')} files; "
            "more files exist. Raise 'Max files' and scan again."
        )

    dbs = access.databases(path, mtime)
    _metrics(
        dbs,
        access.families(path, mtime, "strict"),
        access.families(path, mtime, "loose"),
        run,
    )
    _fleet_table(dbs)
    _families(path, mtime)
    _variants(path, mtime)
    _diff(path, mtime, dbs)
    _timeline(path, mtime, dbs)
    _problems(path, mtime)
