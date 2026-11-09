"""Plots over a flock connection (plan v0.4 Phase 3): fetch_plot_data_on, fetch_flock_plot_data."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from duckflock_core.flock import FlockSelection, describe_flock, open_flock
from duckflock_core.plotdata import (
    OTHER,
    PlotSpec,
    fetch_flock_plot_data,
    fetch_plot_data,
    fetch_plot_data_on,
)
from duckflock_core.query import QueryConfig, QueryError, database_name, open_readonly

EVENTS_FILES = [
    "exp_01/run_01/metrics.duckdb", "exp_01/run_02/metrics.duckdb", "exp_01/run_03/metrics.duckdb",
    "exp_02/run_01/metrics.duckdb", "exp_02/run_02/metrics.duckdb", "exp_02/run_03/metrics.duckdb",
    "exp_03/run_01/metrics.duckdb", "exp_03/run_02/metrics.duckdb",
]  # fmt: skip
F1 = EVENTS_FILES[:3]


@pytest.fixture
def config(cache_dir: Path) -> QueryConfig:
    return QueryConfig(temp_dir=str(cache_dir / "tmp" / "flock-plot"), threads=2)


def spec(kind: str = "line", table: str = "events", **fields) -> PlotSpec:
    base: dict = {"schema_name": "main", "table_name": table, "kind": kind}
    if kind != "bar":
        base["y"] = ["value"]
    return PlotSpec(**(base | fields))


def flock_plot(root: Path, rels: list[str], plot: PlotSpec, config: QueryConfig):
    paths = {rel: str(root / rel) for rel in rels}
    return fetch_flock_plot_data(FlockSelection(rel_paths=rels), paths, plot, config)


def test_plotting_on_an_open_connection_equals_plotting_the_file(
    demo_flock: Path, config: QueryConfig
) -> None:
    plot = spec(x="ts", max_points=100)
    direct = fetch_plot_data(demo_flock / F1[0], plot, config)
    with open_readonly(demo_flock / F1[0], config) as con:
        on = fetch_plot_data_on(con, database_name(con), plot, config)
    assert on.rows == direct.rows and on.columns == direct.columns and on.sql == direct.sql
    assert on.strategy == direct.strategy == "time_bucket"


def test_a_family_plot_has_one_series_per_database(demo_flock: Path, config: QueryConfig) -> None:
    data = flock_plot(demo_flock, F1, spec(x="ts", color="database", max_points=300), config)
    assert data.strategy == "time_bucket" and data.series == F1  # equal counts: by name
    assert data.columns[:2] == ["ts", "database"] and data.rows_scanned == 3000
    assert {row[1] for row in data.rows} == set(F1)
    assert sum(row[-1] for row in data.rows) == 3000
    assert data.points <= 300 and data.notes == []
    assert '"memory"."main"."events"' in data.sql  # the plan runs on the union view


def test_more_series_than_the_cap_fold_the_rest_into_other(
    demo_flock: Path, config: QueryConfig
) -> None:
    data = flock_plot(
        demo_flock, EVENTS_FILES, spec(x="ts", color="database", max_series=3), config
    )
    assert data.series == [*EVENTS_FILES[:3], OTHER] and data.folded_series == 5
    assert sum(1 for row in data.rows if row[1] == OTHER) > 0
    default = flock_plot(demo_flock, EVENTS_FILES, spec(x="ts", color="database"), config)
    assert len(default.series) == 8 and default.folded_series == 0  # eight files fit the cap


def test_a_scatter_over_files_is_capped_at_three_series(
    demo_flock: Path, config: QueryConfig
) -> None:
    data = flock_plot(
        demo_flock, EVENTS_FILES, spec("scatter", x="ts", color="database", max_points=500), config
    )
    assert data.sampled and data.series[-1] == OTHER and len(data.series) == 4
    assert len(data.rows) <= 500


def test_a_bar_chart_counts_rows_per_database(demo_flock: Path, config: QueryConfig) -> None:
    data = flock_plot(demo_flock, F1, spec("bar", x="database"), config)
    assert data.columns == ["database", "count"]
    assert sorted(data.rows) == sorted((rel, 1000) for rel in F1)
    by_run = flock_plot(demo_flock, F1, spec("bar", x="run_id", color="database"), config)
    assert sum(row[-1] for row in by_run.rows) == 3000 and by_run.series == F1


def test_a_histogram_and_the_row_order_work_on_the_union_view(
    demo_flock: Path, config: QueryConfig
) -> None:
    hist = flock_plot(demo_flock, F1, spec("histogram", color="database", bins=10), config)
    assert sum(row[-1] for row in hist.rows) == 3000 and hist.series == F1
    by_row = flock_plot(demo_flock, F1, spec(max_points=100), config)
    assert by_row.strategy == "numeric_bucket" and by_row.columns[0] == "(row order)"
    assert sum(row[-1] for row in by_row.rows) == 3000


def test_a_column_missing_in_some_files_is_null_there(
    demo_flock: Path, config: QueryConfig
) -> None:
    rels = [EVENTS_FILES[0], EVENTS_FILES[6]]  # only exp_03 has quality
    data = flock_plot(
        demo_flock, rels, spec("histogram", y=["quality"], color="database", bins=5), config
    )
    assert data.series == [rels[1]] and data.rows_scanned == 2000
    assert any("1,000 rows without a finite value left out" in note for note in data.notes)


def test_attach_failures_are_notes(demo_flock: Path, config: QueryConfig) -> None:
    rels = [EVENTS_FILES[0], "secure/encrypted.duckdb", "broken/corrupt.duckdb"]
    data = flock_plot(demo_flock, rels, spec(x="ts", color="database"), config)
    assert data.series == [EVENTS_FILES[0]] and data.rows_scanned == 1000
    assert [n.split(" could not be")[0] for n in data.notes] == [
        "secure/encrypted.duckdb", "broken/corrupt.duckdb",
    ]  # fmt: skip
    assert "(encrypted)" in data.notes[0] and "(corrupt)" in data.notes[1]


def test_an_unknown_table_and_unknown_columns_are_sql_errors(
    demo_flock: Path, config: QueryConfig
) -> None:
    with pytest.raises(QueryError) as caught:
        flock_plot(demo_flock, F1, spec(table="nope", x="ts"), config)
    assert caught.value.kind == "sql"
    assert (
        "unknown table main.nope" in caught.value.message and "main.events" in caught.value.message
    )
    with pytest.raises(QueryError, match="unknown column"):
        flock_plot(demo_flock, F1, spec(x="ts", color="no_such_column"), config)


def test_a_column_that_is_text_in_the_view_cannot_be_a_y_or_x_column(
    tmp_path: Path, config: QueryConfig
) -> None:
    for name, expr in (("a", "1"), ("b", "'x'")):
        con = duckdb.connect(str(tmp_path / f"{name}.duckdb"))
        con.execute(f"CREATE TABLE t AS SELECT {expr} AS v, i AS n FROM range(5) r(i)")
        con.close()
    paths = {f"{n}.duckdb": str(tmp_path / f"{n}.duckdb") for n in "ab"}
    selection = FlockSelection(rel_paths=list(paths))
    plot = PlotSpec(schema_name="main", table_name="t", kind="line", x="n", y=["v"])
    with pytest.raises(QueryError) as caught:
        fetch_flock_plot_data(selection, paths, plot, config)
    assert caught.value.kind == "sql" and "v has different types" in caught.value.message
    ok = PlotSpec(schema_name="main", table_name="t", kind="line", x="n", y=["n"], color="database")
    assert fetch_flock_plot_data(selection, paths, ok, config).series == list(paths)
    as_category = PlotSpec(schema_name="main", table_name="t", kind="bar", x="v")
    categories = fetch_flock_plot_data(selection, paths, as_category, config)
    assert sorted(row[0] for row in categories.rows) == ["1", "x"]


def test_the_timeout_covers_the_flock_plot(demo_flock: Path, config: QueryConfig) -> None:
    slow = "(select sum(a.i * b.i) from range(100000) a(i), range(100000) b(i)) > 0"
    quick = config.model_copy(update={"timeout_s": 0.5})
    with pytest.raises(QueryError) as caught:
        flock_plot(demo_flock, F1, spec(x="ts", where=slow), quick)
    assert caught.value.kind == "timeout"
    assert flock_plot(demo_flock, F1, spec(x="ts"), config).rows  # the next plot works


def test_describe_flock_lists_views_without_a_query(demo_flock: Path, config: QueryConfig) -> None:
    summary = describe_flock(
        FlockSelection(rel_paths=EVENTS_FILES[:2] + ["secure/encrypted.duckdb"]),
        {rel: str(demo_flock / rel) for rel in [*EVENTS_FILES[:2], "secure/encrypted.duckdb"]},
        config,
    )
    assert [v.qualified for v in summary.views] == ["main.config", "main.events"]
    assert [f.kind for f in summary.failures] == ["encrypted"]
    events = next(v for v in summary.views if v.table_name == "events")
    assert events.label_column == "database" and events.cast_names == set()
    assert list(summary.aliases.values()) == EVENTS_FILES[:2]


def test_the_flock_plot_leaves_no_temp_folder_behind(demo_flock: Path, config: QueryConfig) -> None:
    root = Path(config.resolved_temp_dir())
    flock_plot(demo_flock, F1, spec(x="ts"), config)
    assert not list(root.glob("flock-*"))
    with open_flock(FlockSelection(rel_paths=F1[:1]), {F1[0]: str(demo_flock / F1[0])}, config):
        assert len(list(root.glob("flock-*"))) == 1
