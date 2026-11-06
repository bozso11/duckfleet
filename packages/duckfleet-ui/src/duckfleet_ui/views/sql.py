"""SQL page: a read-only SQL editor on one database of the fleet.

Phase 4 only provides the page the Database page links to; the editor, the
result grid, the downloads and the history follow in Phase 5.
"""

from __future__ import annotations

import streamlit as st

from duckfleet_ui import common


def render() -> None:
    """Render the SQL page."""
    common.keep(*common.KEPT_KEYS)
    st.title("SQL")
    st.text_area("SQL", key="sql_text", height=220)
    st.info("Running queries is added in the next phase.")
