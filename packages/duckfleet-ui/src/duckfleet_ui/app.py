"""Streamlit entry point: ``streamlit run app.py`` (see ``launch.py``)."""

import streamlit as st

from duckfleet_ui.views import overview

st.set_page_config(page_title="duckfleet", layout="wide")

# Views live in views/, never in pages/ (Streamlit would auto-discover pages/).
page = st.navigation([st.Page(overview.render, title="Overview", default=True)])
page.run()
