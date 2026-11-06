"""Tiny script for AppTest: renders the SQL page without st.navigation."""

import streamlit as st

from duckfleet_ui.views import sql

st.set_page_config(page_title="duckfleet", layout="wide")
sql.render()
