"""Tiny script for AppTest: renders the Overview page without st.navigation."""

import streamlit as st

from duckflock_ui.views import overview

st.set_page_config(page_title="duckflock explorer", layout="wide")
overview.render()
