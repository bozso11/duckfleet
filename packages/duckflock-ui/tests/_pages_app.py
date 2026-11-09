"""Tiny script for AppTest: runs one of the three pages, chosen by ``st.session_state["page"]``.

Streamlit's page switching is not available under AppTest; changing ``page`` between runs of
one AppTest keeps one session state, so widgets that are not rendered on a run behave as they
do when the user visits another page.
"""

import streamlit as st

from duckflock_ui.views import database, overview, plot, sql

st.set_page_config(page_title="duckflock explorer", layout="wide")
PAGES = {
    "overview": overview.render,
    "database": database.render,
    "plot": plot.render,
    "sql": sql.render,
}
PAGES[st.session_state.get("page", "overview")]()
