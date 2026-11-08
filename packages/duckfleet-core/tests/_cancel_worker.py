"""A stand-in for ``profile_database`` that leaves a marker file per call (for spawn workers)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from duckfleet_core.profiler import profile_database

MARKER_DIR_ENV = "DUCKFLEET_TEST_MARKER_DIR"


def slow_profile(abs_path: str, rel_path: str, config_dict: dict) -> dict:
    marker_dir = Path(os.environ[MARKER_DIR_ENV])
    (marker_dir / (Path(rel_path).stem + ".started")).write_text("started", encoding="utf-8")
    time.sleep(0.3)
    return profile_database(abs_path, rel_path, config_dict)
