"""Tiny script for AppTest: renders the Plot page without st.navigation."""

import streamlit as st

from duckflock_ui.views import plot

st.set_page_config(page_title="duckflock explorer", layout="wide")
plot.render()
