"""CLI commands on several databases (plan v0.4 Phase 3): fleet-tables, fleet-sql, plot --family."""

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

EVENTS_FILES = [
    "exp_01/run_01/metrics.duckdb", "exp_01/run_02/metrics.duckdb", "exp_01/run_03/metrics.duckdb",
    "exp_02/run_01/metrics.duckdb", "exp_02/run_02/metrics.duckdb", "exp_02/run_03/metrics.duckdb",
    "exp_03/run_01/metrics.duckdb", "exp_03/run_02/metrics.duckdb",
]  # fmt: skip
FAILED = {
    "broken/corrupt.duckdb": "corrupt",
    "broken/highbit.duckdb": "corrupt",
    "secure/encrypted.duckdb": "encrypted",
}
COUNT_SQL = "select database, count(*) n from events group by 1 order by 1"


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture(scope="module")
def scanned(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("cli_fleet")
    fleet = make_demo_fleet(base / "fleet")
    cache = base / "cache"
    run_scan(fleet, ScanConfig(workers=1, cache_dir=str(cache)))
    return fleet, cache


def args(
    scanned: tuple[Path, Path], command: str, *rest: str, root_first: bool = True
) -> list[str]:
    fleet, cache = scanned
    return [command, str(fleet), *rest, "--cache-dir", str(cache)]


def rows_of(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


# ---------------------------------------------------------------- fleet-tables


def test_fleet_tables_of_a_family_lists_events_and_config_with_three_members(
    scanned, capsys
) -> None:
    code, out, err = run(
        capsys, *args(scanned, "fleet-tables", "--family", "F1", "--format", "csv")
    )
    assert code == 0
    rows = rows_of(out)
    assert rows[0] == ["table", "members", "of", "missing_in", "cast_to_text"]
    assert rows[1:] == [["main.config", "3", "3", "", ""], ["main.events", "3", "3", "", ""]]
    assert "3 of 3 databases attached" in err and "attach failed" not in err


def test_fleet_tables_of_all_databases_names_who_lacks_a_table(scanned, capsys) -> None:
    code, out, err = run(capsys, *args(scanned, "fleet-tables", "--all", "--format", "json"))
    assert code == 0
    tables = {row["table"]: row for row in map(json.loads, out.splitlines())}
    assert set(tables) == {"main.config", "main.events", "main.notes", "main.samples"}
    events = tables["main.events"]
    assert events["members"] == 8 and events["of"] == 10  # the 3 failed files are not attached
    assert events["missing_in"] == "empty/empty.duckdb, legacy/old.db"  # not the failed files
    assert tables["main.notes"]["members"] == 2
    assert "exp_01/run_01/metrics.duckdb" in tables["main.notes"]["missing_in"]
    assert tables["main.samples"]["members"] == 1
    for rel, kind in FAILED.items():
        assert f"attach failed: {rel} ({kind})" in err
    assert "10 of 13 databases attached" in err


def test_the_default_selection_is_all_and_the_table_format_is_aligned(scanned, capsys) -> None:
    code, out, _err = run(capsys, *args(scanned, "fleet-tables"))
    assert code == 0
    assert out.splitlines()[0].split("|")[0].strip() == "table"
    assert "main.samples" in out


def test_fleet_tables_of_chosen_databases(scanned, capsys) -> None:
    chosen = f"{EVENTS_FILES[6]}, {EVENTS_FILES[0]},{EVENTS_FILES[6]}"  # spaces, a repeat
    code, out, _err = run(
        capsys, *args(scanned, "fleet-tables", "--databases", chosen, "--format", "csv")
    )
    assert code == 0
    rows = rows_of(out)[1:]
    notes = next(r for r in rows if r[0] == "main.notes")
    assert notes[1:4] == ["1", "2", EVENTS_FILES[0]]  # only exp_03/run_01 has notes


def test_a_loose_family_and_lower_case_ids(scanned, capsys) -> None:
    code, out, _err = run(
        capsys, *args(scanned, "fleet-tables", "--family", "l1", "--format", "csv")
    )
    assert code == 0
    events = next(r for r in rows_of(out) if r[0] == "main.events")
    assert events[1:3] == ["6", "6"]  # the loose family L1 holds exp_01 and exp_02


# ---------------------------------------------------------------- fleet-sql (acceptance 3)


def test_fleet_sql_counts_events_per_readable_file_and_reports_the_others(scanned, capsys) -> None:
    code, out, err = run(capsys, *args(scanned, "fleet-sql", COUNT_SQL, "--all", "--format", "csv"))
    assert code == 0
    rows = rows_of(out)
    assert rows[0] == ["database", "n"]
    assert [r[0] for r in rows[1:]] == EVENTS_FILES
    assert [r[1] for r in rows[1:]] == ["1000"] * 8
    assert sorted(
        line.split()[3] for line in err.splitlines() if "attach failed" in line
    ) == sorted(FAILED)  # the encrypted and corrupt files are attach failures on stderr


def test_fleet_sql_of_a_family_has_one_group_per_member(scanned, capsys) -> None:
    code, out, _err = run(capsys, *args(scanned, "fleet-sql", COUNT_SQL, "--family", "F1"))
    assert code == 0
    assert len(out.splitlines()) == 2 + 3  # header, rule, three databases


def test_fleet_sql_json_and_max_rows(scanned, capsys) -> None:
    code, out, err = run(
        capsys,
        *args(scanned, "fleet-sql", "select * from events", "--family", "F1", "--max-rows", "4",
              "--format", "json"),
    )  # fmt: skip
    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 4 and set(json.loads(lines[0])) >= {"database", "ts", "value"}
    assert "truncated at 4 rows" in err


def test_fleet_sql_aliases_and_union_views_are_both_visible(scanned, capsys) -> None:
    sql = (
        "select (select count(*) from exp_01_run_01_metrics.main.events) a, "
        "(select count(*) from events) b"
    )
    code, out, _err = run(
        capsys, *args(scanned, "fleet-sql", sql, "--family", "F1", "--format", "csv")
    )
    assert code == 0 and rows_of(out)[1] == ["1000", "3000"]


def test_a_write_is_exit_3_read_only_and_copy_to_is_exit_3_blocked(
    scanned, capsys, tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for sql in (
        "insert into events values ('x', NULL, 1.0, 1, NULL)",
        "insert into exp_01_run_01_metrics.main.events values (NULL, 1.0, 1)",
        "delete from events",
    ):
        code, out, err = run(capsys, *args(scanned, "fleet-sql", sql, "--family", "F1"))
        assert (code, out) == (3, "") and "query error (read_only)" in err, sql
    code, out, err = run(
        capsys, *args(scanned, "fleet-sql", "copy events to 'out.csv'", "--family", "F1")
    )
    assert (code, out) == (3, "") and "query error (blocked)" in err
    assert list(tmp_path.iterdir()) == []


def test_exit_codes_of_the_fleet_commands(scanned, capsys, tmp_path) -> None:
    code, _, err = run(capsys, *args(scanned, "fleet-sql", "selec 1", "--family", "F1"))
    assert code == 3 and "query error (sql)" in err
    code, _, err = run(
        capsys, *args(scanned, "fleet-sql", "select 1", "--databases", "nope.duckdb")
    )
    assert code == 1 and "unknown database(s) 'nope.duckdb'" in err
    code, _, err = run(capsys, *args(scanned, "fleet-sql", "select 1", "--family", "F99"))
    assert code == 1 and "unknown family 'F99'" in err and "F1" in err
    code, _, err = run(capsys, *args(scanned, "fleet-sql", "select 1", "--databases", ","))
    assert code == 1
    code, _, err = run(
        capsys, *args(scanned, "fleet-sql", "select 1", "--databases", "a.db", "--family", "F1")
    )
    assert code == 1 and "not allowed with argument" in err  # mutually exclusive
    fleet, _cache = scanned
    code, _, err = run(
        capsys, "fleet-sql", str(fleet), "select 1", "--cache-dir", str(tmp_path / "empty")
    )
    assert code == 2 and "no catalog" in err
    # nothing can be attached: a query error of kind other, not a usage error
    code, _, err = run(
        capsys, *args(scanned, "fleet-sql", "select 1", "--databases", "secure/encrypted.duckdb")
    )
    assert code == 3 and "query error (other)" in err and "none of the databases" in err


def test_fleet_sql_timeout_option(scanned, capsys) -> None:
    slow = "select sum(a.i * b.i) from range(200000) a(i), range(200000) b(i)"
    code, _, err = run(
        capsys, *args(scanned, "fleet-sql", slow, "--family", "F1", "--timeout", "0.5")
    )
    assert code == 3 and "(timeout)" in err


# ---------------------------------------------------------------- the limit L-12


@pytest.fixture(scope="module")
def fifty_one(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("cli_fleet51")
    root = base / "fleet"
    for i in range(51):
        path = root / f"d{i:02d}" / "x.duckdb"
        path.parent.mkdir(parents=True)
        con = duckdb.connect(str(path))
        con.execute(f"CREATE TABLE t AS SELECT {i} AS i")
        con.close()
    cache = base / "cache"
    run_scan(root, ScanConfig(workers=1, cache_dir=str(cache)))
    return root, cache


def test_more_than_fifty_databases_is_a_usage_error(fifty_one, capsys) -> None:
    for command, rest in (("fleet-tables", []), ("fleet-sql", ["select 1"])):
        code, out, err = run(capsys, *args(fifty_one, command, *rest, "--all"))
        assert code == 1 and out == "", command
        assert "51 databases chosen, but at most 50" in err
    names = ",".join(f"d{i:02d}/x.duckdb" for i in range(51))
    code, _, err = run(capsys, *args(fifty_one, "fleet-sql", "select 1", "--databases", names))
    assert code == 1 and "at most 50" in err


def test_exactly_fifty_databases_work(fifty_one, capsys) -> None:
    names = ",".join(f"d{i:02d}/x.duckdb" for i in range(50))
    code, out, err = run(
        capsys,
        *args(fifty_one, "fleet-sql", "select count(*) n, sum(i) s from t", "--databases", names,
              "--format", "csv"),
    )  # fmt: skip
    assert code == 0 and rows_of(out)[1] == ["50", str(sum(range(50)))]
    assert "attach failed" not in err


# ---------------------------------------------------------------- plot over several databases


def plot(scanned, *rest: str) -> list[str]:
    fleet, cache = scanned
    return ["plot", str(fleet), *rest, "--cache-dir", str(cache)]


def test_a_family_plot_prints_bucketed_rows_with_three_database_series(scanned, capsys) -> None:
    code, out, err = run(
        capsys,
        *plot(scanned, "--family", "F1", "main.events", "--kind", "line", "--x", "ts", "--y",
              "value", "--max-points", "300", "--format", "csv"),
    )  # fmt: skip
    assert code == 0, err
    rows = rows_of(out)
    assert rows[0] == ["ts", "database", "value_mean", "value_min", "value_max", "value_count"]
    assert {r[1] for r in rows[1:]} == set(EVENTS_FILES[:3])  # one series per family member
    assert 3 < len(rows) - 1 <= 300
    assert sum(int(r[5]) for r in rows[1:]) == 3000  # the buckets cover every row
    assert "databases=3" in err and "strategy=time_bucket" in err


def test_databases_plot_with_a_bare_table_name_and_a_colour_override(scanned, capsys) -> None:
    chosen = f"{EVENTS_FILES[0]},{EVENTS_FILES[3]}"
    code, out, err = run(
        capsys,
        *plot(scanned, "--databases", chosen, "events", "--kind", "bar", "--x", "run_id",
              "--color", "run_id", "--format", "csv"),
    )  # fmt: skip
    assert code == 0, err
    rows = rows_of(out)
    assert rows[0][:2] == ["run_id", "run_id_2"]  # colour by a real column, not the database
    default = run(
        capsys, *plot(scanned, "--databases", chosen, "events", "--kind", "bar", "--x", "run_id",
                      "--format", "csv"),
    )  # fmt: skip
    assert default[0] == 0
    assert rows_of(default[1])[0][:2] == ["run_id", "database"]


def test_a_plot_with_a_failed_member_notes_it_on_stderr(scanned, capsys) -> None:
    chosen = f"{EVENTS_FILES[0]},secure/encrypted.duckdb"
    code, out, err = run(
        capsys,
        *plot(scanned, "--databases", chosen, "main.events", "--kind", "line", "--x", "ts",
              "--y", "value", "--format", "csv"),
    )  # fmt: skip
    assert code == 0, err
    assert "databases=1" in err
    assert "note: secure/encrypted.duckdb could not be attached (encrypted)" in err
    assert len(rows_of(out)) == 1001


def test_fleet_plot_usage_errors(scanned, capsys) -> None:
    common = ("--kind", "line", "--x", "ts", "--y", "value")
    code, _, err = run(capsys, *plot(scanned, "--family", "F1", "nope", *common))
    assert code == 1 and "unknown table 'nope'" in err and "main.events" in err
    code, _, err = run(
        capsys, *plot(scanned, "--family", "F1", "main.events", "main.events", *common)
    )
    assert code == 1 and "give only ROOT and SCHEMA.TABLE" in err
    code, _, err = run(capsys, *plot(scanned, EVENTS_FILES[0], *common))
    assert code == 1 and "missing SCHEMA.TABLE" in err
    code, _, err = run(capsys, *plot(scanned, "--family", "F9", "main.events", *common))
    assert code == 1 and "unknown family" in err
    code, _, err = run(
        capsys, *plot(scanned, "--family", "F1", "--databases", "a", "events", *common)
    )
    assert code == 1 and "not allowed with argument" in err


def test_fleet_plot_of_more_than_eight_y_columns_and_text_columns(scanned, capsys) -> None:
    code, _, err = run(
        capsys,
        *plot(scanned, "--family", "F1", "main.events", "--kind", "line", "--x", "ts", "--y",
              ",".join(f"c{i}" for i in range(9))),
    )  # fmt: skip
    assert code == 1 and "at most 8 y columns" in err


def test_a_single_database_plot_is_unchanged(scanned, capsys) -> None:
    code, out, err = run(
        capsys,
        *plot(scanned, EVENTS_FILES[0], "main.events", "--kind", "line", "--x", "ts", "--y",
              "value", "--max-points", "100", "--format", "csv"),
    )  # fmt: skip
    assert code == 0
    assert rows_of(out)[0] == ["ts", "value_mean", "value_min", "value_max", "value_count"]
    assert "databases=" not in err and "bucket=3 hours" in err


# ---------------------------------------------------------------- review fixes of 0.4.1


def test_attach_failures_are_printed_before_a_query_error(scanned, capsys) -> None:
    """X-06: a query error used to hide the attach failures (they were printed after success)."""
    code, out, err = run(capsys, *args(scanned, "fleet-sql", "selec 1", "--all"))
    assert (code, out) == (3, "")
    lines = err.splitlines()
    failed = [i for i, line in enumerate(lines) if "attach failed" in line]
    assert sorted(lines[i].split()[3] for i in failed) == sorted(FAILED)
    (error,) = [i for i, line in enumerate(lines) if "query error (sql)" in line]
    assert max(failed) < error  # the failures come first, the error last


def test_a_bar_chart_by_database_has_no_duplicate_column(scanned, capsys) -> None:
    """X-07: ``--x database`` without ``--color`` also coloured by ``database``."""
    code, out, err = run(
        capsys,
        *plot(scanned, "--family", "F1", "main.events", "--kind", "bar", "--x", "database",
              "--format", "csv"),
    )  # fmt: skip
    assert code == 0, err
    rows = rows_of(out)
    assert rows[0] == ["database", "count"]
    assert sorted(r[0] for r in rows[1:]) == EVENTS_FILES[:3] and {r[1] for r in rows[1:]} == {
        "1000"
    }
    # any other x keeps the default colour
    other = run(
        capsys,
        *plot(scanned, "--family", "F1", "main.events", "--kind", "bar", "--x", "run_id",
              "--format", "csv"),
    )  # fmt: skip
    assert rows_of(other[1])[0] == ["run_id", "database", "count"]


def test_more_than_fifty_family_members_are_a_usage_error(fifty_one, capsys) -> None:
    """The family F1 of this fleet holds 51 databases (the same structure in every file)."""
    for command, rest in (("fleet-tables", []), ("fleet-sql", ["select 1"])):
        code, out, err = run(capsys, *args(fifty_one, command, *rest, "--family", "F1"))
        assert code == 1 and out == "", command
        assert "51 databases chosen, but at most 50" in err
    code, _out, err = run(
        capsys,
        *plot(fifty_one, "--family", "F1", "main.t", "--kind", "bar", "--x", "i"),
    )
    assert code == 1 and "at most 50" in err


@pytest.fixture(scope="module")
def comma_fleet(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("cli_fleet_comma")
    root = base / "fleet"
    for rel, value in (("x,y/a.duckdb", 1), ("z.duckdb", 2), ("w.duckdb", 3)):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path))
        con.execute(f"CREATE TABLE t AS SELECT {value} AS v")
        con.close()
    cache = base / "cache"
    run_scan(root, ScanConfig(workers=1, cache_dir=str(cache)))
    return root, cache


def test_databases_can_be_repeated_and_a_rel_path_with_a_comma_is_taken_whole(
    comma_fleet, capsys
) -> None:
    """P-12: ``--databases`` splits on commas, so ``x,y/a.duckdb`` could not be selected."""
    sql = "select database, v from t order by v"
    code, out, err = run(  # repeated flags, one of them a rel_path with a comma
        capsys,
        *args(comma_fleet, "fleet-sql", sql, "--databases", "x,y/a.duckdb", "--databases",
              "z.duckdb", "--format", "csv"),
    )  # fmt: skip
    assert code == 0, err
    assert rows_of(out)[1:] == [["x,y/a.duckdb", "1"], ["z.duckdb", "2"]]
    code, _out, err = run(  # a value that is not one rel_path as a whole is still a comma list
        capsys, *args(comma_fleet, "fleet-sql", sql, "--databases", "x,y/a.duckdb,z.duckdb")
    )
    assert code == 1 and "unknown database(s) 'x', 'y/a.duckdb'" in err
    code, out, err = run(
        capsys,
        *args(comma_fleet, "fleet-sql", sql, "--databases", "x,y/a.duckdb", "--format", "csv"),
    )  # one flag, one rel_path that contains a comma
    assert code == 0, err
    assert rows_of(out)[1:] == [["x,y/a.duckdb", "1"]]
    code, out, err = run(  # the usual comma list and a repeat that adds to it
        capsys,
        *args(comma_fleet, "fleet-sql", sql, "--databases", "z.duckdb,w.duckdb", "--databases",
              "z.duckdb", "--format", "csv"),
    )  # fmt: skip
    assert code == 0, err
    assert rows_of(out)[1:] == [["z.duckdb", "2"], ["w.duckdb", "3"]]


@pytest.fixture(scope="module")
def unicode_fleet(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("cli_fleet_unicode")
    root = base / "fleet"
    members = (("日本/a.duckdb", ""), ("é/b.duckdb", "CREATE TABLE only_b AS SELECT 1 AS w"))
    for rel, extra in members:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path))
        con.execute("CREATE TABLE t AS SELECT 7 AS v")
        if extra:
            con.execute(extra)
        con.close()
    cache = base / "cache"
    run_scan(root, ScanConfig(workers=1, cache_dir=str(cache)))
    return root, cache


def _fleet_bytes(fleet: tuple[Path, Path], *rest: str) -> tuple[int, bytes, bytes]:
    """The real CLI in a child whose stdout would be latin-1 without the UTF-8 writer (V-03)."""
    root, cache = fleet
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONIOENCODING"] = "latin-1"
    command, *others = rest
    positional = [others.pop(0)] if command == "fleet-sql" else []
    done = subprocess.run(
        [sys.executable, "-m", "duckfleet_core", command, str(root), *positional, "--all",
         *others, "--cache-dir", str(cache)],
        capture_output=True,
        env=env,
        timeout=120,
    )  # fmt: skip
    return done.returncode, done.stdout, done.stderr


def test_fleet_sql_csv_and_json_through_a_subprocess_are_utf8(unicode_fleet) -> None:
    sql = "select database, v from t order by 1"
    code, out, err = _fleet_bytes(unicode_fleet, "fleet-sql", sql, "--format", "csv")
    assert code == 0, err
    assert out == "database,v\né/b.duckdb,7\n日本/a.duckdb,7\n".encode() and b"\r" not in out
    code, out, err = _fleet_bytes(unicode_fleet, "fleet-sql", sql, "--format", "json")
    assert code == 0, err
    assert out.decode("utf-8").split("\n") == [
        '{"database": "é/b.duckdb", "v": 7}',
        '{"database": "日本/a.duckdb", "v": 7}',
        "",
    ]


def test_fleet_tables_csv_and_json_through_a_subprocess_are_utf8(unicode_fleet) -> None:
    code, out, err = _fleet_bytes(unicode_fleet, "fleet-tables", "--format", "csv")
    assert code == 0, err
    assert out.decode("utf-8").split("\n") == [
        "table,members,of,missing_in,cast_to_text",
        "main.only_b,1,2,日本/a.duckdb,",
        "main.t,2,2,,",
        "",
    ]
    code, out, err = _fleet_bytes(unicode_fleet, "fleet-tables", "--format", "json")
    assert code == 0, err
    first = json.loads(out.decode("utf-8").splitlines()[0])
    assert first["missing_in"] == "日本/a.duckdb"


@pytest.fixture(scope="module")
def own_label_fleet(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Two files whose table ``t`` has a column named ``database`` of its own (D-044)."""
    base = tmp_path_factory.mktemp("cli_fleet_label")
    root = base / "fleet"
    for rel, offset in (("a.duckdb", 0), ("b.duckdb", 100)):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(path))
        con.execute(
            f"CREATE TABLE t AS SELECT 'own' AS database, TIMESTAMP '2026-01-01' + "
            f"INTERVAL (i) HOUR AS ts, i + {offset} AS v FROM range(10) r(i)"
        )
        con.close()
    cache = base / "cache"
    run_scan(root, ScanConfig(workers=1, cache_dir=str(cache)))
    return root, cache


def test_a_plot_over_a_view_with_the_fallback_label_column(own_label_fleet, capsys) -> None:
    """The label column is ``duckfleet_database`` here, and the default colour follows it."""
    fleet, cache = own_label_fleet
    code, out, err = run(
        capsys,
        "plot", str(fleet), "--databases", "a.duckdb,b.duckdb", "main.t", "--kind", "line",
        "--x", "ts", "--y", "v", "--format", "csv", "--cache-dir", str(cache),
    )  # fmt: skip
    assert code == 0, err
    rows = rows_of(out)
    assert rows[0][:2] == ["ts", "duckfleet_database"]
    assert {r[1] for r in rows[1:]} == {"a.duckdb", "b.duckdb"} and len(rows) == 21
    assert "databases=2" in err
    # x = the label column: no default colour either
    code, out, err = run(
        capsys,
        "plot", str(fleet), "--databases", "a.duckdb,b.duckdb", "main.t", "--kind", "bar",
        "--x", "duckfleet_database", "--format", "csv", "--cache-dir", str(cache),
    )  # fmt: skip
    assert code == 0, err
    assert rows_of(out)[0] == ["duckfleet_database", "count"]
    # the member's own `database` column is still there to colour by
    code, out, err = run(
        capsys,
        "plot", str(fleet), "--databases", "a.duckdb,b.duckdb", "main.t", "--kind", "bar",
        "--x", "database", "--format", "csv", "--cache-dir", str(cache),
    )  # fmt: skip
    assert code == 0, err
    assert rows_of(out)[0][:2] == ["database", "duckfleet_database"]
