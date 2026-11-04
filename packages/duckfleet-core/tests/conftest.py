"""Shared fixtures: the demo fleet and a throw-away cache directory.

Nothing here touches the real user cache or real user data.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
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


_HOLDER = """
import sys, time, duckdb
con = duckdb.connect(sys.argv[1])
con.execute("CREATE TABLE IF NOT EXISTS held (i INTEGER)")
con.execute("INSERT INTO held VALUES (1)")
open(sys.argv[2], "w").write("ready")
time.sleep(120)
"""


@contextlib.contextmanager
def _hold_open(db: Path) -> Iterator[None]:
    """Keep ``db`` open read-write in a second process (a writer that excludes everyone)."""
    marker = db.with_name(db.name + ".ready")
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(db), str(marker)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 40
        while not marker.exists():
            if child.poll() is not None:
                raise RuntimeError(child.stderr.read().decode() if child.stderr else "holder died")
            if time.monotonic() > deadline:
                raise RuntimeError("writer process did not become ready")
            time.sleep(0.1)
        yield
    finally:
        child.kill()
        child.wait(timeout=30)
        if child.stderr:
            child.stderr.close()


@pytest.fixture
def hold_open() -> Callable[[Path], contextlib.AbstractContextManager[None]]:
    """``with hold_open(path): ...`` keeps a read-write connection to ``path`` in a child."""
    return _hold_open
