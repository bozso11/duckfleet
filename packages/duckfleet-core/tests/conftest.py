"""Shared fixtures: the demo fleet and a throw-away cache directory.

Nothing here touches the real user cache or real user data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from duckfleet_core.demo import make_demo_fleet


@pytest.fixture(scope="session")
def demo_fleet(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Demo fleet shared by read-only tests. Never modify it; use ``fresh_demo_fleet``."""
    return make_demo_fleet(tmp_path_factory.mktemp("shared") / "demo-fleet")


@pytest.fixture
def fresh_demo_fleet(tmp_path: Path) -> Path:
    """A private demo fleet a test may modify."""
    return make_demo_fleet(tmp_path / "fleet" / "demo-fleet")


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Cache directory for one test (never the real user cache)."""
    path = tmp_path / "cache"
    path.mkdir()
    return path
