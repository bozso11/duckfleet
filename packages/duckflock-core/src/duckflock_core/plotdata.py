"""Plot data: a ``PlotSpec`` becomes one bounded DuckDB query (plan v0.3 Section 3, decision D-033).

The browser never receives more than the point budget, so every reduction happens in
DuckDB: ``time_bucket`` with mean, min and max for a temporal x, equal-width buckets for
a numeric x, equal-width bins for histograms, a reservoir sample for scatter charts and
top-N folding for series and bar categories.

``fetch_plot_data`` runs at most four queries on one sandboxed connection (the database
name and the column lookup, one statement with the counts the plan needs, and the data
query), all inside one timeout (review W-10). The data query is checked against the most
rows its plan can legitimately return: the point budget for line and scatter charts, and
the bin and category bounds for histograms and bars.

Identifiers are quoted; the filter is inserted as ``WHERE (...)`` text and is
the only free SQL; every other value in the SQL text is a number computed here. Data
values never enter the SQL text: series folding uses a CTE of the top values.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, Field, field_validator, model_validator

from duckflock_core.flock import MEMORY, AttachFailure, FlockSelection, open_flock
from duckflock_core.profiler import is_numeric_type, is_temporal_type
from duckflock_core.query import (
    QueryConfig,
    QueryError,
    _table_columns,
    as_query_errors,
    database_name,
    execute_limited,
    open_readonly,
)
from duckflock_core.sqlutil import qualified_name, quote_ident

Kind = Literal["line", "scatter", "histogram", "bar"]
Agg = Literal["mean", "sum", "min", "max", "count"]
Strategy = Literal["raw", "time_bucket", "numeric_bucket", "sample", "histogram", "bar"]

# TUNING(bucket-ladder): the bucket widths time_bucket may use, smallest first; the first one
# that keeps the buckets within the point budget is chosen.
BUCKET_LADDER_SECONDS = [
    1, 5, 15, 30, 60, 300, 900, 1800, 3600, 10800, 21600, 43200, 86400, 604800, 2592000,
    7776000, 31536000,
]  # fmt: skip
BUCKET_NAMES = {
    1: "1 second", 5: "5 seconds", 15: "15 seconds", 30: "30 seconds", 60: "1 minute",
    300: "5 minutes", 900: "15 minutes", 1800: "30 minutes", 3600: "1 hour", 10800: "3 hours",
    21600: "6 hours", 43200: "12 hours", 86400: "1 day", 604800: "7 days", 2592000: "30 days",
    7776000: "90 days", 31536000: "365 days",
}  # fmt: skip

MAX_Y_COLUMNS = 8  # W-03: one palette slot per y column (D-032)
_DAY_SECONDS = 86_400
OTHER = "(other)"
NULL_SERIES = "(null)"
ROW_ORDER = "(row order)"
_RN = "__df_rn"  # the row number column of the row-order subquery
_SERIES_CTE = "df_top"
_RANK_CTE = "df_rank"
_RANK_CATEGORY = "df_c"
_RANK_NUMBER = "df_rk"
_MAX_SERIES_SCATTER = 3  # TUNING(series-cap)
_MAX_SERIES_OTHER = 8  # TUNING(series-cap)


# ---------------------------------------------------------------- the spec


class PlotSpec(BaseModel):
    """What to plot (plan Section 3.1). Structural problems raise ``ValueError`` at creation."""

    schema_name: str
    table_name: str
    kind: Kind
    x: str | None = None
    y: list[str] = Field(default_factory=list)
    color: str | None = None
    where: str | None = None
    max_points: int = Field(default=50_000, ge=100)  # L-11, L-21
    bins: int = Field(default=50, ge=5, le=500)  # L-20
    agg: Agg = "mean"
    max_categories: int = Field(default=200, ge=1)  # L-18
    max_series: int | None = Field(default=None, ge=1)  # L-19
    seed: int = Field(default=42, ge=0, le=2**31 - 1)  # REPEATABLE takes a non-negative 31-bit seed

    @field_validator("y")
    @classmethod
    def _unique_y(cls, value: list[str]) -> list[str]:
        names = list(dict.fromkeys(value))
        if len(names) > MAX_Y_COLUMNS:
            raise ValueError(
                f"at most {MAX_Y_COLUMNS} y columns can be plotted at once (got {len(names)})"
            )
        return names

    @field_validator("where")
    @classmethod
    def _blank_filter(cls, value: str | None) -> str | None:
        return value if value is not None and value.strip() else None

    @model_validator(mode="after")
    def _structure(self) -> PlotSpec:
        if self.kind in ("line", "scatter", "histogram") and not self.y:
            raise ValueError(f"choose at least one y column for a {self.kind} chart")
        if self.kind == "bar" and not self.x:
            raise ValueError("a bar chart needs a category column (x)")
        if self.kind == "histogram":
            self.x = None  # the x axis of a histogram is the value itself
        return self

    def resolved_max_series(self) -> int:
        """``max_series``, or 3 for scatter and 8 for the other kinds (D-032, L-19)."""
        # TUNING(series-cap)
        if self.max_series is not None:
            return self.max_series
        return _MAX_SERIES_SCATTER if self.kind == "scatter" else _MAX_SERIES_OTHER

    def budget_rows(self) -> int:
        """Rows (per series) the point budget allows: ``max_points`` shared by the y columns."""
        return self.max_points // max(1, len(self.y))


# ---------------------------------------------------------------- the plan


@dataclass
class HistBins:
    """Equal-width bins of one y column: ``bins`` bins of ``width`` starting at ``origin``."""

    column: str
    origin: float
    width: float
    bins: int
    rows: int  # rows with a finite value


@dataclass
class PlotPlan:
    strategy: Strategy
    bucket_seconds: float | None = None
    bucket_width: float | None = None
    sample_rows: int | None = None
    folded_series: int = 0
    x_expr: str = ""
    x_type: str | None = None
    rows_scanned: int = 0
    # beyond the contract of the plan, set by plan_plot
    budget_rows: int = 0
    n_series: int = 1  # series slots the budget is shared over (kept + (other) + (null))
    row_limit: int | None = None  # raw with a single x value: the first rows only
    bucket_label: str | None = None
    x_origin: float | None = None
    n_buckets: int | None = None
    x_ok: str | None = None  # predicate for rows that can be placed on the x axis
    hist: list[HistBins] = field(default_factory=list)
    folded_categories: int = 0
    notes: list[str] = field(default_factory=list)


def series_slots(n_series: int, max_series: int, has_null: bool) -> int:
    """Series in a result: the kept ones, ``(other)`` when folded, ``(null)`` when present."""
    kept = min(n_series, max_series)
    return max(1, kept + (1 if n_series > max_series else 0) + (1 if has_null else 0))


def choose_bucket_seconds(span_seconds: float, budget_rows: int, n_series: int) -> int:
    """The smallest width whose buckets fit the budget (plan Section 3.2, review W-02).

    A span can touch ``ceil(span / width) + 1`` buckets of a fixed grid, so that is what is
    counted per series. Above the ladder (even 365 days overshoots) the width is derived:
    ``ceil(span / max(1, budget_rows // n_series - 1))`` rounded up to whole days, which is not
    in ``BUCKET_NAMES`` and is written ``N days``.
    """
    # TUNING(bucket-ladder)
    for width in BUCKET_LADDER_SECONDS:
        if (math.ceil(span_seconds / width) + 1) * n_series <= budget_rows:
            return width
    per_series = max(1, budget_rows // max(1, n_series) - 1)
    days = math.ceil(math.ceil(span_seconds / per_series) / _DAY_SECONDS)
    return max(days, BUCKET_LADDER_SECONDS[-1] // _DAY_SECONDS + 1) * _DAY_SECONDS


def bucket_interval(width_seconds: int) -> str:
    """The interval text of a bucket width: a ladder name, else whole days (``N days``)."""
    name = BUCKET_NAMES.get(width_seconds)
    return name if name is not None else f"{width_seconds // _DAY_SECONDS} days"


def _base_type(data_type: str) -> str:
    return data_type.upper().split("(")[0]


def _is_float(data_type: str) -> bool:
    return _base_type(data_type) in ("FLOAT", "DOUBLE")


def x_expression(
    spec: PlotSpec, column_types: dict[str, str] | None
) -> tuple[str, str | None, str | None]:
    """``(SQL expression, type after casting, predicate for usable rows)`` of the x axis.

    Temporal x: ``DATE`` and ``TIMESTAMP_S/MS/NS`` are cast to ``TIMESTAMP``,
    ``TIMESTAMP WITH TIME ZONE`` becomes naive UTC; numeric x is cast to ``DOUBLE``. Rows
    whose x is NULL, not finite or out of the TIMESTAMP range cannot be placed on an axis and
    are left out.
    """
    if spec.kind == "histogram":
        return "", None, None
    if spec.kind == "bar":
        column = quote_ident(spec.x or "")
        return f"COALESCE(CAST({column} AS VARCHAR), '{NULL_SERIES}')", "VARCHAR", None
    if spec.x is None:
        return quote_ident(_RN), "BIGINT", None
    column = quote_ident(spec.x)
    data_type = (column_types or {}).get(spec.x, "")
    base = _base_type(data_type)
    if is_temporal_type(data_type):
        if base == "TIMESTAMP":
            expr = column
        elif data_type.upper() == "TIMESTAMP WITH TIME ZONE":
            expr = f"({column} AT TIME ZONE 'UTC')"
        else:  # DATE, TIMESTAMP_S, TIMESTAMP_MS, TIMESTAMP_NS
            # TRY_CAST: a DATE beyond the TIMESTAMP range becomes NULL, so that row is left out
            # and counted instead of failing the whole plot (W-12)
            expr = f"TRY_CAST({column} AS TIMESTAMP)"
        return expr, "TIMESTAMP", f"{expr} IS NOT NULL AND isfinite({expr})"
    expr = f"CAST({column} AS DOUBLE)"
    predicate = f"{column} IS NOT NULL"
    if _is_float(data_type):
        predicate += f" AND isfinite({column})"
    return expr, "DOUBLE", predicate


def plan_plot(
    spec: PlotSpec,
    *,
    rows: int,
    span_seconds: float | None,
    n_series: int,
    column_types: dict[str, str] | None = None,
    x_min: float | None = None,
    has_null_series: bool = False,
    rows_scanned: int | None = None,
    y_ranges: list[tuple[int, float | None, float | None]] | None = None,
    n_categories: int = 0,
) -> PlotPlan:
    """Choose the strategy and its numbers (plan Section 3.2).

    ``rows`` are the rows that can be plotted (after the filter and without unusable x),
    ``span_seconds`` the span of x (seconds for a temporal x, the numeric range otherwise),
    ``n_series`` the number of distinct non-null colour values (0 without a colour column),
    ``y_ranges`` per y column
    ``(finite rows, min, max)`` for histograms, ``n_categories`` the distinct categories of
    a bar chart.
    """
    x_expr, x_type, x_ok = x_expression(spec, column_types)
    max_series = spec.resolved_max_series()
    folded = max(0, n_series - max_series) if spec.color else 0
    slots = series_slots(n_series, max_series, has_null_series) if spec.color else 1
    plan = PlotPlan(
        strategy="raw",
        folded_series=folded,
        x_expr=x_expr,
        x_type=x_type,
        rows_scanned=rows if rows_scanned is None else rows_scanned,
        budget_rows=spec.budget_rows(),
        n_series=slots,
        x_ok=x_ok,
    )

    if spec.kind == "histogram":
        plan.strategy = "histogram"
        for index, column in enumerate(spec.y):
            count, low, high = (y_ranges or [])[index] if y_ranges else (0, None, None)
            if count <= 0 or low is None or high is None:
                plan.notes.append(f"{column}: no finite values")
                continue
            if high > low:
                width = (high - low) / spec.bins
                if not math.isfinite(width):  # W-08: a range that overflows a double
                    plan.notes.append(f"{column}: the value range is too wide to bin")
                    continue
                plan.hist.append(HistBins(column, low, width, spec.bins, count))
            else:  # a constant column: one bin of width 1 centred on the value
                plan.hist.append(HistBins(column, low - 0.5, 1.0, 1, count))
        return plan

    if spec.kind == "bar":
        plan.strategy = "bar"
        plan.folded_categories = max(0, n_categories - spec.max_categories)
        return plan

    budget = plan.budget_rows
    if rows <= budget:
        return plan  # raw
    if spec.kind == "scatter":
        plan.strategy = "sample"
        plan.sample_rows = budget
        return plan

    # line chart above the budget: bucket x
    span = span_seconds if span_seconds is not None else 0.0
    if span <= 0 or not math.isfinite(span):
        # no usable span to bucket (a single x value, or a range that overflows a double, W-08):
        # the first rows in x order are shown instead
        plan.row_limit = budget
        why = "every x value is the same" if span <= 0 else "the x range is too wide to bucket"
        plan.notes.append(f"{why}; the first {budget:,} of {rows:,} rows are shown")
        return plan
    if x_type == "TIMESTAMP":
        width = choose_bucket_seconds(span, budget, slots)
        plan.strategy = "time_bucket"
        plan.bucket_seconds = float(width)
        plan.bucket_label = bucket_interval(width)
        plan.n_buckets = math.ceil(span / width) + 1
        return plan
    buckets = max(1, budget // slots)
    origin = 1.0 if spec.x is None else (x_min if x_min is not None else 0.0)
    plan.strategy = "numeric_bucket"
    plan.x_type = "DOUBLE"  # the bucket start
    plan.n_buckets = buckets
    plan.bucket_width = span / buckets
    plan.bucket_label = f"{plan.bucket_width:.6g}"
    plan.x_origin = origin
    return plan


# ---------------------------------------------------------------- validation


def _check_columns(spec: PlotSpec, known: dict[str, str]) -> None:
    used = [name for name in (spec.x, spec.color, *spec.y) if name is not None]
    unknown = [name for name in dict.fromkeys(used) if name not in known]
    if unknown:
        raise QueryError(
            "sql",
            f"unknown column(s) in {spec.schema_name}.{spec.table_name}: {', '.join(unknown)}",
        )


def validate_spec(
    con: duckdb.DuckDBPyConnection,
    db_name: str,
    spec: PlotSpec,
    *,
    timeout_s: float | None = None,
) -> dict[str, str]:
    """``{column: type}`` of the table; raises ``QueryError(kind="sql")`` for a bad spec.

    The column lookup runs under ``timeout_s`` when given (W-10).
    """
    columns = _table_columns(con, db_name, spec.schema_name, spec.table_name, timeout_s=timeout_s)
    if not columns:
        raise QueryError("sql", f"unknown table or view {spec.schema_name}.{spec.table_name}")
    known = dict(columns)
    _check_columns(spec, known)
    needs_numeric_y = spec.kind in ("line", "scatter", "histogram") or (
        spec.kind == "bar" and bool(spec.y) and spec.agg != "count"
    )
    if needs_numeric_y:
        bad = [name for name in spec.y if not is_numeric_type(known[name])]
        if bad:
            described = ", ".join(f"{name} ({known[name]})" for name in bad)
            raise QueryError("sql", f"y columns must be numeric: {described}")
    if spec.kind in ("line", "scatter") and spec.x is not None:
        data_type = known[spec.x]
        if not (is_temporal_type(data_type) or is_numeric_type(data_type)):
            raise QueryError(
                "sql",
                f"x must be a date/time or numeric column for a {spec.kind} chart, "
                f"{spec.x} is {data_type}",
            )
    return known


# ---------------------------------------------------------------- SQL


@dataclass
class _Parts:
    """The pieces every query of one plot shares."""

    table: str
    where: str  # the user's filter as a SQL condition, or TRUE
    source: str  # FROM clause: the table, or the row-number subquery
    outer_where: str  # WHERE condition on the source (filter + usable x)
    series: str | None  # SQL expression of the series label
    series_cte: str | None


def _filter_condition(spec: PlotSpec) -> str:
    # The text sits on its own lines, so a trailing "--" comment cannot swallow the bracket.
    return f"(\n{spec.where}\n)" if spec.where else "TRUE"


def _join_conditions(*conditions: str | None) -> str:
    parts = [c for c in conditions if c and c != "TRUE"]
    return " AND ".join(f"({c})" if " AND " in c or " OR " in c else c for c in parts) or "TRUE"


def _parts(spec: PlotSpec, plan: PlotPlan, db_name: str) -> _Parts:
    table = qualified_name(db_name, spec.schema_name, spec.table_name)
    where = _filter_condition(spec)
    if spec.kind in ("line", "scatter") and spec.x is None:
        source = (
            f"(SELECT row_number() OVER () AS {quote_ident(_RN)}, * "
            f"FROM {table} WHERE {where}) AS q"
        )
        outer = "TRUE"
    else:
        source = table
        outer = _join_conditions(where, plan.x_ok)
    series = series_cte = None
    if spec.color:
        column = quote_ident(spec.color)
        if plan.folded_series > 0:
            keep = spec.resolved_max_series()
            series_cte = (
                f"{_SERIES_CTE} AS (SELECT CAST({column} AS VARCHAR) AS s FROM {table} "
                f"WHERE {_join_conditions(where, f'{column} IS NOT NULL')} "
                f"GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT {keep})"
            )
            series = (
                f"CASE WHEN {column} IS NULL THEN '{NULL_SERIES}' "
                f"WHEN CAST({column} AS VARCHAR) IN (SELECT s FROM {_SERIES_CTE}) "
                f"THEN CAST({column} AS VARCHAR) ELSE '{OTHER}' END"
            )
        else:
            series = f"COALESCE(CAST({column} AS VARCHAR), '{NULL_SERIES}')"
    return _Parts(table, where, source, outer, series, series_cte)


def _with(*ctes: str | None) -> str:
    present = [c for c in ctes if c]
    return f"WITH {', '.join(present)}\n" if present else ""


def _literal(value: float) -> str:
    """A float as an exact DOUBLE literal; the values are computed here, never taken from text.

    A plain ``0.1`` is a DECIMAL in DuckDB and does not convert back to the same double, which
    pushed a minimum into bin -1; 17 significant digits in exponent form are a DOUBLE.
    """
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite number in a plot query")
    return f"CAST({number:.16e} AS DOUBLE)"


def _measure(agg: str, column: str | None) -> str:
    if column is None or agg == "count":
        return "count(*)" if column is None else f"count({quote_ident(column)})"
    function = {"mean": "avg", "sum": "sum", "min": "min", "max": "max"}[agg]
    return f"{function}({quote_ident(column)})"


def _raw_select(spec: PlotSpec, plan: PlotPlan, parts: _Parts) -> str:
    columns = [f"{plan.x_expr} AS x"]
    if parts.series:
        columns.append(f"{parts.series} AS s")
    columns += [quote_ident(name) for name in spec.y]
    return f"SELECT {', '.join(columns)} FROM {parts.source} WHERE {parts.outer_where}"


def _sql_raw(spec: PlotSpec, plan: PlotPlan, parts: _Parts) -> str:
    sql = _with(parts.series_cte) + _raw_select(spec, plan, parts)
    if spec.kind == "line":
        sql += " ORDER BY 1" + (", 2" if parts.series else "")
    if plan.row_limit:
        sql += f" LIMIT {plan.row_limit}"
    return sql


def _sql_sample(spec: PlotSpec, plan: PlotPlan, parts: _Parts) -> str:
    # USING SAMPLE is applied before WHERE (verified), so the filtered rows are sampled from a
    # subquery; sampling the table directly would return fewer rows than asked for.
    return (
        f"{_with(parts.series_cte)}SELECT * FROM ({_raw_select(spec, plan, parts)}) "
        f"USING SAMPLE reservoir({plan.sample_rows} ROWS) REPEATABLE ({int(spec.seed)})"
    )


def _sql_buckets(spec: PlotSpec, plan: PlotPlan, parts: _Parts) -> str:
    if plan.strategy == "time_bucket":
        width = int(plan.bucket_seconds or 1)
        x_out = f"time_bucket(INTERVAL '{bucket_interval(width)}', {plan.x_expr})"
    else:
        origin = _literal(plan.x_origin or 0.0)
        step = _literal(plan.bucket_width or 1.0)
        last = max(1, plan.n_buckets or 1) - 1
        index = f"greatest(0, least(floor(({plan.x_expr} - {origin}) / {step}), {last}))"
        x_out = f"CAST({origin} + {index} * {step} AS DOUBLE)"
    columns = [f"{x_out} AS x"]
    group = ["1"]
    if parts.series:
        columns.append(f"{parts.series} AS s")
        group.append("2")
    for name in spec.y:
        column = quote_ident(name)
        columns += [f"avg({column})", f"min({column})", f"max({column})", f"count({column})"]
    columns.append("count(*) AS n")
    return (
        f"{_with(parts.series_cte)}SELECT {', '.join(columns)} FROM {parts.source} "
        f"WHERE {parts.outer_where} GROUP BY {', '.join(group)} ORDER BY {', '.join(group)}"
    )


def _sql_histogram(spec: PlotSpec, plan: PlotPlan, parts: _Parts) -> str:
    selects = []
    for index, column in enumerate(spec.y):
        bins = next((h for h in plan.hist if h.column == column), None)
        if bins is None:
            continue
        value = f"CAST({quote_ident(column)} AS DOUBLE)"
        bucket = (
            f"CAST(greatest(0, least(floor(({value} - {_literal(bins.origin)}) / "
            f"{_literal(bins.width)}), {bins.bins - 1})) AS BIGINT)"
        )
        columns = [f"{index} AS yi", f"{bucket} AS b"]
        group = ["2"]
        if parts.series:
            columns.append(f"{parts.series} AS s")
            group.append("3")
        columns.append("count(*) AS n")
        condition = _join_conditions(parts.outer_where, f"isfinite({value})")
        selects.append(
            f"SELECT {', '.join(columns)} FROM {parts.source} WHERE {condition} "
            f"GROUP BY {', '.join(group)}"
        )
    if not selects:  # nothing to bin: an empty result with the right shape
        extra = ", CAST(NULL AS VARCHAR) AS s" if parts.series else ""
        return (
            "SELECT CAST(NULL AS BIGINT) AS yi, CAST(NULL AS BIGINT) AS b"
            f"{extra}, CAST(NULL AS BIGINT) AS n WHERE FALSE"
        )
    return _with(parts.series_cte) + "\nUNION ALL\n".join(selects)


def _free_name(name: str, taken: set[str]) -> str:
    """``name``, with underscores prepended until it is not a column of the table."""
    while name in taken:
        name = "_" + name
    return name


def _sql_bar(
    spec: PlotSpec, plan: PlotPlan, parts: _Parts, column_names: set[str] | None = None
) -> str:
    """The bar query: categories ranked in a CTE, then joined back to fold the tail.

    The CTE's columns are named so that no column of the table has the same name (W-05): the
    join exposes both sides, and an unqualified table column such as ``df_c`` would otherwise be
    ambiguous. The source subquery is aliased as well.
    """
    taken = column_names or set()
    cat_col = quote_ident(_free_name(_RANK_CATEGORY, taken))
    num_col = quote_ident(_free_name(_RANK_NUMBER, taken))
    measure_col = quote_ident(_free_name("df_m", taken))
    measures = [_measure(spec.agg, name) for name in spec.y] or ["count(*)"]
    category = plan.x_expr
    rank, number = f"{_RANK_CTE}.{num_col}", f"{_RANK_CTE}.{cat_col}"
    ranked = (
        f"{_RANK_CTE} AS (SELECT {cat_col}, row_number() OVER "
        f"(ORDER BY {measure_col} DESC NULLS LAST, {cat_col}) AS {num_col} FROM "
        f"(SELECT {category} AS {cat_col}, {measures[0]} AS {measure_col} FROM {parts.table} "
        f"WHERE {parts.outer_where} GROUP BY 1) AS df_src)"
    )
    keep = spec.max_categories
    columns = [f"CASE WHEN {rank} <= {keep} THEN {category} ELSE '{OTHER}' END AS cat"]
    order = ["1"]
    if parts.series:
        columns.append(f"{parts.series} AS s")
    columns += [f"{m} AS v{i}" for i, m in enumerate(measures)]
    columns += ["count(*) AS n", f"min({rank}) AS rk"]
    group = ["1", "2"] if parts.series else ["1"]
    rank_position = len(columns)  # the last column
    order = [str(rank_position)] + (["2"] if parts.series else [])
    return (
        f"{_with(ranked, parts.series_cte)}SELECT {', '.join(columns)} "
        f"FROM {parts.table} JOIN {_RANK_CTE} ON {category} = {number} "
        f"WHERE {parts.outer_where} GROUP BY {', '.join(group)} ORDER BY {', '.join(order)}"
    )


def build_sql(
    spec: PlotSpec, plan: PlotPlan, db_name: str, column_types: dict[str, str] | None = None
) -> str:
    """The data query of ``plan``; ``column_types`` names the table's columns (bar helper names)."""
    parts = _parts(spec, plan, db_name)
    if plan.strategy == "raw":
        return _sql_raw(spec, plan, parts)
    if plan.strategy == "sample":
        return _sql_sample(spec, plan, parts)
    if plan.strategy in ("time_bucket", "numeric_bucket"):
        return _sql_buckets(spec, plan, parts)
    if plan.strategy == "histogram":
        return _sql_histogram(spec, plan, parts)
    return _sql_bar(spec, plan, parts, set(column_types or {}))


# ---------------------------------------------------------------- the counts query


@dataclass
class _Counts:
    n_all: int = 0
    n_rows: int = 0
    span: float | None = None
    x_min: float | None = None
    n_series: int = 0
    n_series_rows: int = 0
    n_categories: int = 0
    y_ranges: list[tuple[int, float | None, float | None]] = field(default_factory=list)


def _counts_sql(spec: PlotSpec, plan: PlotPlan, db_name: str) -> tuple[str, list[str]]:
    """One statement with everything ``plan_plot`` needs, and the meaning of each column."""
    parts = _parts(spec, plan, db_name)
    ok = plan.x_ok
    suffix = f" FILTER (WHERE {ok})" if ok else ""
    columns = ["count(*) AS n_all", f"count(*){suffix} AS n_rows"]
    meaning = ["n_all", "n_rows"]
    if spec.kind in ("line", "scatter"):
        if plan.x_type == "TIMESTAMP":
            newest, oldest = f"max({plan.x_expr}){suffix}", f"min({plan.x_expr}){suffix}"
            columns.append(f"epoch({newest} - {oldest}) AS span")
            meaning.append("span")
        elif plan.x_type == "DOUBLE":
            columns += [f"min({plan.x_expr}){suffix}", f"max({plan.x_expr}){suffix}"]
            meaning += ["x_min", "x_max"]
    if spec.color:
        text = f"CAST({quote_ident(spec.color)} AS VARCHAR)"
        columns += [f"count(DISTINCT {text}){suffix}", f"count({text}){suffix}"]
        meaning += ["n_series", "n_series_rows"]
    if spec.kind == "bar":
        columns.append(f"count(DISTINCT {plan.x_expr})")
        meaning.append("n_categories")
    if spec.kind == "histogram":
        for name in spec.y:
            value = f"CAST({quote_ident(name)} AS DOUBLE)"
            finite = f" FILTER (WHERE isfinite({value}))"
            columns += [f"count({value}){finite}", f"min({value}){finite}", f"max({value}){finite}"]
            meaning += [f"y_n:{name}", f"y_min:{name}", f"y_max:{name}"]
    source = parts.source
    where = parts.where if source == parts.table else "TRUE"
    return f"SELECT {', '.join(columns)} FROM {source} WHERE {where}", meaning


def _read_counts(spec: PlotSpec, plan: PlotPlan, row: tuple, meaning: list[str]) -> _Counts:
    values = dict(zip(meaning, row, strict=True))
    counts = _Counts(n_all=int(values["n_all"]), n_rows=int(values["n_rows"]))
    if plan.x_type == "TIMESTAMP" and values.get("span") is not None:
        counts.span = float(values["span"])
    if "x_min" in values and values["x_min"] is not None:
        counts.x_min = float(values["x_min"])
        counts.span = float(values["x_max"]) - counts.x_min
    if spec.kind in ("line", "scatter") and spec.x is None:  # row order: 1 .. n
        counts.x_min = 1.0
        counts.span = float(max(counts.n_rows - 1, 0))
    counts.n_series = int(values.get("n_series") or 0)
    counts.n_series_rows = int(values.get("n_series_rows") or 0)
    counts.n_categories = int(values.get("n_categories") or 0)
    for name in spec.y if spec.kind == "histogram" else []:
        low, high = values[f"y_min:{name}"], values[f"y_max:{name}"]
        counts.y_ranges.append(
            (
                int(values[f"y_n:{name}"] or 0),
                None if low is None else float(low),
                None if high is None else float(high),
            )
        )
    return counts


# ---------------------------------------------------------------- the result


@dataclass
class PlotData:
    spec: PlotSpec
    strategy: Strategy
    columns: list[str]
    rows: list[tuple]
    x_type: str | None
    series: list[str]
    bucket: str | None
    rows_scanned: int
    points: int
    sampled: bool
    folded_series: int
    folded_categories: int
    elapsed_s: float
    sql: str
    notes: list[str] = field(default_factory=list)


def _unique(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for name in names:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return out


def _order_series(weights: dict[str, int]) -> list[str]:
    """Series by weight (row count) descending; ``(null)`` and ``(other)`` last."""
    regular = sorted(
        (s for s in weights if s not in (NULL_SERIES, OTHER)), key=lambda s: (-weights[s], s)
    )
    return [*regular, *(s for s in (NULL_SERIES, OTHER) if s in weights)]


def _x_name(spec: PlotSpec) -> str:
    return spec.x if spec.x is not None else ROW_ORDER


def _shape(
    spec: PlotSpec, plan: PlotPlan, result_rows: list[tuple]
) -> tuple[list[str], list[tuple], list[str]]:
    """``(column names, rows, series in order)`` from the rows of the data query."""
    has_s = bool(spec.color)
    series_weight: dict[str, int] = {}
    if plan.strategy in ("raw", "sample"):
        names = [_x_name(spec), *([spec.color] if has_s else []), *spec.y]
        if has_s:
            for row in result_rows:
                series_weight[row[1]] = series_weight.get(row[1], 0) + 1
        columns = _unique([n for n in names if n is not None])
        return columns, result_rows, _order_series(series_weight)

    if plan.strategy in ("time_bucket", "numeric_bucket"):
        names = [_x_name(spec), *([spec.color] if has_s else [])]
        for name in spec.y:
            names += [f"{name}_mean", f"{name}_min", f"{name}_max", f"{name}_count"]
        rows = [row[:-1] for row in result_rows]
        if has_s:
            for row in result_rows:
                series_weight[row[1]] = series_weight.get(row[1], 0) + int(row[-1])
        return _unique([n for n in names if n is not None]), rows, _order_series(series_weight)

    if plan.strategy == "histogram":
        names = ["y_column", "bin_start", "bin_end", *([spec.color] if has_s else []), "count"]
        by_index = {
            i: next((h for h in plan.hist if h.column == c), None) for i, c in enumerate(spec.y)
        }
        rows = []
        for row in result_rows:
            bins = by_index[int(row[0])]
            assert bins is not None
            start = bins.origin + int(row[1]) * bins.width
            tail = (row[2], int(row[3])) if has_s else (int(row[2]),)
            if has_s:
                series_weight[row[2]] = series_weight.get(row[2], 0) + int(row[3])
            rows.append((bins.column, start, start + bins.width, *tail))
        rows.sort(key=lambda r: (spec.y.index(r[0]), r[1]))
        return _unique([n for n in names if n is not None]), rows, _order_series(series_weight)

    # bar: category, [series], v0, v1, ..., n, rk
    value_names = [
        f"{spec.agg}({name})" if spec.agg != "count" else f"count({name})" for name in spec.y
    ] or ["count"]
    names = [_x_name(spec), *([spec.color] if has_s else []), *value_names]
    rows = [row[:-2] for row in result_rows]
    if has_s:
        for row in result_rows:
            series_weight[row[1]] = series_weight.get(row[1], 0) + int(row[-2])
    return _unique([n for n in names if n is not None]), rows, _order_series(series_weight)


def _fetch_cap(spec: PlotSpec, plan: PlotPlan) -> int:
    """The most rows the plan can legitimately return."""
    slots = plan.n_series
    if plan.strategy == "histogram":
        return max(1, spec.bins * len(spec.y) * slots)
    if plan.strategy == "bar":
        return (spec.max_categories + 1) * slots
    if plan.strategy == "time_bucket":
        # at least two buckets per series are possible (a span can straddle a grid boundary), so
        # a budget below 2 rows per series slot is exceeded by that and no more (W-02)
        return max(plan.budget_rows, (plan.n_buckets or 0) * slots)
    return plan.budget_rows


def _single_statement(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    if len(con.extract_statements(sql)) != 1:  # e.g. a filter that ends the statement early
        raise QueryError("sql", "the filter must be one boolean expression, not several statements")


def _deadline(started: float, config: QueryConfig) -> Callable[[], float]:
    """``remaining()``: seconds left of the plot's timeout, ``QueryError(timeout)`` when none."""
    deadline = started + config.timeout_s

    def remaining() -> float:
        left = deadline - time.perf_counter()
        if left <= 0:
            raise QueryError("timeout", f"the plot ran longer than {config.timeout_s:g} s")
        return left

    return remaining


def fetch_plot_data_on(
    con: duckdb.DuckDBPyConnection,
    db_name: str,
    spec: PlotSpec,
    config: QueryConfig,
    *,
    started: float | None = None,
) -> PlotData:
    """Plan and run the plot queries on an open sandboxed connection.

    ``db_name`` is the database that holds the table (``memory`` for the union views of a
    flock connection). All statements share the timeout of ``config`` counted from ``started``
    (``time.perf_counter()``, default: now). Raises ``QueryError``.
    """
    started = time.perf_counter() if started is None else started
    remaining = _deadline(started, config)

    with as_query_errors():
        column_types = validate_spec(con, db_name, spec, timeout_s=remaining())
        # the plan needs numbers that only the data can give: a first plan fixes the expressions
        draft = plan_plot(spec, rows=0, span_seconds=None, n_series=0, column_types=column_types)
        counts_sql, meaning = _counts_sql(spec, draft, db_name)
        _single_statement(con, counts_sql)
        counted = execute_limited(con, counts_sql, 1, remaining())
        counts = _read_counts(spec, draft, counted.rows[0], meaning)
        plan = plan_plot(
            spec,
            rows=counts.n_rows,
            span_seconds=counts.span,
            n_series=counts.n_series,
            column_types=column_types,
            x_min=counts.x_min,
            has_null_series=counts.n_series_rows < counts.n_rows if spec.color else False,
            rows_scanned=counts.n_all,
            y_ranges=counts.y_ranges,
            n_categories=counts.n_categories,
        )
        try:
            sql = build_sql(spec, plan, db_name, column_types)
        except ValueError as exc:  # a number that cannot enter the SQL; never a bare ValueError
            raise QueryError("other", f"cannot build the plot query: {exc}") from exc
        _single_statement(con, sql)
        cap = _fetch_cap(spec, plan)
        result = execute_limited(con, sql, cap + 1, remaining())

    if len(result.rows) > cap:
        raise QueryError(
            "other",
            f"internal error in the plot plan: {len(result.rows)} rows came back for a budget "
            f"of {cap} ({plan.strategy}); please report this",
        )
    columns, rows, series = _shape(spec, plan, result.rows)
    notes = list(plan.notes)
    if counts.n_all > counts.n_rows:
        left_out = counts.n_all - counts.n_rows
        notes.append(f"{left_out:,} rows without a usable x value were left out")
    if spec.kind == "histogram":
        for bins in plan.hist:
            missing = counts.n_all - bins.rows
            if missing > 0:
                notes.append(f"{bins.column}: {missing:,} rows without a finite value left out")
    points = len(rows) if plan.strategy in ("histogram", "bar") else len(rows) * len(spec.y)
    return PlotData(
        spec=spec,
        strategy=plan.strategy,
        columns=columns,
        rows=rows,
        x_type=plan.x_type,
        series=series,
        bucket=plan.bucket_label,
        rows_scanned=counts.n_all,
        points=points,
        sampled=plan.strategy == "sample",
        folded_series=plan.folded_series,
        folded_categories=plan.folded_categories,
        elapsed_s=time.perf_counter() - started,
        sql=sql,
        notes=notes,
    )


def fetch_plot_data(db_path: str | Path, spec: PlotSpec, config: QueryConfig) -> PlotData:
    """Plan and run the plot queries on ``db_path`` (read-only, sandboxed).

    Raises ``QueryError`` (kind ``sql`` for a bad spec or filter, ``timeout``, ``other``).
    """
    started = time.perf_counter()
    remaining = _deadline(started, config)
    with as_query_errors(), open_readonly(db_path, config) as con:
        db_name = database_name(con, timeout_s=remaining())
        return fetch_plot_data_on(con, db_name, spec, config, started=started)


def _attach_notes(failures: list[AttachFailure]) -> list[str]:
    return [f"{f.rel_path} could not be attached ({f.kind}): {f.message}" for f in failures]


def fetch_flock_plot_data(
    selection: FlockSelection,
    paths: dict[str, str],
    spec: PlotSpec,
    config: QueryConfig,
) -> PlotData:
    """Plan and run the plot queries on the union view ``spec.schema_name.spec.table_name``.

    The flock of ``selection`` is opened (``paths``: rel_path -> absolute path). The label
    column of the view (``database``, see ``UnionView.label_column``) is an ordinary VARCHAR
    column, so the series cap folds the files beyond the cap into ``(other)`` like any other
    series. ``PlotData.notes`` carries the members that could not be attached. Raises
    ``QueryError``.
    """
    started = time.perf_counter()
    with as_query_errors(), open_flock(selection, paths, config) as flock:
        assert flock.con is not None
        view = flock.view(spec.schema_name, spec.table_name)
        if view is None:
            known = ", ".join(v.qualified for v in flock.views) or "none"
            raise QueryError(
                "sql",
                f"unknown table {spec.schema_name}.{spec.table_name} in the selected databases "
                f"(tables: {known})",
            )
        # columns that must be numbers or times: a cast column is text, which only a category,
        # a colour or a counted column can be
        numeric = [*spec.y] if spec.kind == "histogram" or spec.agg != "count" else []
        if spec.kind in ("line", "scatter") and spec.x:
            numeric.append(spec.x)
        as_text = [name for name in numeric if name.lower() in view.cast_names]
        if as_text:
            names = ", ".join(dict.fromkeys(as_text))
            raise QueryError(
                "sql",
                f"{names} has different types in the selected databases and is shown as text; "
                "choose other columns or other databases",
            )
        data = fetch_plot_data_on(flock.con, MEMORY, spec, config, started=started)
        data.notes = [*_attach_notes(flock.failures), *data.notes]
        return data
