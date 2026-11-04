"""Phase 0 placeholder: proves the UI package installs with its dependencies.

Replaced by a Streamlit AppTest smoke test in Phase 6 (see docs/PLAN_v0.1.md).
"""

import duckfleet_ui


def test_package_imports() -> None:
    assert duckfleet_ui.__version__


def test_ui_dependencies_import() -> None:
    import pandas  # noqa: F401
    import plotly  # noqa: F401
    import streamlit  # noqa: F401

    import duckfleet_core  # noqa: F401
