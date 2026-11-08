"""Plot data fixes of the v0.3 review (0.3.1): W-02, W-03, W-05, W-06, W-08, W-10, W-12, P-11."""

from __future__ import annotations

import dataclasses
import math
from datetime import datetime
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from duckfleet_core import plotdata, query
from duckfleet_core.plotdata import (
    BUCKET_LADDER_SECONDS,
    PlotPlan,
    PlotSpec,
    bucket_interval,
    choose_bucket_seconds,
    fetch_plot_data,
    plan_plot,
)
from duckfleet_core.query import QueryConfig, QueryError
from duckfleet_core.sqlutil import quote_ident

DAY = 86_400
HOSTILE = {
    "x": "DOUBLE", "s": "VARCHAR", "n": "BIGINT", "df_c": "VARCHAR", "df_rk": "DOUBLE",
    "df_m": "DOUBLE", "cat": "VARCHAR", "rk": "BIGINT", "v0": "DOUBLE", "yi": "BIGINT",
    "b": "BIGINT", 'q"uote': "DOUBLE", "a b": "DOUBLE", "é": "DOUBLE", "ts": "TIMESTAMP",
    "g": "VARCHAR",
}  # fmt: skip


@pytest.fixture(scope="module")
def xdb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("plotfix") / "fix.duckdb"
    con = duckdb.connect(str(path))
    try:
        # 200 yearly rows over 200 years (W-02)
        con.execute(
            "CREATE TABLE yearly AS SELECT TIMESTAMP '1850-01-01' + to_days(i * 365) AS ts, "
            "i::DOUBLE AS v FROM range(200) r(i)"
        )
        columns = ", ".join(f"{quote_ident(name)} {kind}" for name, kind in HOSTILE.items())
        con.execute(f"CREATE TABLE hostile ({columns})")
        con.execute(
            """
            INSERT INTO hostile SELECT
                i / 10.0, 'S' || (i % 4)::VARCHAR, i, 'c' || (i % 5)::VARCHAR, i * 2.0, i * 3.0,
                'k' || (i % 3)::VARCHAR, i % 9, i * 0.5, i % 6, i % 7, i + 0.25, i - 0.5, i * 1.5,
                TIMESTAMP '2026-01-01' + to_minutes(i), 'g' || (i % 2)::VARCHAR
            FROM range(600) r(i)
            """
        )
        # timestamp precisions (Section 6 item 3 of the review)
        con.execute(
            """
            CREATE TABLE precisions AS SELECT
                (TIMESTAMP '2026-03-01' + to_minutes(i))::TIMESTAMP_S AS t_s,
                (TIMESTAMP '2026-03-01' + to_minutes(i))::TIMESTAMP_MS AS t_ms,
                (TIMESTAMP '2026-03-01' + to_minutes(i))::TIMESTAMP_NS AS t_ns,
                i::DOUBLE AS v
            FROM range(400) r(i)
            """
        )
        con.execute("CREATE TABLE empty_t (ts TIMESTAMP, v DOUBLE, c VARCHAR)")
        con.execute("CREATE TABLE single (ts TIMESTAMP, v DOUBLE, c VARCHAR)")
        con.execute("INSERT INTO single VALUES (TIMESTAMP '2026-04-01 12:00:00', 2.5, 'a')")
        # a DATE beyond the TIMESTAMP range (W-12)
        con.execute(
            "CREATE TABLE far AS SELECT * FROM (VALUES (DATE '2026-01-01', 1.0), "
            "(DATE '2026-01-02', 2.0), (DATE '1000000-01-01', 3.0)) t(d, v)"
        )
        # a range that overflows a double (W-08)
        con.execute(
            "CREATE TABLE wide AS SELECT * FROM (VALUES (-1.7e308, -1.7e308), (1.7e308, 1.7e308), "
            "(0.0, 5.0), (1.0, 6.0)) t(x, v)"
        )
    finally:
        con.close()
    return path


def spec(table: str, kind: str = "line", **fields) -> PlotSpec:
    base: dict = {"schema_name": "main", "table_name": table, "kind": kind}
    if kind != "bar":
        base["y"] = ["v"]
    return PlotSpec(**(base | fields))


def fetch(path: Path, plot: PlotSpec, **config):
    return fetch_plot_data(path, plot, QueryConfig(**config))


# ---------------------------------------------------------------- W-02: above the ladder


def test_above_the_ladder_the_width_is_derived_in_whole_days() -> None:
    span = 10_000 * 365 * DAY
    width = choose_bucket_seconds(span, 100, 1)
    assert width % DAY == 0 and width > BUCKET_LADDER_SECONDS[-1]
    assert (math.ceil(span / width) + 1) <= 100
    # a shared budget derives a wider bucket
    shared = choose_bucket_seconds(span, 100, 4)
    assert shared > width and (math.ceil(span / shared) + 1) * 4 <= 100
    assert bucket_interval(width) == f"{width // DAY} days"
    assert bucket_interval(3600) == "1 hour" and bucket_interval(DAY) == "1 day"
    # the budget is never exceeded above the ladder, whatever the span
    for years in (400, 1_000, 50_000):
        for budget, series in ((100, 1), (1000, 5), (333, 3)):
            span = years * 365 * DAY
            w = choose_bucket_seconds(span, budget, series)
            assert (math.ceil(span / w) + 1) * series <= budget


def test_a_yearly_series_over_two_centuries_fits_a_budget_of_100(xdb: Path) -> None:
    data = fetch(xdb, spec("yearly", x="ts", max_points=100))
    assert data.strategy == "time_bucket" and len(data.rows) <= 100  # no guard error
    assert data.bucket is not None and data.bucket.endswith(" days")
    assert int(data.bucket.split()[0]) > 365 and "INTERVAL '" + data.bucket + "'" in data.sql
    assert sum(row[4] for row in data.rows) == 200  # every row is in a bucket


def test_above_the_ladder_with_series_stays_within_the_budget(xdb: Path) -> None:
    data = fetch(xdb, spec("yearly", x="ts", max_points=100, color="v", max_series=3))
    assert len(data.rows) <= 100 and data.strategy == "time_bucket"


def test_a_degenerate_budget_is_exceeded_only_by_the_straddling_bucket(xdb: Path) -> None:
    """Fewer than two rows per series slot: the plan's own bucket bound is the cap."""
    plot = spec("hostile", x="ts", y=["x", "n", "df_rk", "df_m", "v0", "q\"uote", "a b", "é"],
                color="cat", max_points=100, max_series=8)
    data = fetch(xdb, plot)
    assert data.strategy in ("time_bucket", "raw")


# ---------------------------------------------------------------- W-03 and W-06: the spec


def test_at_most_eight_y_columns() -> None:
    names = [f"c{i}" for i in range(8)]
    assert PlotSpec(schema_name="main", table_name="t", kind="line", y=names).y == names
    with pytest.raises(ValidationError, match="at most 8 y columns can be plotted at once"):
        PlotSpec(schema_name="main", table_name="t", kind="line", y=[*names, "c8"])
    # duplicates do not count twice
    assert len(PlotSpec(schema_name="main", table_name="t", kind="line", y=[*names, *names]).y) == 8


@pytest.mark.parametrize("seed", [0, 1, 2**31 - 1])
def test_valid_seeds_run(xdb: Path, seed: int) -> None:
    data = fetch(xdb, spec("hostile", "scatter", x="x", y=["n"], max_points=100, seed=seed))
    assert data.sampled and len(data.rows) == 100


@pytest.mark.parametrize("seed", [-5, -1, 2**31, 2**40])
def test_a_seed_outside_the_reservoir_range_is_a_spec_error(seed: int) -> None:
    with pytest.raises(ValidationError):
        PlotSpec(schema_name="main", table_name="t", kind="scatter", y=["v"], seed=seed)


# ---------------------------------------------------------------- W-05: hostile column names


@pytest.mark.parametrize(
    ("x", "y", "color"),
    [
        ("df_c", ["df_rk"], None),
        ("df_rk", ["df_rk", "df_m"], "df_c"),
        ("df_c", [], "df_rk"),
        ("cat", ["v0"], "rk"),
        ("s", ["n"], "s"),
        ("g", ["q\"uote", "a b", "é"], "yi"),
        ("b", ["x"], "n"),
    ],
)
@pytest.mark.parametrize("agg", ["mean", "count", "sum"])
def test_bar_works_with_column_names_that_collide_with_the_helpers(
    xdb: Path, x: str, y: list[str], color: str | None, agg: str
) -> None:
    data = fetch(xdb, spec("hostile", "bar", x=x, y=y, color=color, agg=agg, max_categories=2))
    assert data.strategy == "bar" and data.rows
    assert data.folded_categories >= 0 and len(data.rows) <= 3 * (len(data.series) or 1)
    if agg == "count" or not y:
        total = 600
        counts = [r[-1 if not y else 1] for r in data.rows] if not y else None
        if counts is not None and color is None:
            assert sum(counts) == total  # the categories, the folded tail included, cover every row


def test_bar_counts_cover_every_row_with_a_column_named_df_c(xdb: Path) -> None:
    data = fetch(xdb, spec("hostile", "bar", x="df_c", max_categories=2))
    assert sum(r[1] for r in data.rows) == 600 and data.rows[-1][0] == "(other)"
    assert data.columns == ["df_c", "count"] and data.folded_categories == 3


@pytest.mark.parametrize("kind", ["line", "scatter", "histogram"])
def test_other_kinds_accept_the_hostile_names(xdb: Path, kind: str) -> None:
    for color in (None, "s", "df_c", "cat"):
        plot = spec(
            "hostile", kind, x=None if kind == "histogram" else "x", y=["n", "df_rk"],
            color=color, max_points=200,
        )  # fmt: skip
        data = fetch(xdb, plot)
        assert data.rows and data.rows_scanned == 600


# ---------------------------------------------------------------- W-08: non-finite ranges


def test_a_numeric_range_beyond_a_double_falls_back_to_the_first_rows(xdb: Path) -> None:
    data = fetch(xdb, spec("wide", x="x", max_points=100))
    assert data.strategy == "raw" and len(data.rows) == 4  # fewer rows than the budget: raw
    squeezed = plan_plot(
        spec("wide", x="x"), rows=10**6, span_seconds=math.inf, n_series=0,
        column_types={"x": "DOUBLE", "v": "DOUBLE"}, x_min=-1.7e308,
    )  # fmt: skip
    assert squeezed.strategy == "raw" and squeezed.row_limit == 50_000
    assert "too wide to bucket" in squeezed.notes[0]
    assert plan_plot(
        spec("wide", x="x"), rows=10**6, span_seconds=math.nan, n_series=0,
        column_types={"x": "DOUBLE"},
    ).notes  # fmt: skip


def test_a_wide_range_in_a_big_table_does_not_raise(tmp_path: Path) -> None:
    path = tmp_path / "w.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE w AS SELECT CASE WHEN i % 2 = 0 THEN -1.7e308 ELSE 1.7e308 END AS x, "
        "i::DOUBLE AS v FROM range(1000) r(i)"
    )
    con.close()
    data = fetch(path, spec("w", x="x", max_points=100))
    assert data.strategy == "raw" and len(data.rows) == 100
    assert any("too wide to bucket" in note for note in data.notes)
    hist = fetch(path, spec("w", "histogram", y=["x"]))
    assert hist.rows == [] and any("too wide to bin" in note for note in hist.notes)
    scatter = fetch(path, spec("w", "scatter", x="x", max_points=100))
    assert scatter.sampled and len(scatter.rows) == 100


def test_a_number_that_cannot_enter_the_sql_is_a_query_error(
    xdb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args, **_kwargs):
        raise ValueError("non-finite number in a plot query")

    monkeypatch.setattr(plotdata, "build_sql", boom)
    with pytest.raises(QueryError) as caught:
        fetch(xdb, spec("hostile", x="x", y=["n"]))
    assert caught.value.kind == "other" and "non-finite" in caught.value.message


# ---------------------------------------------------------------- W-10: lookups are timed


def test_the_name_and_column_lookups_run_through_execute_limited(
    xdb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real = query.execute_limited

    def spy(con, sql, max_rows, timeout_s, **kwargs):
        seen.append(sql.split()[0:4].__str__())
        assert timeout_s > 0
        return real(con, sql, max_rows, timeout_s, **kwargs)

    monkeypatch.setattr(query, "execute_limited", spy)
    monkeypatch.setattr(plotdata, "execute_limited", spy)
    data = fetch(xdb, spec("hostile", x="x", y=["n"], max_points=1000))
    assert data.rows
    assert len(seen) == 4, seen  # database name, columns, counts, data
    assert "duckdb_databases" in "".join(seen) or "database_name" in "".join(seen)


# ---------------------------------------------------------------- W-12 and the Section 6 gaps


def test_a_date_beyond_the_timestamp_range_is_left_out_and_counted(xdb: Path) -> None:
    for max_points in (100, 1000):
        data = fetch(xdb, spec("far", x="d", max_points=max_points))
        assert data.rows_scanned == 3 and len(data.rows) == 2
        assert any("1 rows without a usable x value" in n for n in data.notes)
        assert [r[0] for r in data.rows] == [datetime(2026, 1, 1), datetime(2026, 1, 2)]


@pytest.mark.parametrize("column", ["t_s", "t_ms", "t_ns"])
@pytest.mark.parametrize("max_points", [100, 100_000])
def test_timestamp_precisions_work_as_x_through_every_strategy(
    xdb: Path, column: str, max_points: int
) -> None:
    data = fetch(xdb, spec("precisions", x=column, max_points=max_points))
    assert data.x_type == "TIMESTAMP" and data.rows_scanned == 400
    if max_points == 100:
        assert data.strategy == "time_bucket" and sum(r[4] for r in data.rows) == 400
    else:
        assert data.strategy == "raw" and len(data.rows) == 400
        assert data.rows[0][0] == datetime(2026, 3, 1)
        assert data.rows[-1][0] == datetime(2026, 3, 1, 6, 39)
    scatter = fetch(xdb, spec("precisions", "scatter", x=column, max_points=100))
    assert scatter.sampled and len(scatter.rows) == 100


@pytest.mark.parametrize("kind", ["line", "scatter", "histogram", "bar"])
def test_an_empty_table_plots_nothing_without_an_error(xdb: Path, kind: str) -> None:
    x = {"line": "ts", "scatter": "ts", "histogram": None, "bar": "c"}[kind]
    data = fetch(xdb, spec("empty_t", kind, x=x, color="c" if kind != "bar" else None))
    assert data.rows == [] and data.rows_scanned == 0


@pytest.mark.parametrize("kind", ["line", "scatter", "histogram", "bar"])
def test_a_one_row_table_plots_one_row(xdb: Path, kind: str) -> None:
    x = {"line": "ts", "scatter": "ts", "histogram": None, "bar": "c"}[kind]
    data = fetch(xdb, spec("single", kind, x=x))
    assert len(data.rows) == 1 and data.rows_scanned == 1
    if kind == "histogram":
        assert data.rows[0][1:] == (2.0, 3.0, 1) or data.rows[0][-1] == 1


# ---------------------------------------------------------------- P-11


def test_the_plan_no_longer_carries_series_kept() -> None:
    assert "series_kept" not in {f.name for f in dataclasses.fields(PlotPlan)}
    assert "series_top" not in plan_plot.__code__.co_varnames


def test_the_budget_guard_also_covers_histogram_and_bar() -> None:
    plot = PlotSpec(schema_name="main", table_name="t", kind="histogram", y=["a", "b"], bins=10)
    plan = PlotPlan(strategy="histogram", n_series=3)
    assert plotdata._fetch_cap(plot, plan) == 10 * 2 * 3
    bar = PlotSpec(schema_name="main", table_name="t", kind="bar", x="c", max_categories=5)
    assert plotdata._fetch_cap(bar, PlotPlan(strategy="bar", n_series=2)) == 6 * 2
