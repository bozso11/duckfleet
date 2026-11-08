"""The ``st.Page`` objects, shared by ``app.py`` and the views.

Views import this module inside functions, never at module level: it imports
the views, so a module-level import back would be a cycle.
"""

from __future__ import annotations

import streamlit as st

from duckfleet_ui.views import database, overview, plot, sql

PAGES = {
    "overview": st.Page(overview.render, title="Overview", default=True),
    "database": st.Page(database.render, title="Database", url_path="database"),
    "plot": st.Page(plot.render, title="Plot", url_path="plot"),
    "sql": st.Page(sql.render, title="SQL", url_path="sql"),
}
