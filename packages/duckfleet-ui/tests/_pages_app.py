"""Tiny script for AppTest: runs one of the three pages, chosen by ``st.session_state["page"]``.

Streamlit's page switching is not available under AppTest; changing ``page`` between runs of
one AppTest keeps one session state, so widgets that are not rendered on a run behave as they
do when the user visits another page.
"""

import streamlit as st

from duckfleet_ui.views import database, overview, sql

st.set_page_config(page_title="duckfleet", layout="wide")
{"overview": overview.render, "database": database.render, "sql": sql.render}[
    st.session_state.get("page", "overview")
]()
