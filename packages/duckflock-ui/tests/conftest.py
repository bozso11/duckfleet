"""Shared fixtures of the UI tests: nothing here touches the real user cache (review W-09)."""

from __future__ import annotations

from pathlib import Path

import pytest

from duckflock_core import config as core_config
from duckflock_core import query as core_query
from duckflock_ui import common as ui_common


@pytest.fixture(autouse=True)
def _isolated_default_cache(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Every place that looks up ``default_cache_dir`` resolves to a folder of the run."""
    default = tmp_path_factory.mktemp("default-cache")
    # the name is bound in each module that looks it up, so each one is patched
    for module in (core_config, core_query, ui_common):
        monkeypatch.setattr(module, "default_cache_dir", lambda default=default: default)
    return default
