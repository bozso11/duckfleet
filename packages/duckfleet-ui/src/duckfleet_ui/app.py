"""Streamlit entry point: ``streamlit run app.py`` (see ``launch.py``)."""

import streamlit as st

from duckfleet_ui import nav

st.set_page_config(page_title="duckfleet", layout="wide")

# Views live in views/, never in pages/ (Streamlit would auto-discover pages/).
page = st.navigation(list(nav.PAGES.values()))
page.run()
