"""duckflock-ui: Streamlit front end for duckflock.

Depends on duckflock-core for every non-UI concern. Views live in ``views/``
(not ``pages/``, which Streamlit would auto-discover and which conflicts with
``st.navigation``).
"""

__version__ = "0.5.0"
