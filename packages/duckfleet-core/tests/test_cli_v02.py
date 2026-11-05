"""CLI commands ``sql``, ``summarize`` and ``preview`` (plan v0.2 Phase 3)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import duckdb
import pytest

from duckfleet_core.cli import main
from duckfleet_core.config import ScanConfig
from duckfleet_core.coordinator import run_scan
from duckfleet_core.demo import make_demo_fleet

DB = "exp_01/run_01/metrics.duckdb"
CROSS_JOIN = "select sum(a.i * b.i) from range(200000) a(i), range(200000) b(i)"


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture(scope="module")
def scanned(demo_fleet: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    cache = tmp_path_factory.mktemp("cli_v02_cache")
    run_scan(demo_fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    return demo_fleet, cache


def args(scanned: tuple[Path, Path], command: str, *rest: str) -> list[str]:
    fleet, cache = scanned
    return [command, str(fleet), *rest, "--cache-dir", str(cache)]


# ---------------------------------------------------------------- sql


def test_sql_prints_a_table(scanned, capsys) -> None:
    code, out, err = run(capsys, *args(scanned, "sql", DB, "select count(*) from events"))
    assert code == 0 and err == ""
    assert out.splitlines() == ["count_star()", "------------", "1000"]


def test_sql_table_aligns_columns_and_shows_null(scanned, capsys) -> None:
    sql = "select 1 as a, 'é long text' as b, NULL as c union all select 22, 'x', 3"
    code, out, _ = run(capsys, *args(scanned, "sql", DB, sql))
    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith("a  | b ")
    assert "NULL" in out and "é long text" in out
    assert len({line.index("|") for line in lines if "|" in line}) == 1  # aligned bars


def test_sql_max_rows_truncates(scanned, capsys) -> None:
    code, out, err = run(
        capsys, *args(scanned, "sql", DB, "select i from range(100) r(i)", "--max-rows", "5")
    )
    assert code == 0
    lines = out.splitlines()
    assert lines[2:7] == ["0", "1", "2", "3", "4"] and lines[-1] == "... (truncated)"
    code, out, err = run(
        capsys,
        *args(scanned, "sql", DB, "select i from range(100) r(i)", "--max-rows", "5",
              "--format", "csv"),
    )  # fmt: skip
    assert code == 0 and "... (truncated)" not in out and "truncated at 5 rows" in err


def test_sql_csv_and_json(scanned, capsys) -> None:
    sql = "select ts, value, run_id, [1, 2] as l from events order by ts limit 3"
    code, out, _ = run(capsys, *args(scanned, "sql", DB, sql, "--format", "csv"))
    assert code == 0
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == ["ts", "value", "run_id", "l"] and len(rows) == 4
    assert rows[1][0] == "2026-01-05 00:00:00" and rows[1][3] == "[1, 2]"

    code, out, _ = run(capsys, *args(scanned, "sql", DB, sql, "--format", "json"))
    assert code == 0
    objects = [json.loads(line) for line in out.splitlines()]
    assert len(objects) == 3 and set(objects[0]) == {"ts", "value", "run_id", "l"}
    assert objects[0]["ts"] == "2026-01-05 00:00:00" and objects[0]["l"] == [1, 2]

    code, out, _ = run(capsys, *args(scanned, "sql", DB, "select 1 as a, 2 as a, 'é' as b",
                                     "--format", "json"))  # fmt: skip
    assert json.loads(out) == {"a": 1, "a_2": 2, "b": "é"}


def test_sql_write_and_file_access_exit_with_3(scanned, capsys, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    insert = "insert into events values (now(), 1, 1)"
    code, out, err = run(capsys, *args(scanned, "sql", DB, insert))
    assert code == 3 and out == ""
    assert "(read_only)" in err and "read-only mode" in err
    code, out, err = run(capsys, *args(scanned, "sql", DB, "copy events to 'x.csv'"))
    assert code == 3 and "(blocked)" in err
    code, _, err = run(capsys, *args(scanned, "sql", DB, "selec 1"))
    assert code == 3 and "(sql)" in err
    assert list(tmp_path.iterdir()) == []


def test_sql_timeout_option(scanned, capsys) -> None:
    code, _, err = run(capsys, *args(scanned, "sql", DB, CROSS_JOIN, "--timeout", "0.5"))
    assert code == 3 and "(timeout)" in err
    code, out, _ = run(capsys, *args(scanned, "sql", DB, "select 7", "--timeout", "30"))
    assert code == 0 and out.splitlines()[-1] == "7"


def test_sql_multiple_statements_show_the_last_result(scanned, capsys) -> None:
    sql = "create temp table t1 as select 5 as v; select v * 2 as doubled from t1"
    code, out, _ = run(capsys, *args(scanned, "sql", DB, sql))
    assert code == 0 and out.splitlines() == ["doubled", "-------", "10"]


def test_database_names_accept_backslashes_and_a_dot_slash(scanned, capsys) -> None:
    for spelled in ("exp_01\\run_01\\metrics.duckdb", "./exp_01/run_01/metrics.duckdb"):
        code, out, _ = run(capsys, *args(scanned, "sql", spelled, "select 1"))
        assert code == 0 and out.splitlines()[-1] == "1", spelled


# ---------------------------------------------------------------- failures before the query


def test_no_catalog_is_fatal(demo_fleet: Path, cache_dir: Path, capsys) -> None:
    for argv in (
        ["sql", str(demo_fleet), DB, "select 1", "--cache-dir", str(cache_dir)],
        ["summarize", str(demo_fleet), DB, "main.events", "--cache-dir", str(cache_dir)],
        ["preview", str(demo_fleet), DB, "main.events", "--cache-dir", str(cache_dir)],
    ):
        code, out, err = run(capsys, *argv)
        assert code == 2 and out == "" and "duckfleet scan" in err


def test_unknown_database_and_table_are_usage_errors(scanned, capsys) -> None:
    code, out, err = run(capsys, *args(scanned, "sql", "nope/none.duckdb", "select 1"))
    assert code == 1 and out == "" and "unknown database 'nope/none.duckdb'" in err
    assert DB in err  # the catalog's databases are listed
    code, _, err = run(capsys, *args(scanned, "preview", DB, "main.nope"))
    assert code == 1 and "unknown table 'main.nope'" in err and "main.events" in err
    code, _, err = run(capsys, *args(scanned, "summarize", DB, "nope.events"))
    assert code == 1


def test_a_database_that_failed_in_the_scan_is_fatal(scanned, capsys) -> None:
    failures = (("broken/corrupt.duckdb", "corrupt"), ("secure/encrypted.duckdb", "encrypted"))
    for broken, kind in failures:
        code, out, err = run(capsys, *args(scanned, "sql", broken, "select 1"))
        assert code == 2 and out == "" and kind in err


def test_a_file_that_vanished_is_fatal(tmp_path: Path, capsys) -> None:
    fleet = make_demo_fleet(tmp_path / "fleet")
    cache = tmp_path / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    (fleet / "legacy" / "old.db").unlink()
    code, _, err = run(
        capsys, "sql", str(fleet), "legacy/old.db", "select 1", "--cache-dir", str(cache)
    )
    assert code == 2 and "no longer exists" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["sql"],
        ["sql", "root", "db"],
        ["sql", "root", "db", "select 1", "--format", "xml"],
        ["sql", "root", "db", "select 1", "--max-rows", "0"],
        ["sql", "root", "db", "select 1", "--timeout", "0"],
        ["summarize", "root", "db"],
        ["summarize", "root", "db", "main.t", "--format", "csv"],
        ["preview", "root", "db", "main.t", "--rows", "0"],
        ["preview", "root", "db", "main.t", "--offset", "-1"],
        ["preview", "root", "db", "main.t", "--format", "xml"],
    ],
)
def test_usage_errors_exit_with_1(argv: list[str], capsys) -> None:
    code, out, err = run(capsys, *argv)
    assert code == 1 and out == "" and "usage:" in err


# ---------------------------------------------------------------- summarize


def test_summarize_prints_the_columns_and_then_says_cached(scanned, capsys) -> None:
    argv = args(scanned, "summarize", DB, "main.events", "--refresh")
    code, out, err = run(capsys, *argv)
    assert code == 0 and "(computed in" in err and "cached" not in err
    lines = out.splitlines()
    header = [part.strip() for part in lines[0].split("|")]
    assert header == [
        "column_name", "column_type", "min", "max", "approx_unique", "avg", "std",
        "q25", "q50", "q75", "count", "null_percentage",
    ]  # fmt: skip
    body = lines[2:]
    assert [row.split("|")[0].strip() for row in body] == ["ts", "value", "run_id"]
    value = [part.strip() for part in body[1].split("|")]
    assert value[1] == "DOUBLE" and float(value[2]) < 0 < float(value[3])
    assert value[5] != "NULL" and value[7] != "NULL" and value[10] == "1000"

    code, out2, err2 = run(capsys, *args(scanned, "summarize", DB, "main.events"))
    assert code == 0 and err2.startswith("(cached 20") and out2 == out
    code, _, err3 = run(capsys, *args(scanned, "summarize", DB, "main.events", "--refresh"))
    assert code == 0 and "(computed in" in err3 and "cached" not in err3


def test_summarize_json_and_bare_table_name(scanned, capsys) -> None:
    code, out, _ = run(capsys, *args(scanned, "summarize", DB, "events", "--format", "json"))
    assert code == 0
    data = json.loads(out)
    assert data["table_name"] == "events" and data["schema_name"] == "main"
    assert data["row_count"] == 1000 and data["sampled"] is False and data["error"] is None
    assert [c["column_name"] for c in data["columns"]] == ["ts", "value", "run_id"]


def test_summarize_of_a_failing_table_exits_3(tmp_path: Path, capsys) -> None:
    fleet = make_demo_fleet(tmp_path / "fleet")
    cache = tmp_path / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    path = fleet / "exp_01" / "run_01" / "metrics.duckdb"
    con = duckdb.connect(str(path))
    con.execute("DROP TABLE config")  # the catalog still lists it
    con.close()
    code, out, err = run(
        capsys,
        "summarize", str(fleet), DB, "main.config", "--cache-dir", str(cache),
    )  # fmt: skip
    assert code == 3 and out == "" and "query error" in err


# ---------------------------------------------------------------- preview


def test_preview_descending_with_offset(scanned, capsys) -> None:
    fleet, _ = scanned
    con = duckdb.connect(str(fleet / DB), read_only=True)
    expected = [
        str(row[0])
        for row in con.execute(
            "select ts from events order by ts desc limit 5 offset 5"
        ).fetchall()
    ]
    con.close()
    code, out, err = run(
        capsys,
        *args(scanned, "preview", DB, "main.events", "--rows", "5", "--offset", "5",
              "--order-by", "ts", "--desc"),
    )  # fmt: skip
    assert code == 0 and err == ""
    lines = out.splitlines()
    assert lines[0].split("|")[0].strip() == "ts"
    got = [line.split("|")[0].strip() for line in lines[2:7]]
    assert got == expected and got == sorted(got, reverse=True)
    assert lines[-1] == "... (truncated)"  # more rows follow the page


def test_preview_columns_formats_and_defaults(scanned, capsys) -> None:
    code, out, _ = run(
        capsys,
        *args(scanned, "preview", DB, "main.events", "--columns", "run_id, ts", "--rows", "3",
              "--format", "csv"),
    )  # fmt: skip
    assert code == 0
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0] == ["run_id", "ts"] and len(rows) == 4 and rows[1][0] == "1"

    code, out, _ = run(capsys, *args(scanned, "preview", DB, "config", "--format", "json"))
    assert code == 0
    objects = [json.loads(line) for line in out.splitlines()]
    assert {o["key"] for o in objects} == {"learning_rate", "batch_size", "optimizer"}

    code, out, _ = run(capsys, *args(scanned, "preview", DB, "main.events"))
    assert code == 0 and len(out.splitlines()) == 2 + 20 + 1  # default 20 rows, then the marker


def test_preview_rejects_unknown_columns_with_exit_3(scanned, capsys) -> None:
    code, _, err = run(capsys, *args(scanned, "preview", DB, "main.events", "--columns", "nope"))
    assert code == 3 and "(sql)" in err and "nope" in err
    code, _, err = run(
        capsys, *args(scanned, "preview", DB, "main.events", "--order-by", "x; drop")
    )
    assert code == 3 and "(sql)" in err


def test_the_module_entry_point_runs_the_new_commands(scanned) -> None:
    import subprocess
    import sys

    fleet, cache = scanned
    result = subprocess.run(
        [sys.executable, "-m", "duckfleet_core", "sql", str(fleet), DB,
         "select count(*) as n from events", "--format", "json", "--cache-dir", str(cache)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"n": 1000}
