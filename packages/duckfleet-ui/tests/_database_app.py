"""Tiny script for AppTest: renders the Database page without st.navigation."""

import streamlit as st

from duckfleet_ui.views import database

st.set_page_config(page_title="duckfleet", layout="wide")
database.render()
