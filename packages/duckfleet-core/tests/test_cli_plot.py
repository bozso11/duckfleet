"""CLI command ``plot`` (plan v0.3 Phase 2)."""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from duckfleet_core.cli import main
from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.demo import make_demo_fleet

DB = "exp_01/run_01/metrics.duckdb"
BIG = "exp_04/run_01/metrics.duckdb"
SLOW_FILTER = "(select sum(a.i * b.i) from range(200000) a(i), range(200000) b(i)) > 0"


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture(scope="module")
def scanned(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("cli_plot")
    fleet = make_demo_fleet(base / "fleet", big=True)
    cache = base / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    return fleet, cache


def plot(scanned: tuple[Path, Path], *rest: str, database: str = DB, table: str = "main.events"):
    fleet, cache = scanned
    return ["plot", str(fleet), database, table, *rest, "--cache-dir", str(cache)]


# ---------------------------------------------------------------- acceptance 3 and 5


def test_a_line_plot_with_a_small_budget_prints_buckets(scanned, capsys) -> None:
    argv = plot(scanned, "--kind", "line", "--x", "ts", "--y", "value", "--max-points", "100",
                "--format", "csv")  # fmt: skip
    code, out, err = run(capsys, *argv)
    assert code == 0
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == ["ts", "value_mean", "value_min", "value_max", "value_count"]
    assert 0 < len(rows) - 1 < 100
    assert sum(int(r[4]) for r in rows[1:]) == 1000
    assert "strategy=time_bucket" in err and "bucket=3 hours" in err
    assert "points=" in err and "rows_scanned=1000" in err and "elapsed=" in err
    assert "INTERVAL" not in err  # the SQL only with --show-sql


def test_show_sql_prints_the_final_query_on_stderr(scanned, capsys) -> None:
    argv = plot(scanned, "--kind", "line", "--x", "ts", "--y", "value", "--max-points", "100",
                "--show-sql")  # fmt: skip
    code, out, err = run(capsys, *argv)
    assert code == 0 and "time_bucket(INTERVAL '3 hours'" in err
    assert "INTERVAL" not in out


def test_the_big_demo_table_is_bucketed_with_the_default_budget(scanned, capsys) -> None:
    argv = plot(scanned, "--kind", "line", "--x", "ts", "--y", "value", database=BIG)
    code, out, err = run(capsys, *argv)
    assert code == 0
    rows = list(csv.reader(io.StringIO(out)))
    assert 40_000 < len(rows) - 1 <= 50_000
    assert "bucket=1 minute" in err and "rows_scanned=200000" in err


# ---------------------------------------------------------------- every kind


def test_line_raw_as_json(scanned, capsys) -> None:
    argv = plot(scanned, "--kind", "line", "--x", "ts", "--y", "value", "--format", "json")
    code, out, err = run(capsys, *argv)
    assert code == 0 and "strategy=raw" in err and "bucket=" not in err
    objects = [json.loads(line) for line in out.splitlines()]
    assert len(objects) == 1000 and set(objects[0]) == {"ts", "value"}
    assert [o["ts"] for o in objects] == sorted(o["ts"] for o in objects)


def test_line_by_row_order_and_two_columns(scanned, capsys) -> None:
    code, out, _ = run(capsys, *plot(scanned, "--kind", "line", "--y", "value,run_id"))
    assert code == 0
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == ["(row order)", "value", "run_id"] and rows[1][0] == "1"
    again = plot(scanned, "--kind", "line", "--y", "value", "--y", "run_id")  # repeated --y
    code, out_again, _ = run(capsys, *again)
    assert code == 0 and out_again == out


def test_scatter_is_sampled_above_the_budget(scanned, capsys) -> None:
    argv = plot(scanned, "--kind", "scatter", "--x", "value", "--y", "run_id", "--max-points",
                "200", "--format", "json")  # fmt: skip
    code, out, err = run(capsys, *argv)
    assert code == 0 and "strategy=sample" in err and " sampled" in err
    assert len(out.splitlines()) == 200
    code, out_again, _ = run(capsys, *argv)
    assert out_again == out  # the seed is fixed


def test_histogram(scanned, capsys) -> None:
    argv = plot(scanned, "--kind", "histogram", "--y", "value", "--bins", "10", "--format", "json")
    code, out, err = run(capsys, *argv)
    assert code == 0 and "strategy=histogram" in err
    objects = [json.loads(line) for line in out.splitlines()]
    assert set(objects[0]) == {"y_column", "bin_start", "bin_end", "count"}
    assert 5 <= len(objects) <= 10 and sum(o["count"] for o in objects) == 1000


def test_bar_of_the_config_keys(scanned, capsys) -> None:
    argv = plot(scanned, "--kind", "bar", "--x", "key", "--format", "json", table="main.config")
    code, out, err = run(capsys, *argv)
    assert code == 0 and "strategy=bar" in err
    objects = [json.loads(line) for line in out.splitlines()]
    assert {o["key"]: o["count"] for o in objects} == {
        "learning_rate": 1, "batch_size": 1, "optimizer": 1,
    }  # fmt: skip


def test_bar_with_a_measure_and_a_filter(scanned, capsys) -> None:
    argv = plot(scanned, "--kind", "bar", "--x", "run_id", "--y", "value", "--agg", "max",
                "--where", "value > 0", "--format", "csv")  # fmt: skip
    code, out, _ = run(capsys, *argv)
    assert code == 0
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == ["run_id", "max(value)"] and rows[1][0] == "1" and len(rows) == 2


def test_folded_categories_are_reported(scanned, capsys) -> None:
    argv = plot(scanned, "--kind", "bar", "--x", "ts", "--format", "csv")  # 1000 categories
    code, out, err = run(capsys, *argv)
    assert code == 0 and "categories_folded=800" in err
    rows = list(csv.reader(io.StringIO(out)))
    assert len(rows) - 1 == 201 and rows[-1][0] == "(other)" and rows[-1][1] == "800"


# ---------------------------------------------------------------- exit codes


def test_usage_errors_exit_1(scanned, capsys) -> None:
    cases = [
        plot(scanned, "--kind", "pie"),  # argparse choice
        plot(scanned, "--kind", "line"),  # no y
        plot(scanned, "--kind", "bar"),  # no x
        plot(scanned, "--kind", "histogram", "--y", "value", "--bins", "3"),
        plot(scanned, "--kind", "line", "--y", "value", "--max-points", "50"),
        plot(scanned, "--kind", "line", "--y", "value", database="nope.duckdb"),
        plot(scanned, "--kind", "line", "--y", "value", table="main.nope"),
        plot(scanned, "--kind", "line", "--y", "value", "--timeout", "0"),
    ]
    for argv in cases:
        code, out, err = run(capsys, *argv)
        assert code == 1 and out == "", argv
        assert err.strip(), argv


def test_a_missing_catalog_exits_2(scanned, tmp_path: Path, capsys) -> None:
    fleet, _cache = scanned
    argv = ["plot", str(fleet), DB, "main.events", "--kind", "line", "--y", "value",
            "--cache-dir", str(tmp_path / "empty")]  # fmt: skip
    code, out, err = run(capsys, *argv)
    assert code == 2 and out == "" and "duckfleet scan" in err


def test_query_errors_exit_3(scanned, capsys) -> None:
    cases = {
        "unknown column": plot(scanned, "--kind", "line", "--y", "nope"),
        "y columns must be numeric": plot(scanned, "--kind", "line", "--y", "ts"),
        "no_such_column": plot(
            scanned, "--kind", "line", "--y", "value", "--where", "no_such_column > 1"
        ),
        "syntax error": plot(scanned, "--kind", "line", "--y", "value", "--where", "value >"),
    }
    for message, argv in cases.items():
        code, out, err = run(capsys, *argv)
        assert code == 3 and out == "" and "(sql)" in err, (message, err)
        assert message.lower() in err.lower(), (message, err)


def test_a_timeout_exits_3(scanned, capsys) -> None:
    argv = plot(scanned, "--kind", "line", "--y", "value", "--where", SLOW_FILTER,
                "--timeout", "0.5")  # fmt: skip
    code, out, err = run(capsys, *argv)
    assert code == 3 and "(timeout)" in err and out == ""


def test_nothing_is_written_to_the_working_directory(
    scanned, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    run(capsys, *plot(scanned, "--kind", "line", "--x", "ts", "--y", "value"))
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- through a subprocess (0.3.1)


@pytest.fixture(scope="module")
def unicode_fleet(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("cli_plot_unicode")
    root = base / "fleet"
    root.mkdir()
    con = duckdb.connect(str(root / "uni.duckdb"))
    con.execute(
        "CREATE TABLE t AS SELECT CASE WHEN i % 2 = 0 THEN '日本語 é' ELSE 'plain' END AS k, "
        "i::DOUBLE AS v FROM range(10) r(i)"
    )
    con.close()
    cache = base / "cache"
    run_scan(root, ScanConfig(workers=1, cache_dir=str(cache)))
    return root, cache


def _plot_bytes(fleet: tuple[Path, Path], *rest: str) -> tuple[int, bytes, bytes]:
    """The real CLI in a child whose stdout would be latin-1 without the UTF-8 writer (V-03)."""
    root, cache = fleet
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONIOENCODING"] = "latin-1"
    done = subprocess.run(
        [sys.executable, "-m", "duckfleet_core", "plot", str(root), "uni.duckdb", "main.t", *rest,
         "--cache-dir", str(cache)],
        capture_output=True,
        env=env,
        timeout=120,
    )  # fmt: skip
    return done.returncode, done.stdout, done.stderr


def test_plot_csv_through_a_subprocess_is_utf8_with_lf_line_ends(unicode_fleet) -> None:
    code, out, err = _plot_bytes(unicode_fleet, "--kind", "bar", "--x", "k", "--y", "v")
    assert code == 0, err
    assert out == "k,mean(v)\nplain,5.0\n日本語 é,4.0\n".encode()


def test_plot_json_through_a_subprocess_is_utf8_one_object_per_line(unicode_fleet) -> None:
    code, out, err = _plot_bytes(
        unicode_fleet, "--kind", "bar", "--x", "k", "--y", "v", "--format", "json"
    )
    assert code == 0, err
    lines = out.decode("utf-8").split("\n")
    assert lines == ['{"k": "plain", "mean(v)": 5.0}', '{"k": "日本語 é", "mean(v)": 4.0}', ""]
    assert b"\r" not in out


def test_more_than_eight_y_columns_is_a_usage_error(scanned, capsys) -> None:
    """W-03: the CLI reports the spec limit as a usage error (exit 1)."""
    names = ",".join(f"c{i}" for i in range(9))
    code, out, err = run(capsys, *plot(scanned, "--kind", "line", "--x", "ts", "--y", names))
    assert code == 1 and out == ""
    assert "at most 8 y columns can be plotted at once (got 9)" in err


def test_a_seed_is_not_a_cli_option_but_a_bad_spec_value_is_a_usage_error() -> None:
    """W-06: the spec rejects a seed outside the reservoir range before any SQL runs."""
    from pydantic import ValidationError

    from duckfleet_core.plotdata import PlotSpec

    with pytest.raises(ValidationError):
        PlotSpec(schema_name="main", table_name="events", kind="scatter", y=["value"], seed=-5)
