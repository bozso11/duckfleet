"""Tiny script for AppTest: renders the Plot page without st.navigation."""

import streamlit as st

from duckfleet_ui.views import plot

st.set_page_config(page_title="duckfleet", layout="wide")
plot.render()
