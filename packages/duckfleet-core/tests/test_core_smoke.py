"""Workspace smoke tests for the core package.

The streamlit guard stays for the life of the project: it imports every
``duckfleet_core`` module in a fresh interpreter and asserts no UI library
came along (CLAUDE.md rule 2).
"""

import subprocess
import sys

import duckdb

import duckfleet_core

_GUARD = """
import importlib, pkgutil, sys
import duckfleet_core
for mod in pkgutil.walk_packages(duckfleet_core.__path__, "duckfleet_core."):
    importlib.import_module(mod.name)
leaked = [m for m in ("streamlit", "plotly", "pandas") if m in sys.modules]
assert not leaked, f"duckfleet_core imported UI libraries at module level: {leaked}"
print("ok")
"""


def test_package_imports() -> None:
    assert duckfleet_core.__version__


def test_core_does_not_import_ui_libraries() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _GUARD], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_duckdb_available() -> None:
    con = duckdb.connect()
    assert con.execute("select 1").fetchone() == (1,)
    con.close()
