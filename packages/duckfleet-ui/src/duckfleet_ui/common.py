"""Helpers shared by the pages: environment, root and catalog resolution, session state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from duckfleet_core.config import default_cache_dir
from duckfleet_core.store import ProfileStore, recent_roots

ENV_ROOT = "DUCKFLEET_ROOT"
ENV_CACHE_DIR = "DUCKFLEET_CACHE_DIR"

# Widgets whose value must survive a visit to another page carry ``key=`` and
# ``persist_state="session"``: Streamlit otherwise drops the state of a widget in every run that
# does not render it (V-11; this replaces the earlier ``keep()`` helper).


def cache_dir() -> Path:
    env = os.environ.get(ENV_CACHE_DIR)
    return Path(env) if env else default_cache_dir()


def clean_root(text: str) -> str:
    """Pasted Windows paths often come with quotes."""
    return text.strip().strip('"').strip("'").strip()


def catalog_path(cache: Path, root: str) -> Path:
    return ProfileStore(cache, Path(root)).catalog_path


def keep_valid(key: str, options: list[Any]) -> None:
    """Drop a stored selection that is no longer among the options (after a rescan)."""
    if key in st.session_state and st.session_state[key] not in options:
        del st.session_state[key]


def ordered_databases(dbs: pd.DataFrame) -> list[str]:
    """Databases with status ``ok`` first (the ones with tables before empty ones), then errors.

    The first entry is what the page shows by default, so it should be worth looking at.
    """
    is_ok = dbs["status"] == "ok"
    with_tables = is_ok & (dbs["n_tables"].fillna(0) > 0)
    ok_empty = is_ok & ~with_tables
    return [
        *dbs.loc[with_tables, "rel_path"],
        *dbs.loc[ok_empty, "rel_path"],
        *dbs.loc[~is_ok, "rel_path"],
    ]


def pick_database(dbs: pd.DataFrame) -> str | None:
    """The database selectbox of the Database and SQL pages (key shared with the Overview)."""
    options = ordered_databases(dbs)
    if not options:
        return None
    keep_valid("selected_rel_path", options)
    return st.selectbox(
        "Database",
        options,
        key="selected_rel_path",
        persist_state="session",
        help="Shared by all pages.",
    )


def init_state(cache: Path) -> None:
    state = st.session_state
    env_root = os.environ.get(ENV_ROOT, "")
    if "root_input" not in state:
        recents = recent_roots(cache)
        state["root_input"] = env_root or (recents[0]["root"] if recents else "")
    if "active_root" not in state:
        state["active_root"] = None
        # Launched with DUCKFLEET_ROOT and a catalog exists: show it without a click.
        if env_root and catalog_path(cache, clean_root(env_root)).is_file():
            state["active_root"] = clean_root(env_root)
