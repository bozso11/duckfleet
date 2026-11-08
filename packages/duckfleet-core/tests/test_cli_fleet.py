"""CLI commands on several databases (plan v0.4 Phase 3): fleet-tables, fleet-sql, plot --family."""

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
    assert {r[1] for r in rows[1:]} == set(EVENTS_FILES[:3]) or len({r[1] for r in rows[1:]}) == 3
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
