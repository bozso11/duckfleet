"""Tiny script for AppTest: renders the SQL page without st.navigation."""

import streamlit as st

from duckflock_ui.views import sql

st.set_page_config(page_title="duckflock explorer", layout="wide")
sql.render()
