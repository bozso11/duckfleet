"""Run a scan as a subprocess and relay its JSON-lines progress.

The scan is a separate process so the process pool never lives inside the
Streamlit server (decision D-004): ``python -m duckflock_core scan ROOT
--progress json``. Every stdout line is one JSON event (plan Section 3.5);
stderr (logs, errors) is collected and returned.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScanOptions:
    """What the sidebar lets the user choose."""

    workers: int | None = None
    max_files: int | None = None
    full: bool = False
    sniff_all: bool = False
    cache_dir: str | None = None


@dataclass
class ScanRunResult:
    returncode: int
    summary: dict[str, Any] | None = None  # the ScanSummary of the "finished" event
    fatal: str | None = None  # message of a "fatal" event
    stderr: str = ""
    stray_stdout: list[str] = field(default_factory=list)  # stdout lines that were not JSON

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.summary is not None


def build_command(root: str, options: ScanOptions) -> list[str]:
    command = [sys.executable, "-m", "duckflock_core", "scan", root, "--progress", "json"]
    if options.workers:
        command += ["--workers", str(options.workers)]
    if options.max_files:
        command += ["--max-files", str(options.max_files)]
    if options.full:
        command.append("--full")
    if options.sniff_all:
        command.append("--sniff-all")
    if options.cache_dir:
        command += ["--cache-dir", options.cache_dir]
    return command


def run_scan_subprocess(
    root: str, options: ScanOptions, on_event: Callable[[dict], None]
) -> ScanRunResult:
    """Run the scan, call ``on_event`` for every progress event, and wait for the exit."""
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console window on Windows
    process = subprocess.Popen(
        build_command(root, options),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
    )
    stderr_lines: list[str] = []

    def drain_stderr() -> None:  # a full stderr pipe would block the scan
        assert process.stderr is not None
        for line in process.stderr:
            stderr_lines.append(line)

    reader = threading.Thread(target=drain_stderr, daemon=True)
    reader.start()

    result = ScanRunResult(returncode=-1)
    assert process.stdout is not None
    try:
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                result.stray_stdout.append(line)
                continue
            if not isinstance(event, dict):
                result.stray_stdout.append(line)
                continue
            if event.get("event") == "finished":
                result.summary = event.get("summary")
            elif event.get("event") == "fatal":
                result.fatal = str(event.get("message"))
            on_event(event)
    finally:
        # If on_event raised (Streamlit interrupts a script that gets a rerun), keep draining
        # stdout so the scan can finish and write its catalog instead of blocking on a full pipe.
        for _ in process.stdout:
            pass
        result.returncode = process.wait()
        reader.join(timeout=10)
        result.stderr = "".join(stderr_lines)
    return result
