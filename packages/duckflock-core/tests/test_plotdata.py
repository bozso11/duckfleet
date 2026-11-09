"""Plot data (plan v0.3 Phase 1): strategies, reduction in DuckDB, budget, folding, validation."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from duckflock_core.plotdata import (
    BUCKET_LADDER_SECONDS,
    BUCKET_NAMES,
    OTHER,
    PlotSpec,
    choose_bucket_seconds,
    fetch_plot_data,
    plan_plot,
    series_slots,
)
from duckflock_core.query import QueryConfig, QueryError

ROWS = 200_000
TYPES = {
    "ts": "TIMESTAMP", "x": "DOUBLE", "y": "DOUBLE", "z": "DOUBLE", "g": "VARCHAR",
    "cat": "SMALLINT", "d": "DATE", "tz": "TIMESTAMP WITH TIME ZONE", "i": "BIGINT",
}  # fmt: skip


@pytest.fixture(scope="module")
def pdb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("plotdata") / "plot é.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute(
            f"""
            CREATE TABLE m AS SELECT
                TIMESTAMP '2026-01-01 00:00:00' + to_seconds(i * 15) AS ts,
                (i % 1000) / 10.0 AS x,
                sin(i / 5000.0) * 10 + 20 AS y,
                cos(i / 3000.0) AS z,
                'g' || (i % 3)::VARCHAR AS g,
                (i % 7)::SMALLINT AS cat,
                DATE '2026-01-01' + CAST(i // 1000 AS INTEGER) AS d,
                TIMESTAMPTZ '2026-01-01 00:00:00+00' + to_seconds(i * 15) AS tz,
                i AS i
            FROM range({ROWS}) r(i)
            """
        )
        # small table with NULLs, NaN, infinities and a constant column
        con.execute(
            """
            CREATE TABLE odd AS SELECT
                CASE WHEN i % 10 = 0 THEN NULL ELSE TIMESTAMP '2026-02-01' + to_hours(i) END AS ts,
                CASE WHEN i = 3 THEN 'nan'::DOUBLE WHEN i = 4 THEN 'inf'::DOUBLE
                     WHEN i % 7 = 0 THEN NULL ELSE i * 1.5 END AS v,
                5.0 AS const,
                CASE WHEN i % 4 = 0 THEN NULL ELSE 'c' || (i % 3)::VARCHAR END AS c,
                i AS n
            FROM range(300) r(i)
            """
        )
        con.execute("CREATE VIEW mv AS SELECT ts, y FROM m WHERE cat = 0")
        con.execute(
            "CREATE TABLE same_x AS SELECT TIMESTAMP '2026-03-01' AS ts, i AS v "
            "FROM range(500) r(i)"
        )
        con.execute(
            "CREATE TABLE few AS SELECT TIMESTAMP '2026-03-01' + to_minutes(i) AS ts, "
            "i::DOUBLE AS v FROM range(10) r(i)"
        )
    finally:
        con.close()
    return path


def spec(kind: str = "line", **fields) -> PlotSpec:
    base = {"schema_name": "main", "table_name": "m", "kind": kind}
    if kind != "bar":
        base["y"] = ["y"]
    return PlotSpec(**(base | fields))


def ask(path: Path, sql: str) -> list:
    """Rows of a direct query; the connection is closed before the next fetch (one config)."""
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def fetch(path: Path, plot: PlotSpec, **config):
    return fetch_plot_data(path, plot, QueryConfig(**config))


# ---------------------------------------------------------------- spec and ladder


def test_spec_defaults_and_ranges() -> None:
    plot = spec("line", x="ts")
    assert (plot.max_points, plot.bins, plot.agg) == (50_000, 50, "mean")
    assert (plot.max_categories, plot.max_series, plot.seed) == (200, None, 42)
    assert plot.budget_rows() == 50_000 and spec("line", y=["y", "z"]).budget_rows() == 25_000
    assert spec("scatter").resolved_max_series() == 3
    for kind in ("line", "histogram"):
        assert spec(kind).resolved_max_series() == 8
    assert spec("line", max_series=5).resolved_max_series() == 5
    for bad in ({"bins": 4}, {"bins": 501}, {"max_points": 99}, {"max_categories": 0}):
        with pytest.raises(ValidationError):
            spec("line", **bad)


def test_spec_structure_is_checked_before_any_sql() -> None:
    with pytest.raises(ValidationError, match="at least one y column"):
        PlotSpec(schema_name="main", table_name="m", kind="line")
    with pytest.raises(ValidationError, match="category column"):
        PlotSpec(schema_name="main", table_name="m", kind="bar")
    assert PlotSpec(schema_name="main", table_name="m", kind="bar", x="g").y == []
    assert spec("histogram", x="ts").x is None  # a histogram has no x
    assert spec("line", y=["y", "y", "z"]).y == ["y", "z"]
    assert spec("line", where="   ").where is None


def test_ladder_choice_for_a_span_and_a_budget() -> None:
    assert BUCKET_LADDER_SECONDS == [
        1, 5, 15, 30, 60, 300, 900, 1800, 3600, 10800, 21600, 43200, 86400, 604800, 2592000,
        7776000, 31536000,
    ]  # fmt: skip
    assert set(BUCKET_NAMES) == set(BUCKET_LADDER_SECONDS)
    day = 86_400
    assert choose_bucket_seconds(60, 1000, 1) == 1  # 61 buckets of 1 s
    assert choose_bucket_seconds(3 * day, 1000, 1) == 300  # 865 buckets of 5 minutes
    assert choose_bucket_seconds(3 * day, 100, 1) == 3600  # 73 buckets of 1 hour
    assert choose_bucket_seconds(34.7 * day, 1000, 1) == 3600  # 834 buckets
    assert choose_bucket_seconds(34.7 * day, 1000, 8) == 43200  # shared by 8 series: 71 each
    assert choose_bucket_seconds(10 * 365 * day, 100, 1) == 7776000  # 42 buckets of 90 days
    # above the ladder the width is derived, in whole days (W-02; more in test_plotdata_fixes.py)
    derived = choose_bucket_seconds(10_000 * 365 * day, 100, 1)
    assert derived > BUCKET_LADDER_SECONDS[-1] and derived % day == 0
    assert derived not in BUCKET_NAMES
    # the buckets (plus the one a span can straddle) never exceed the budget
    for span in (10, 700, 5000, 90_000, 3_000_000, 90_000_000, 9_000_000_000):
        for budget, series in ((100, 1), (500, 4), (2000, 9)):
            width = choose_bucket_seconds(span, budget, series)
            assert (math.ceil(span / width) + 1) * series <= budget


def test_series_slots() -> None:
    assert series_slots(0, 8, False) == 1
    assert series_slots(3, 8, False) == 3
    assert series_slots(3, 8, True) == 4  # plus (null)
    assert series_slots(12, 8, False) == 9  # plus (other)
    assert series_slots(12, 8, True) == 10


# ---------------------------------------------------------------- plan choices (no database)


def plan(kind: str, rows: int, **kwargs):
    fields = kwargs.pop("fields", {})
    return plan_plot(
        spec(kind, **fields),
        rows=rows,
        span_seconds=kwargs.pop("span", 86_400.0),
        n_series=kwargs.pop("n_series", 0),
        column_types=TYPES,
        **kwargs,
    )


def test_plan_line_with_a_temporal_x() -> None:
    under = plan("line", 50_000, fields={"x": "ts"})
    assert under.strategy == "raw" and under.x_type == "TIMESTAMP"
    over = plan("line", 50_001, fields={"x": "ts"})  # one day: 17,281 buckets of 5 s fit 50,000
    assert over.strategy == "time_bucket" and over.bucket_seconds == 5.0
    assert over.bucket_label == "5 seconds" and over.n_buckets == 17_281
    ten_days = {"span": 864_000.0}
    assert plan("line", 60_000, fields={"x": "ts"}, **ten_days).bucket_label == "30 seconds"
    # the budget is shared by the y columns
    shared = plan("line", 60_000, fields={"x": "ts", "y": ["y", "z"]}, **ten_days)
    assert shared.strategy == "time_bucket" and shared.budget_rows == 25_000
    assert shared.bucket_label == "1 minute"
    assert plan("line", 30_000, fields={"x": "ts", "max_points": 30_000}).strategy == "raw"


def test_plan_line_with_a_numeric_x_or_the_row_order() -> None:
    numeric = plan("line", 60_000, span=99.9, x_min=0.0, fields={"x": "x"})
    assert numeric.strategy == "numeric_bucket" and numeric.x_type == "DOUBLE"
    assert numeric.n_buckets == 50_000 and numeric.x_origin == 0.0
    assert numeric.bucket_width == pytest.approx(99.9 / 50_000)
    by_row = plan("line", 60_000, span=59_999.0, fields={})
    assert by_row.strategy == "numeric_bucket" and by_row.x_origin == 1.0
    assert plan("line", 40_000, fields={}).strategy == "raw"


def test_plan_line_buckets_are_shared_by_the_series() -> None:
    many = plan(
        "line", 60_000, n_series=20, has_null_series=True, x_min=0.0,
        fields={"x": "x", "color": "g"},
    )
    assert many.n_series == 10 and many.folded_series == 12  # 8 kept + (other) + (null)
    assert many.n_buckets == 5_000  # 50,000 rows over 10 series


def test_plan_line_with_one_x_value_falls_back_to_the_first_rows() -> None:
    same = plan("line", 60_000, span=0.0, fields={"x": "ts"})
    assert same.strategy == "raw" and same.row_limit == 50_000 and same.notes


def test_plan_scatter_samples_above_the_budget() -> None:
    assert plan("scatter", 50_000, fields={"x": "x"}).strategy == "raw"
    over = plan("scatter", 50_001, fields={"x": "x"})
    assert over.strategy == "sample" and over.sample_rows == 50_000
    assert over.folded_series == 0
    capped = plan("scatter", 10, n_series=5, fields={"x": "x", "color": "g"})
    assert capped.folded_series == 2 and capped.n_series == 4  # cap of 3, plus (other)


def test_plan_histogram_and_bar_ignore_the_budget() -> None:
    hist = plan("histogram", 10**9, y_ranges=[(10**9, 0.0, 10.0)])
    assert hist.strategy == "histogram" and len(hist.hist) == 1
    assert (hist.hist[0].bins, hist.hist[0].width, hist.hist[0].origin) == (50, 0.2, 0.0)
    constant = plan("histogram", 5, y_ranges=[(5, 7.0, 7.0)])
    assert (constant.hist[0].bins, constant.hist[0].origin, constant.hist[0].width) == (1, 6.5, 1.0)
    empty = plan("histogram", 0, y_ranges=[(0, None, None)])
    assert empty.hist == [] and "no finite values" in empty.notes[0]
    bar = plan("bar", 10**9, n_categories=500, fields={"x": "g"})
    assert bar.strategy == "bar" and bar.folded_categories == 300


# ---------------------------------------------------------------- fetching: line


def test_line_raw_returns_every_row_in_order(pdb: Path) -> None:
    data = fetch(pdb, spec("line", table_name="few", x="ts", y=["v"]))
    assert data.strategy == "raw" and data.bucket is None and not data.sampled
    assert data.columns == ["ts", "v"] and len(data.rows) == 10 and data.points == 10
    assert data.rows_scanned == 10 and data.x_type == "TIMESTAMP"
    assert [r[0] for r in data.rows] == sorted(r[0] for r in data.rows)
    assert isinstance(data.rows[0][0], datetime) and "ORDER BY 1" in data.sql


def test_line_time_bucket_reduces_inside_duckdb(pdb: Path) -> None:
    data = fetch(pdb, spec("line", x="ts", max_points=1000))
    assert data.strategy == "time_bucket" and data.bucket == "1 hour"
    assert data.columns == ["ts", "y_mean", "y_min", "y_max", "y_count"]
    assert data.rows_scanned == ROWS and data.points == len(data.rows) <= 1000
    assert sum(row[4] for row in data.rows) == ROWS  # the buckets cover every row
    assert all(row[2] <= row[1] <= row[3] for row in data.rows)
    assert [r[0] for r in data.rows] == sorted(r[0] for r in data.rows)
    assert "time_bucket(INTERVAL '1 hour'" in data.sql and "ROW_NUMBER" not in data.sql.upper()


def test_line_with_two_y_columns_shares_the_budget(pdb: Path) -> None:
    data = fetch(pdb, spec("line", x="ts", y=["y", "z"], max_points=1000))
    assert data.strategy == "time_bucket" and data.bucket == "3 hours"
    assert data.columns[:2] == ["ts", "y_mean"] and data.columns[-1] == "z_count"
    assert len(data.rows) <= 500 and data.points == len(data.rows) * 2 <= 1000


def test_line_numeric_x_buckets_cover_the_range(pdb: Path) -> None:
    data = fetch(pdb, spec("line", x="x", max_points=200))
    assert data.strategy == "numeric_bucket" and data.x_type == "DOUBLE"
    assert len(data.rows) <= 200 and sum(r[4] for r in data.rows) == ROWS
    xs = [r[0] for r in data.rows]
    assert xs == sorted(xs) and xs[0] == 0.0 and xs[-1] <= 99.9
    assert float(data.bucket) == pytest.approx(99.9 / 200, rel=1e-3)


def test_line_row_order_x(pdb: Path) -> None:
    raw = fetch(pdb, spec("line", table_name="few", y=["v"]))
    assert raw.strategy == "raw" and raw.columns == ["(row order)", "v"]
    assert [r[0] for r in raw.rows] == list(range(1, 11)) and raw.x_type == "BIGINT"
    big = fetch(pdb, spec("line", max_points=500))
    assert big.strategy == "numeric_bucket" and len(big.rows) <= 500
    assert big.columns[0] == "(row order)" and sum(r[4] for r in big.rows) == ROWS
    assert big.rows[0][0] == 1.0 and "row_number() OVER ()" in big.sql


def test_date_and_timestamptz_x_are_cast_to_naive_timestamps(pdb: Path) -> None:
    by_date = fetch(pdb, spec("line", x="d", max_points=100))
    assert by_date.strategy == "time_bucket" and by_date.x_type == "TIMESTAMP"
    assert 'TRY_CAST("d" AS TIMESTAMP)' in by_date.sql and len(by_date.rows) <= 100
    assert sum(r[4] for r in by_date.rows) == ROWS
    by_tz = fetch(pdb, spec("line", x="tz", max_points=1000))
    assert by_tz.strategy == "time_bucket" and by_tz.bucket == "1 hour"
    assert "AT TIME ZONE 'UTC'" in by_tz.sql
    first = by_tz.rows[0][0]
    assert first.tzinfo is None and first == datetime(2026, 1, 1, 0, 0)  # naive UTC
    plain = fetch(pdb, spec("line", x="ts", max_points=1000))
    assert [r[0] for r in plain.rows] == [r[0] for r in by_tz.rows]  # same buckets as ts


def test_one_x_value_shows_the_first_rows_only(pdb: Path) -> None:
    data = fetch(pdb, spec("line", table_name="same_x", x="ts", y=["v"], max_points=100))
    assert data.strategy == "raw" and len(data.rows) == 100
    assert any("every x value is the same" in note for note in data.notes)


def test_the_filter_is_applied_everywhere(pdb: Path) -> None:
    data = fetch(pdb, spec("line", x="ts", max_points=1000, where="cat = 3 AND y > 20"))
    expected = ask(pdb, "select count(*) from m where cat = 3 and y > 20")[0][0]
    assert data.rows_scanned == expected and sum(r[4] for r in data.rows) == expected
    assert "cat = 3 AND y > 20" in data.sql
    row_order = fetch(pdb, spec("line", max_points=1000, where="cat = 3 AND y > 20"))
    assert row_order.rows_scanned == expected and sum(r[4] for r in row_order.rows) == expected
    zero = fetch(pdb, spec("line", x="ts", where="cat = 99"))
    assert zero.rows == [] and zero.rows_scanned == 0 and zero.points == 0


def test_a_filter_cannot_comment_out_the_rest_of_the_query(pdb: Path) -> None:
    data = fetch(pdb, spec("line", table_name="few", x="ts", y=["v"], where="v < 3 -- trailing"))
    assert len(data.rows) == 3
    with pytest.raises(QueryError) as caught:
        fetch(pdb, spec("line", table_name="few", x="ts", y=["v"], where="v < 3); select 1; --"))
    assert caught.value.kind == "sql"


def test_a_view_can_be_plotted(pdb: Path) -> None:
    data = fetch(pdb, spec("line", table_name="mv", x="ts", max_points=500))
    assert data.rows_scanned == ROWS // 7 + 1 and data.points <= 500


# ---------------------------------------------------------------- fetching: series


def test_series_by_colour_are_ordered_by_row_count(pdb: Path) -> None:
    data = fetch(pdb, spec("line", x="ts", color="g", max_points=1000))
    assert data.strategy == "time_bucket" and data.folded_series == 0
    assert data.series == ["g0", "g1", "g2"]  # equal counts: by name
    assert data.columns[:2] == ["ts", "g"] and len(data.rows) <= 1000 // 1
    assert {r[1] for r in data.rows} == {"g0", "g1", "g2"}
    assert sum(r[5] for r in data.rows) == ROWS


def test_series_beyond_the_cap_are_folded_into_other(pdb: Path) -> None:
    data = fetch(pdb, spec("line", x="ts", color="cat", max_series=2, max_points=1000))
    assert data.folded_series == 5 and len(data.series) == 3
    assert data.series[-1] == OTHER and data.series[0] in {"0", "1", "2", "3", "4", "5", "6"}
    assert {r[1] for r in data.rows} == set(data.series)
    assert len(data.rows) <= 1000 and sum(r[5] for r in data.rows) == ROWS
    assert "df_top" in data.sql  # values come from the CTE, not from the SQL text
    assert "'0'" not in data.sql and "'1'" not in data.sql


def test_the_top_series_are_the_most_frequent(pdb: Path, tmp_path: Path) -> None:
    path = tmp_path / "freq.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE f AS SELECT TIMESTAMP '2026-01-01' + to_minutes(i) AS ts, i AS v, "
        "CASE WHEN i % 10 < 5 THEN 'common' WHEN i % 10 < 8 THEN 'mid' WHEN i % 10 = 8 THEN 'rare' "
        "ELSE 'rarest' END AS c FROM range(2000) r(i)"
    )
    con.close()
    data = fetch(
        path,
        PlotSpec(
            schema_name="main", table_name="f", kind="line", x="ts", y=["v"], color="c",
            max_series=2, max_points=300,
        ),
    )  # fmt: skip
    assert data.series == ["common", "mid", OTHER]


def test_null_colours_form_their_own_series(pdb: Path) -> None:
    data = fetch(
        pdb, spec("line", table_name="odd", x="ts", y=["n"], color="c", max_points=1000)
    )
    assert data.strategy == "raw" and "(null)" in data.series and data.series[-1] == "(null)"
    assert {r[1] for r in data.rows} == set(data.series)


def test_a_non_text_colour_column_works(pdb: Path) -> None:
    data = fetch(pdb, spec("scatter", x="x", color="cat", max_points=500))
    assert data.sampled and data.folded_series == 4 and len(data.series) == 4
    assert data.series[-1] == OTHER


# ---------------------------------------------------------------- fetching: scatter


def test_scatter_raw_below_the_budget_and_sampled_above(pdb: Path) -> None:
    small = fetch(pdb, spec("scatter", table_name="few", x="ts", y=["v"]))
    assert small.strategy == "raw" and not small.sampled and len(small.rows) == 10
    data = fetch(pdb, spec("scatter", x="x", max_points=1000))
    assert data.strategy == "sample" and data.sampled and len(data.rows) == 1000
    assert data.columns == ["x", "y"] and data.points == 1000 and data.rows_scanned == ROWS
    assert "reservoir(1000 ROWS) REPEATABLE (42)" in data.sql


def test_scatter_sample_is_repeatable_and_follows_the_seed(pdb: Path) -> None:
    first = fetch(pdb, spec("scatter", x="x", max_points=500))
    again = fetch(pdb, spec("scatter", x="x", max_points=500))
    other = fetch(pdb, spec("scatter", x="x", max_points=500, seed=7))
    assert first.rows == again.rows and first.rows != other.rows


def test_scatter_sample_is_taken_after_the_filter(pdb: Path) -> None:
    data = fetch(pdb, spec("scatter", x="x", y=["y"], max_points=300, where="cat = 3"))
    assert len(data.rows) == 300  # not a sample of the table that is then filtered
    assert data.rows_scanned == ask(pdb, "select count(*) from m where cat = 3")[0][0]


# ---------------------------------------------------------------- fetching: histogram


def test_histogram_bins_cover_every_row(pdb: Path) -> None:
    data = fetch(pdb, spec("histogram", bins=40))
    assert data.strategy == "histogram"
    assert data.columns == ["y_column", "bin_start", "bin_end", "count"]
    assert 30 <= len(data.rows) <= 40 and data.points == len(data.rows)
    assert sum(r[3] for r in data.rows) == data.rows_scanned == ROWS
    assert {r[0] for r in data.rows} == {"y"}
    starts = [r[1] for r in data.rows]
    assert starts == sorted(starts)
    width = data.rows[0][2] - data.rows[0][1]
    assert all(r[2] - r[1] == pytest.approx(width) for r in data.rows)
    ((low, high),) = ask(pdb, "select min(y), max(y) from m")
    assert data.rows[0][1] == pytest.approx(low, abs=width) and data.rows[-1][2] >= high - 1e-9


def test_histogram_of_several_columns_with_series(pdb: Path) -> None:
    data = fetch(pdb, spec("histogram", y=["y", "z"], color="g", bins=10))
    assert data.columns == ["y_column", "bin_start", "bin_end", "g", "count"]
    assert {r[0] for r in data.rows} == {"y", "z"} and data.series == ["g0", "g1", "g2"]
    for column in ("y", "z"):
        assert sum(r[4] for r in data.rows if r[0] == column) == ROWS
    assert len(data.rows) <= 2 * 3 * 10


def test_histogram_leaves_out_nulls_and_non_finite_values_and_says_so(pdb: Path) -> None:
    data = fetch(pdb, spec("histogram", table_name="odd", y=["v"], bins=5))
    finite = ask(pdb, "select count(*) from odd where isfinite(v)")[0][0]
    assert sum(r[3] for r in data.rows) == finite and data.rows_scanned == 300
    assert any("rows without a finite value" in note for note in data.notes)


def test_histogram_of_a_constant_column_is_one_bin(pdb: Path) -> None:
    data = fetch(pdb, spec("histogram", table_name="odd", y=["const"]))
    assert len(data.rows) == 1
    assert data.rows[0][1:] == (4.5, 5.5, 300)


# ---------------------------------------------------------------- fetching: bar


def test_bar_counts_per_category_in_descending_order(pdb: Path) -> None:
    data = fetch(pdb, spec("bar", x="cat"))
    assert data.strategy == "bar" and data.columns == ["cat", "count"]
    assert sum(r[1] for r in data.rows) == ROWS and len(data.rows) == 7
    counts = [r[1] for r in data.rows]
    assert counts == sorted(counts, reverse=True)
    assert data.rows[0][0] == "0" and data.folded_categories == 0  # 0 has one more row


def test_bar_measures_and_extra_columns(pdb: Path) -> None:
    data = fetch(pdb, spec("bar", x="g", y=["y", "z"], agg="mean"))
    assert data.columns == ["g", "mean(y)", "mean(z)"] and len(data.rows) == 3
    expected = ask(pdb, "select g, avg(y), avg(z) from m group by g")
    got = {r[0]: r[1:] for r in data.rows}
    for name, mean_y, mean_z in expected:
        assert got[name] == pytest.approx((mean_y, mean_z))
    assert [r[1] for r in data.rows] == sorted((r[1] for r in data.rows), reverse=True)
    summed = fetch(pdb, spec("bar", x="g", y=["i"], agg="sum"))
    assert sum(r[1] for r in summed.rows) == sum(range(ROWS))
    counted = fetch(pdb, spec("bar", x="g", y=["y"], agg="count"))
    assert sorted(r[1] for r in counted.rows) == [66_666, 66_667, 66_667]


def test_bar_folds_the_tail_into_other(pdb: Path) -> None:
    data = fetch(pdb, spec("bar", x="cat", max_categories=3))
    assert len(data.rows) == 4 and data.rows[-1][0] == OTHER
    assert data.folded_categories == 4 and sum(r[1] for r in data.rows) == ROWS
    top = ask(pdb, "select cat::varchar, count(*) from m group by 1 order by 2 desc, 1 limit 3")
    assert [tuple(r) for r in data.rows[:3]] == [tuple(r) for r in top]
    pooled = fetch(pdb, spec("bar", x="cat", y=["i"], agg="max", max_categories=3))
    assert pooled.rows[-1][0] == OTHER and pooled.rows[-1][1] <= ROWS - 1


def test_bar_with_series_and_null_categories(pdb: Path) -> None:
    data = fetch(pdb, spec("bar", table_name="odd", x="c", color="c"))
    assert "(null)" in {r[0] for r in data.rows}
    by_series = fetch(pdb, spec("bar", x="cat", color="g"))
    assert by_series.columns == ["cat", "g", "count"] and by_series.series == ["g0", "g1", "g2"]
    assert sum(r[2] for r in by_series.rows) == ROWS and len(by_series.rows) == 21


# ---------------------------------------------------------------- rows that cannot be placed


def test_rows_without_a_usable_x_are_left_out_and_counted(pdb: Path) -> None:
    data = fetch(pdb, spec("line", table_name="odd", x="ts", y=["n"], max_points=1000))
    assert data.strategy == "raw"
    assert data.rows_scanned == 300 and len(data.rows) == 270
    assert any("30 rows without a usable x value" in note for note in data.notes)
    floats = fetch(pdb, spec("line", table_name="odd", x="v", y=["n"], max_points=1000))
    assert all(math.isfinite(r[0]) for r in floats.rows)  # NaN, inf and NULL are not placed


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"x": "nope"}, "unknown column(s) in main.m: nope"),
        ({"y": ["y", "ghost"]}, "ghost"),
        ({"color": "ghost"}, "ghost"),
        ({"y": ["g"]}, "y columns must be numeric: g (VARCHAR)"),
        ({"x": "g"}, "x must be a date/time or numeric column for a line chart, g is VARCHAR"),
        ({"table_name": "missing"}, "unknown table or view main.missing"),
        ({"where": "no_such_column > 1"}, "no_such_column"),
        ({"where": "y >"}, "syntax error"),
    ],
)
def test_bad_specs_are_sql_errors(pdb: Path, fields: dict, message: str) -> None:
    with pytest.raises(QueryError) as caught:
        fetch(pdb, spec("line", **({"x": "ts"} | fields)))
    assert caught.value.kind == "sql" and message.lower() in caught.value.message.lower()


def test_bar_needs_numeric_y_unless_it_counts(pdb: Path) -> None:
    with pytest.raises(QueryError, match="y columns must be numeric"):
        fetch(pdb, spec("bar", x="cat", y=["g"], agg="mean"))
    assert fetch(pdb, spec("bar", x="cat", y=["g"], agg="count")).rows


def test_a_write_in_the_filter_is_refused(pdb: Path) -> None:
    with pytest.raises(QueryError) as caught:
        fetch(pdb, spec("line", x="ts", where="(select count(*) from read_csv('x.csv')) > 0"))
    assert caught.value.kind == "blocked"


def test_a_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(QueryError) as caught:
        fetch(tmp_path / "gone.duckdb", spec("line", x="ts"))
    assert caught.value.kind == "other"


# ---------------------------------------------------------------- timeout and the budget


def test_the_timeout_is_honoured(pdb: Path) -> None:
    slow = "(select sum(a.i * b.i) from range(100000) a(i), range(100000) b(i)) > 0"
    with pytest.raises(QueryError) as caught:
        fetch(pdb, spec("line", x="ts", where=slow), timeout_s=0.5)
    assert caught.value.kind == "timeout"
    assert fetch(pdb, spec("line", x="ts", max_points=1000)).rows  # the file stays usable


@pytest.mark.parametrize("kind", ["line", "scatter"])
@pytest.mark.parametrize("x", ["ts", "x", "d", "tz", None])
@pytest.mark.parametrize("color", [None, "g", "cat"])
@pytest.mark.parametrize("max_points", [100, 777, 5000])
def test_the_final_query_never_returns_more_than_the_budget(
    pdb: Path, kind: str, x: str | None, color: str | None, max_points: int
) -> None:
    plot = spec(kind, x=x, color=color, y=["y", "z"], max_points=max_points, max_series=4)
    data = fetch(pdb, plot)
    assert len(data.rows) * 2 <= max_points
    assert data.points <= max_points


def test_series_and_buckets_together_stay_within_the_budget(pdb: Path) -> None:
    data = fetch(pdb, spec("line", x="ts", color="cat", max_series=8, max_points=100))
    assert len(data.rows) <= 100 and len(data.series) == 7 and data.folded_series == 0
    folded = fetch(pdb, spec("line", x="ts", color="cat", max_series=3, max_points=100))
    assert len(folded.rows) <= 100 and folded.series[-1] == OTHER
