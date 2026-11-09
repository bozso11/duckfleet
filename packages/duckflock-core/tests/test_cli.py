from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from duckflock_core.cli import main
from duckflock_core.demo import make_demo_flock
from duckflock_core.store import ProfileStore


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def scan_args(flock: Path, cache: Path, *extra: str) -> list[str]:
    return ["scan", str(flock), "--cache-dir", str(cache), "--workers", "1", *extra]


def test_scan_json_prints_only_json_lines_on_stdout(
    demo_flock: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, *scan_args(demo_flock, cache_dir, "--progress", "json"))
    assert code == 0
    events = [json.loads(line) for line in out.splitlines()]  # every line is JSON, nothing else
    kinds = [e["event"] for e in events]
    assert kinds == ["discovered", "planned", *["profiled"] * 13, "catalog", "finished"]
    assert events[0]["candidates"] == 13 and events[0]["rejected"] == 2
    finished = events[-1]["summary"]
    assert finished["files_failed"] == 3 and finished["workers"] == 1
    assert finished["config"]["cache_dir"] == str(cache_dir)
    assert "{" not in err  # no events on stderr


def test_scan_text_progress_goes_to_stderr(
    demo_flock: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, *scan_args(demo_flock, cache_dir))
    assert code == 0
    assert "found 13 candidate files (16 files walked, 2 rejected)" in err
    assert "profiling 13 files, reusing 0 cached profiles" in err
    assert "[13/13]" in err and "error:corrupt broken/corrupt.duckdb" in err
    assert "error:corrupt broken/highbit.duckdb" in err
    assert "error:encrypted secure/encrypted.duckdb" in err
    assert "catalog written:" in err
    assert "13 candidates, 2 rejected, 10 ok, 3 failed, 0 reused, 1 workers" in out
    assert "Catalog:" in out


def test_scan_progress_none_is_silent(
    demo_flock: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, *scan_args(demo_flock, cache_dir, "--progress", "none"))
    assert (code, out, err) == (0, "", "")


def test_second_scan_reuses_profiles(
    demo_flock: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, *scan_args(demo_flock, cache_dir, "--progress", "none"))
    code, out, err = run(capsys, *scan_args(demo_flock, cache_dir))
    assert code == 0
    assert "profiling 3 files, reusing 10 cached profiles" in err
    assert "13 candidates, 2 rejected, 10 ok, 3 failed, 10 reused" in out
    code, out, _ = run(capsys, *scan_args(demo_flock, cache_dir, "--full", "--progress", "none"))
    assert code == 0


def test_scan_options(
    demo_flock: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, *scan_args(demo_flock, cache_dir, "--max-files", "3"))
    assert code == 0 and "WARNING: stopped at the file limit" in err
    assert "3 candidates" in out
    code, out, _ = run(
        capsys, *scan_args(demo_flock, cache_dir, "--extensions", "duckdb", "--progress", "none")
    )
    assert code == 0
    code, _, err = run(capsys, *scan_args(demo_flock, cache_dir, "--extensions", "db,ddb"))
    assert "found 1 candidate files" in err  # only legacy/old.db (the look-alike is rejected)
    code, _, err = run(capsys, *scan_args(demo_flock, cache_dir, "--sniff-all"))
    assert "found 13 candidate files" in err and "(16 files walked, 2 rejected)" in err


def test_missing_root_is_fatal(
    tmp_path: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, *scan_args(tmp_path / "nope", cache_dir))
    assert code == 2 and out == "" and "error:" in err
    code, out, err = run(capsys, *scan_args(tmp_path / "nope", cache_dir, "--progress", "json"))
    assert code == 2
    (fatal,) = [json.loads(line) for line in out.splitlines()]
    assert fatal["event"] == "fatal" and "nope" in fatal["message"]
    assert "error:" in err


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["frobnicate"],
        ["scan"],
        ["scan", "x", "--workers", "0"],
        ["scan", "x", "--workers", "many"],
        ["scan", "x", "--progress", "loud"],
        ["scan", "x", "--no-such-option"],
        ["report", "x", "--format", "html"],
    ],
)
def test_usage_errors_exit_with_1(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, *argv)
    assert code == 1 and out == ""
    assert "usage:" in err


def test_help_and_version_exit_with_0(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "--help")
    assert code == 0 and "scan" in out and "report" in out
    code, out, _ = run(capsys, "--version")
    assert code == 0 and out.startswith("duckflock ")
    code, out, _ = run(capsys, "scan", "--help")
    assert code == 0 and "--progress" in out


def test_report_text(demo_flock: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, *scan_args(demo_flock, cache_dir, "--progress", "none"))
    code, out, err = run(capsys, "report", str(demo_flock), "--cache-dir", str(cache_dir))
    assert code == 0 and err == ""
    assert "Strict schema families (4)" in out and "Loose schema families (3)" in out
    for needle in (
        "F1: 3 databases, 2 tables",
        "F3: 2 databases, 3 tables",
        "F4: 1 database, 1 table",
        "L1: 6 databases, 2 tables",
        "L2: 2 databases, 3 tables",
        "main.events: 3 variants",
        "wal_present",
        "empty_database",
        "broken/corrupt.duckdb: corrupt:",
        "broken/highbit.duckdb: corrupt:",
        "secure/encrypted.duckdb: encrypted:",
        "legacy/sqlite_lookalike.db: not_duckdb_header",
        "broken/truncated.duckdb: not_duckdb_header",
        "files walked: 16",
        "candidates: 13",
        "ok: 10",
        "failed: 3",
    ):
        assert needle in out, needle
    windows = [line.strip() for line in out.splitlines() if " .. " in line]
    assert len(windows) == 9
    starts = [line[:19] for line in windows]
    assert starts == sorted(starts)  # time windows in order
    assert windows[0].endswith("legacy/old.db  [F4]")
    assert "no time window (4):" in out


def test_report_markdown(
    demo_flock: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, *scan_args(demo_flock, cache_dir, "--progress", "none"))
    code, out, _ = run(
        capsys, "report", str(demo_flock), "--cache-dir", str(cache_dir), "--format", "md"
    )
    assert code == 0
    assert out.startswith("# duckflock report")
    assert "## Strict schema families (4)" in out and "## Errors" in out
    assert "- F1: 3 databases, 2 tables" in out
    assert "\n    - exp_01/run_01/metrics.duckdb" in out  # members nest two levels deep


def test_report_without_a_scan_is_fatal(
    demo_flock: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, "report", str(demo_flock), "--cache-dir", str(cache_dir))
    assert code == 2 and out == ""
    assert "duckflock scan" in err


def test_make_demo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "demo out"
    code, out, _ = run(capsys, "make-demo", str(target), "--seed", "3")
    assert code == 0 and "Demo flock written" in out
    assert (target / "exp_01" / "run_01" / "metrics.duckdb").is_file()
    assert (target / "broken" / "corrupt.duckdb").is_file()
    assert (target / "broken" / "highbit.duckdb").is_file()
    assert (target / "secure" / "encrypted.duckdb").is_file()
    assert len([p for p in target.rglob("*") if p.is_file()]) == 16


def test_paths(demo_flock: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "paths", str(demo_flock), "--cache-dir", str(cache_dir))
    store = ProfileStore(cache_dir, demo_flock)
    assert code == 0
    assert out.splitlines() == [
        f"cache_dir: {cache_dir}",
        f"root_dir: {store.root_dir}",
        f"catalog: {store.catalog_path}",
    ]


def test_non_ascii_names_never_crash_the_output(
    tmp_path: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flock = make_demo_flock(tmp_path / "données été")
    code, out, err = run(capsys, *scan_args(flock, cache_dir, "--progress", "json"))
    assert code == 0 and out.isascii()  # JSON escapes non-ASCII characters
    code, out, _ = run(capsys, "report", str(flock), "--cache-dir", str(cache_dir))
    assert code == 0 and "données" in out or "donn" in out


@pytest.mark.timeout(180)
def test_module_entry_point_streams_json_lines(tmp_path: Path) -> None:
    """The way the UI runs a scan: ``python -m duckflock_core scan ... --progress json``."""
    flock = make_demo_flock(tmp_path / "flotte été" / "flock 1")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [
            sys.executable, "-m", "duckflock_core", "scan", str(flock),
            "--progress", "json", "--workers", "2", "--cache-dir", str(tmp_path / "cache"),
        ],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=150,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[0]["event"] == "discovered" and events[-1]["event"] == "finished"
    assert sum(e["event"] == "profiled" for e in events) == 13
    assert events[-1]["summary"]["workers"] == 2


def test_invalid_tag_pattern_is_a_usage_error(
    demo_flock: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """R-03: exit 1 and a message that names the pattern; nothing is scanned."""
    code, out, err = run(capsys, *scan_args(demo_flock, cache_dir, "--tag-pattern", "("))
    assert code == 1 and out == ""
    assert "invalid tag pattern '('" in err
    assert not (cache_dir / "roots").exists()
    code, _, err = run(capsys, *scan_args(demo_flock, cache_dir, "--tag-pattern", r"(\d+)"))
    assert code == 1 and "named groups" in err
    code, out, _ = run(
        capsys, *scan_args(demo_flock, cache_dir, "--tag-pattern", "(", "--progress", "json")
    )
    assert code == 1 and json.loads(out.splitlines()[0])["event"] == "fatal"


def test_valid_tag_pattern_replaces_the_defaults(
    demo_flock: Path, cache_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pattern = r"(?P<key>run)_(?P<value>\d+)"
    code, _, _ = run(capsys, *scan_args(demo_flock, cache_dir, "--tag-pattern", pattern))
    assert code == 0
    store = ProfileStore(cache_dir, demo_flock)
    profile = store.load("exp_01/run_02/metrics.duckdb")
    assert profile is not None and profile.tags == {"run": "02"}


def test_the_cache_directory_inside_the_root_is_not_scanned(
    fresh_demo_flock: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """R-06: the catalog is a valid DuckDB file and must not become a flock member."""
    cache = fresh_demo_flock / "duckflock-cache"
    counts = []
    for _ in range(3):
        code, out, _ = run(
            capsys, *scan_args(fresh_demo_flock, cache, "--progress", "json")
        )
        assert code == 0
        counts.append(json.loads(out.splitlines()[-1])["summary"]["files_candidates"])
    assert counts == [13, 13, 13]
    catalogs = list(cache.rglob("catalog.duckdb"))
    assert len(catalogs) == 1 and catalogs[0].is_file()
