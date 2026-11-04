"""Tiny script for AppTest: renders the Overview page without st.navigation."""

import streamlit as st

from duckfleet_ui.views import overview

st.set_page_config(page_title="duckfleet", layout="wide")
overview.render()
