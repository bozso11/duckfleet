"""Helpers shared by the pages: environment, root and catalog resolution, session state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st

from duckfleet_core.config import default_cache_dir
from duckfleet_core.store import ProfileStore, recent_roots

ENV_ROOT = "DUCKFLEET_ROOT"
ENV_CACHE_DIR = "DUCKFLEET_CACHE_DIR"

# Widget keys that must survive a visit to another page: Streamlit drops the state of a widget on
# every run in which the widget is not rendered, so each page turns them into plain state.
# Widgets with a ``value=`` or ``index=`` default must not be listed (Streamlit warns when the
# state of such a widget was set through the API); their defaults live in the session state.
KEPT_KEYS = (
    "root_input", "recent_root", "workers_input", "max_files_input", "full_input", "sniff_input",
    "selected_rel_path", "selected_table", "sql_text", "sql_max_rows", "sql_timeout",
)  # fmt: skip


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


def keep(*keys: str) -> None:
    """Turn the widget state under ``keys`` into plain session state so it survives this run."""
    for key in keys:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


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
