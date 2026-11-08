# Implementation plan v0.3: visualisation

Status: ready for implementation, 2026-11-08. Written for Sonnet 5.5 or any
Claude Code session. Read `CLAUDE.md` first, then this file top to bottom,
then `docs/REVIEW_v0.2.md` (Phase 0 fixes its findings). Everything here is
decided unless marked (TBC) or listed in Section 10. Facts marked
(verified) were checked on 2026-11-08 against duckdb 1.5.6, streamlit
1.66.1, plotly 7.1.0, Python 3.11.9, Windows 11.

## Key immediate next steps

1. Phase 0 first: the v0.2 review fixes, shipped as 0.2.1 in one commit.
2. Then Phases 1 to 4 in order, one commit per phase on `main`, `uv run
   ruff check .` clean and `uv run pytest` green locally, CI green on both
   OSes before the next phase.
3. After Phase 4, tick the acceptance checklist (Section 8), bump to
   0.3.0, and stop. Do not start v0.4 features.
4. Never end a turn on a browser check. A turn ends with a commit pushed
   or with a written status of what is left; the manual UI check of the
   acceptance list is done, recorded in `CHANGELOG.md`, and followed by the
   commit in the same turn.

## 1. What v0.3 delivers

1. **Plot page** (Streamlit): pick a database and a table of the scanned
   fleet; choose a chart kind (line, scatter, histogram, bar), an x column
   (temporal columns first, numeric next, or the row order), one or more y
   columns, an optional colour column (series), an optional SQL filter, and
   the point budget; DuckDB does the aggregation so the browser never
   receives more than the budget; the page shows the chart, how the data
   was reduced, the plotted data as a table, and offers it as a CSV
   download.
2. **Time-series first**: with a temporal x and more rows than the budget,
   the data is bucketed with `time_bucket` to a width chosen from the span
   and the budget, with mean, min and max per bucket and series; the mean
   is drawn as the line and min to max as a band.
3. **CLI** `duckfleet plot`: the same reduction, written as CSV or JSON
   (the chart itself is the page's job; an HTML export is an open option).
4. **Database page** gets a **Plot this table** button.
5. **0.2.1 fixes** from `docs/REVIEW_v0.2.md` (Phase 0).
6. Everything works on Windows and Linux; CI runs the suite on both.

## 2. Repository layout after v0.3 (additions only)

```
packages/duckfleet-core/src/duckfleet_core/
  plotdata.py          PlotSpec, PlotPlan, PlotData, plan_plot, fetch_plot_data
  cli_plot.py          duckfleet plot
packages/duckfleet-core/tests/
  test_plotdata.py, test_cli_plot.py
packages/duckfleet-ui/src/duckfleet_ui/
  charts.py            palette, theme detection, Plotly figure builders (no Streamlit widgets)
  views/plot.py        render()
packages/duckfleet-ui/tests/
  _plot_app.py, test_ui_plot.py, test_charts.py
```

## 3. Data contracts

### 3.1 PlotSpec (`plotdata.py`)

Pydantic model, validated before any SQL is built:

| Field | Type / default | Meaning |
|---|---|---|
| `schema_name`, `table_name` | str | the table or view |
| `kind` | `"line" \| "scatter" \| "histogram" \| "bar"` | chart kind |
| `x` | `str \| None` | x column; `None` means the row order for line and scatter (`row_number() over ()`), unused for histogram, required for bar |
| `y` | `list[str]` | value columns; numeric for line, scatter and histogram; for bar may be empty (then the measure is the row count) |
| `color` | `str \| None` | series column (group by) |
| `where` | `str \| None` | SQL boolean expression evaluated on the sandboxed connection |
| `max_points` | int, default 50,000 | L-11: budget of plotted points (rows x y columns after reduction) |
| `bins` | int, default 50 | histogram bins, 5 to 500 (L-20) |
| `agg` | `"mean" \| "sum" \| "min" \| "max" \| "count"`, default `mean` | measure for bar charts |
| `max_categories` | int, default 200 | L-18: bar categories kept (the rest folded into `(other)`) |
| `max_series` | `int \| None`, default `None` | L-19: series kept (the rest folded into `(other)`); `resolved_max_series()` returns the field when set, else 3 for scatter and 8 otherwise |
| `seed` | int, default 42 | reservoir sample seed for scatter |

Validation (raise `QueryError(kind="sql")` with a plain message): columns
must exist in the table (checked against `duckdb_columns()` like
`preview_table`); `y` columns must be numeric types (`is_numeric_type`) for
line, scatter and histogram; `x` must be temporal, numeric or `None` for
line and scatter; `bins` within range; `max_points` at least 100.

### 3.2 Strategies (`PlotPlan.strategy`)

Decided by `plan_plot(spec, counts)` where `counts` comes from one
preliminary query: `select count(*), count(distinct color), min(x), max(x)
from table where (filter)` (the distinct count and the span only when the
spec needs them).

| Kind | Rows after filter <= budget | Above the budget |
|---|---|---|
| line, temporal x | `raw`: every row, ordered by x | `time_bucket`: `time_bucket(INTERVAL '<w>', x)` with `avg`, `min`, `max`, `count` per y, grouped by bucket and series |
| line, numeric x | `raw` | `numeric_bucket`: `floor((x - xmin) / w) * w + xmin` with the same aggregates |
| line, row order | `raw` with `row_number() over ()` as x | `numeric_bucket` over the row number |
| scatter | `raw` | `sample`: `USING SAMPLE reservoir(<n> ROWS) REPEATABLE (<seed>)` |
| histogram | always `histogram`: equal-width bins over each y column: `floor((y - ymin) / ((ymax - ymin) / bins))` clamped to `bins - 1`, `count(*)` per bin and series | same |
| bar | always `bar`: `group by x [, color]` with the measure; categories ordered by the measure descending, the first `max_categories` kept, the rest summed into `(other)` | same |

Budget arithmetic: `budget_rows = max_points // max(1, len(y))`; for
`time_bucket` choose the smallest width from the ladder `1s, 5s, 15s, 30s,
1min, 5min, 15min, 30min, 1h, 3h, 6h, 12h, 1d, 7d, 30d, 90d, 365d` such
that `span / width * n_series <= budget_rows`; for `numeric_bucket` the
width is `span / (budget_rows / n_series)`; for `sample` the sample size is
`budget_rows`. If the span is zero, fall back to `raw` limited to
`budget_rows` rows.

Series: when the distinct count of `color` exceeds the resolved
`max_series`, the kept series are the top `max_series` by row count and
the rest become one series named `(other)`. This happens inside the data
query, never by interpolating data values into SQL: a CTE `WITH top AS
(SELECT color::VARCHAR AS s FROM <table> WHERE (<filter>) GROUP BY 1 ORDER
BY count(*) DESC LIMIT <max_series>)` and the series expression `CASE WHEN
color IS NULL THEN '(null)' WHEN color::VARCHAR IN (SELECT s FROM top) THEN
color::VARCHAR ELSE '(other)' END`. The cast makes non-text colour columns
work; NULL colours form their own series `(null)`. Series order for the
palette comes from the result rows, by row count descending, `(other)`
last. Line, histogram and bar charts get up to 8 series, scatter up to 3
(decision D-032: beyond that, colours stop being distinguishable for every
reader).

Ladder exhaustion: if even the widest width overshoots the budget, use the
widest width; `span / width` rows is then small and the guard below passes.

Temporal x of type `DATE` is cast to `TIMESTAMP`; `TIMESTAMP_S/MS/NS` are
cast to `TIMESTAMP`; `TIMESTAMP WITH TIME ZONE` becomes `x AT TIME ZONE
'UTC'` (naive UTC, as in the profiler).

### 3.3 PlotData

```python
@dataclass
class PlotData:
    spec: PlotSpec
    strategy: Literal["raw", "time_bucket", "numeric_bucket", "sample", "histogram", "bar"]
    columns: list[str]        # see below per strategy
    rows: list[tuple]
    x_type: str | None        # DuckDB type of x after casting ("TIMESTAMP", "DOUBLE", ...)
    series: list[str]         # distinct series values in fixed order (by row count desc), incl. "(other)"
    bucket: str | None        # "15 minutes" for time_bucket, "12.5" for numeric_bucket, None otherwise
    rows_scanned: int         # count(*) after the filter
    points: int               # len(rows) * len(y) (or len(rows) for histogram and bar)
    sampled: bool             # scatter sample
    folded_series: int        # series folded into (other)
    folded_categories: int    # bar categories folded into (other)
    elapsed_s: float
    sql: str                  # the final query, for the "SQL" expander and the CLI --show-sql
```

Column layouts: `raw` and `sample`: `x, [series], y1, y2, ...`;
`time_bucket` and `numeric_bucket`: `x, [series], y1_mean, y1_min, y1_max,
y1_count, y2_mean, ...`; `histogram`: `y_column, bin_start, bin_end,
[series], count`; `bar`: `category, [series], value`.

`fetch_plot_data(db_path, spec, config: QueryConfig) -> PlotData` opens
the file with `open_readonly`, runs the preliminary count query and the
final query under the timeout (`execute_limited`). Budget guard: for the
strategies `raw`, `sample`, `time_bucket` and `numeric_bucket` the final
query is fetched with `max_rows = budget_rows + 1`; if more rows come back
than the budget, `fetch_plot_data` raises `QueryError(kind="other")` whose
message names the plan bug (this must never happen; the test suite asserts
it does not). `histogram` and `bar` are bounded by L-18, L-19 and L-20
instead of the point budget (at most `bins x series` and `max_categories x
series` rows), and the caption says so.

### 3.4 Chart rules (`charts.py`, decision D-032)

The UI renders `PlotData` with Plotly. The rules below are fixed; they come
from the data-visualisation method the project follows.

1. **Palette**: eight categorical slots in a fixed order, assigned to
   series in `PlotData.series` order and never cycled. Light surface:
   `#2a78d6, #eb6834, #1baf7a, #eda100, #e87ba4, #008300, #6250d6,
   #e34948`. Dark surface: `#3987e5, #d95926, #199e70, #c98500, #d55181,
   #008300, #9085e9, #e66767`. `(other)` always takes neutral grey
   (`#898781`), `(null)` the same grey at 60 % opacity. Several y columns
   without a colour column are series too (one slot each, in `spec.y`
   order). A colour column together with more than one y column always
   uses one panel per y column (item 2) with the series by colour inside
   each panel; no dash styles anywhere.
2. **One y axis.** Never a second y axis. When several y columns differ in
   scale by more than 100x (ratio of the largest absolute maxima), the page
   switches to **one panel per y column** (Plotly subplots sharing x) and
   says so; the user can force either layout with a radio.
3. **Marks**: 2px lines, 8px markers, min-to-max band as the series colour
   at 20 % opacity with no outline, hairline grid (`#e1e0d9` light,
   `#2c2c2a` dark), axis line `#c3c2b7` / `#383835`, no dashed grid, no
   value labels on points. Histogram and bar: a 2px gap between bars
   (`bargap` 0.1 or more), no bar borders. Band trace order in Plotly: for
   each series add the min trace (`line_width=0`, `showlegend=False`,
   `hoverinfo="skip"`) immediately followed by the max trace with
   `fill="tonexty"`, `line_width=0` and `fillcolor` as the series colour in
   rgba at 0.2, then the mean trace; otherwise fills bleed across series.
4. **Legend** always present for two or more series, none for one; hover
   on line charts is `hovermode="x unified"`; the bucket width and the
   strategy appear in the caption, not in the chart title.
5. **Table view**: the plotted data is always available in an expander
   under the chart (`st.dataframe`) and as a CSV download.
6. **Theme**: light or dark values from the Streamlit theme
   (`st.context.theme.type` when available, else light; TBC Section 10
   item 1), applied through a Plotly layout template; `st.plotly_chart(...,
   theme=None)` so Streamlit does not repaint the series.

## 4. Verified facts the code relies on

1. `time_bucket(INTERVAL '15 minutes', ts)` on TIMESTAMP, on `d::TIMESTAMP`
   for DATE, and on `tstz AT TIME ZONE 'UTC'` for TIMESTAMP WITH TIME ZONE
   all work under the locked read-only connection (verified). `time_bucket`
   on a timestamptz directly also works but returns aware values in the
   machine zone; use the UTC cast.
2. Numeric bins `floor((x - xmin) / w) * w + xmin` and histogram bins
   `floor((y - ymin) / w)` with `count(*)` per bin and series (verified).
3. `USING SAMPLE reservoir(n ROWS) REPEATABLE (seed)` works on the sandboxed
   connection (verified).
4. `row_number() over ()` as a pseudo x works (verified); it follows the
   table's storage order, which is the insertion order for an unsorted
   table.
5. `epoch(max(x) - min(x))` gives the span in seconds for bucket arithmetic
   (verified).
6. A filter expression with an unknown column raises `BinderException`,
   which `classify_error` turns into `QueryError(kind="sql")` (verified).
7. `"temp_directory": <path>` is accepted together with
   `lock_configuration` in the connect config and takes effect
   (`current_setting('temp_directory')`); the default without it is
   `<database path>.tmp` beside the scanned file (verified, review V-01).
8. Streamlit 1.66: `st.form(key, clear_on_submit=False, *,
   enter_to_submit=True, border=True, ...)` and `st.form_submit_button(label,
   ..., type=, disabled=)` (verified signatures); `st.plotly_chart(fig, *,
   width="stretch", height=, theme="streamlit" | None, key=, on_select=,
   config=)` (verified); `persist_state` exists on `number_input`,
   `text_area` and `selectbox` (verified, review V-11).
9. plotly 7.1.0 is installed; `plotly.graph_objects` and
   `plotly.subplots.make_subplots` are the APIs to use.

## 5. Phases

### Phase 0: 0.2.1, the review fixes

Implements every item of `docs/REVIEW_v0.2.md` Sections 2 to 4 and 6.

Core (`packages/duckfleet-core`):

1. V-01: `connection_config` gains `"temp_directory": str(temp_dir)` where
   `temp_dir` is `QueryConfig.temp_dir` resolved once per process to
   `<cache_dir>/tmp/<pid>` (`cache_dir` from `DUCKFLEET_CACHE_DIR` or
   `default_cache_dir()`, or `--cache-dir` in the CLI). The profiler's
   connect config gains the same key from `ScanConfig.cache_dir` (always
   resolved by the coordinator) with its own `<pid>` folder per worker
   process, plus `enable_external_access=False` and
   `lock_configuration=True` (P-05); the catalog writer gains the key too.
   Create the directory before connecting; a worker removes its folder on
   exit (best effort), and `run_scan` sweeps stale `tmp/<pid>` folders
   whose process no longer exists before starting the pool. Whether
   several DuckDB instances can share one temp directory safely is not
   verified, which is why each process gets its own; the per-process path
   still satisfies the one-config-per-process rule. Test: a spilling query
   with `memory_limit_mb=64` on a generated table while a thread lists the
   scanned folder; nothing but the database file is ever seen there, and
   the temp directory under the cache is used.
2. V-02: in `_with_timeout`, after `con.interrupt()`, every `duckdb.Error`
   raised by the worker becomes `QueryError("timeout", ...)`;
   `classify_error` maps a message containing `interrupt` to `timeout`.
   Test: a streaming VARCHAR/list result with `timeout_s=0.3` reports
   `timeout` in 5 of 5 runs.
3. V-03: for the `csv` and `json` formats `write_rows` first calls
   `sys.stdout.reconfigure(encoding="utf-8", newline="")` (no wrapper
   object, so nothing can close stdout when collected); `table` keeps the
   console encoding; README and LIMITS document `PYTHONUTF8=1` for the
   table format on Windows. Test
   through `subprocess` without `PYTHONUTF8`, comparing bytes for `日本語 é`
   and asserting `\n` line endings.
4. V-04: `_with_timeout` gets `except BaseException: con.interrupt();
   raise` before the `finally`.
5. V-06: `classify_error` order: `InterruptException` or `interrupt` in the
   text -> `timeout`; `PermissionException` -> `blocked`;
   `InvalidInputException` whose text starts with `Invalid Input Error:
   Cannot execute statement` or contains `read-only mode` -> `read_only`;
   `configuration has been locked` -> `blocked`; the SQL exception types ->
   `sql`; else `other`. Test the `select 'read-only mode'::INTEGER` case
   gives `sql`.
6. V-07: JSON output uses `allow_nan=False` with a `default` that maps NaN
   and infinities to `null`, `bytes` to a hex string, tuples to lists; CSV
   renders list and tuple values as JSON text.
7. V-08: remove the unreachable `(no result set)` branch.
8. P-07: fix the `get_or_compute` docstring.
9. Test gaps 4 to 6 of the review: a real cancellation test for P-02 (20
   files, 2 workers, consumer raises after the first saved profile, assert
   fewer profiles than candidates), an assertion on the catalog writer's
   config (R-07), a 2-worker run with a valid non-matching tag pattern
   (R-03), summaries of a nested-only table, a binary-only table and a view
   whose base table was dropped.

UI (`packages/duckfleet-ui`):

10. V-05: `_file_state` returns the live `stat`; `preview_page` is keyed by
    `st_size` and `st_mtime_ns` of the file now, not the catalog row; test:
    insert a row into a scanned file, rerun, the preview shows it.
11. V-09: `st.rerun()` after a successful summary compute only.
12. V-10: the Options inputs and the Run button move into `st.form("sql")`
    with `st.form_submit_button("Run", type="primary")`; Clear stays
    outside the form; test: set the timeout to 1 and submit in one AppTest
    run, the slow query reports `timeout` after about 1 s.
13. V-11: the kept widgets use `persist_state="session"`; `common.keep` and
    `KEPT_KEYS` go away; test: values survive a visit to another page
    (AppTest can run the three page scripts in turn against one
    `session_state`; if it cannot, keep `keep()` and record why in
    DECISIONS).
14. V-12: Prepare CSV/Parquet are disabled while the editor text differs
    from the shown result's SQL, with a caption saying so.

Documentation: the six corrections of review Section 4 (ARCHITECTURE
Section 7 kept keys, LIMITS 23 wording, D-024 wording, `#
TUNING(timeline-padding)` marker and row, LIMITS lines for the preview
cache and the temp directory, README line for `PYTHONUTF8=1`); CHANGELOG
0.2.1 entry listing the fixes by review ID; versions 0.2.1 in the three
`pyproject.toml` files and both `__init__.py`.

### Phase 1: plot data (`plotdata.py`)

```python
class PlotSpec(BaseModel): ...                     # Section 3.1
@dataclass
class PlotPlan:
    strategy: str; bucket_seconds: float | None; bucket_width: float | None
    sample_rows: int | None; series_kept: list[str]; folded_series: int
    x_expr: str; x_type: str | None; rows_scanned: int

def validate_spec(con, db_name, spec) -> dict[str, str]        # column -> type, raises QueryError
def plan_plot(spec, *, rows, span_seconds, n_series, series_top) -> PlotPlan
def build_sql(spec, plan, db_name, column_types) -> str
def fetch_plot_data(db_path, spec, config) -> PlotData
BUCKET_LADDER_SECONDS = [1, 5, 15, 30, 60, 300, 900, 1800, 3600, 10800, 21600, 43200, 86400, 604800, 2592000, 7776000, 31536000]
```

Rules:

1. All identifiers through `quote_ident`; the filter is inserted as `WHERE
   (<where>)` verbatim; every other value is a literal built from validated
   numbers; data values never enter the SQL text (series folding uses the
   CTE of Section 3.2). The `(row order)` x is `row_number() over ()`
   computed in a subquery (`FROM (SELECT row_number() over () AS rn, *
   FROM <table> WHERE (<filter>)) q`) before any bucketing, so the
   `GROUP BY` sees a plain column.
2. `fetch_plot_data` runs at most three queries: the validation lookup
   (`duckdb_columns`), the preliminary counts (`count(*)`, `min(x)`,
   `max(x)`, `count(distinct color)` when `color` is set, and `min(y)`,
   `max(y)` for every y column when the kind is histogram, all in one
   statement), and the final data query. All under the timeout.
3. Buckets are labelled by their start; `bucket` text uses the ladder's
   human names (`15 minutes`, `1 day`).
4. Histogram bins are computed per y column from that column's min and
   max after the filter; a constant column gives one bin.
5. Bar categories: `x` of any type; the measure is `agg(y1)` for the first
   y column (further y columns are extra measures in extra columns) or
   `count(*)` when `y` is empty; ordering by the first measure descending.
6. `PlotData.rows` values are Python objects as DuckDB returns them
   (datetime for buckets, floats, Decimals); the UI converts.

Tests (`test_plotdata.py`, on a generated table with 200,000 rows:
TIMESTAMP `ts`, DOUBLE `x`, `y`, VARCHAR `g` with 3 values, SMALLINT `cat`
with 7 values, a DATE and a TIMESTAMPTZ column): plan choices for each
kind under and over the budget; ladder choice for a given span and budget;
series folding with `max_series=2`; bar folding with `max_categories=3`;
histogram bin count and coverage (bins sum to `rows_scanned`); filter
applied; unknown column and non-numeric y rejected as `sql`; DATE and
TIMESTAMPTZ x; row-order x; timeout honoured; the final query never returns
more than the budget.

### Phase 2: CLI (`cli_plot.py`)

```
duckfleet plot ROOT DATABASE SCHEMA.TABLE --kind line|scatter|histogram|bar
               [--x COL] [--y COL[,COL...]] [--color COL] [--where EXPR]
               [--max-points N] [--bins N] [--agg mean|sum|min|max|count]
               [--format csv|json] [--show-sql] [--timeout S] [--cache-dir DIR]
```

1. Prints the `PlotData` rows as CSV or JSON (through the UTF-8 output of
   Phase 0 item 3) and a one-line summary on stderr (strategy, bucket,
   points, rows scanned, elapsed); `--show-sql` prints the final query on
   stderr too.
2. No HTML export: core must not import the UI package (CLAUDE.md rule 2),
   and the chart code lives there. Listed in Section 9 as an open option.
3. Exit codes as `sql`: 0, 1 usage (bad spec), 2 fatal, 3 query error.

Tests: each kind on the demo fleet's `events` table, `--format json`,
`--show-sql`, exit codes.

### Phase 3: Plot page (`charts.py`, `views/plot.py`, Database button)

1. `charts.py` (pure functions, no widgets): `palette(mode) -> list[str]`,
   `template(mode) -> dict` (Plotly layout template with surfaces, grid,
   axis, font), `figure(data: PlotData, mode: str, layout: Literal["one",
   "panels"]) -> plotly.graph_objects.Figure`, `needs_panels(data) -> bool`
   (ratio rule of Section 3.4 item 2), `to_frame(data) -> pandas.DataFrame`
   (table view and CSV). Series order and colours follow Section 3.4.
2. `views/plot.py` `render()`:
   - database and table selectors (shared keys `selected_rel_path`,
     `selected_table`); a missing or changed file is handled as on the
     Database page;
   - an `st.form("plot")` with: kind radio; x selectbox (temporal columns
     first, then numeric, then `(row order)`; hidden for histogram; any
     column for bar); y multiselect (numeric columns; preselects the first
     numeric column; for bar optional); colour selectbox (`(none)` then
     columns with `approx_distinct <= 50` from the catalog first, then the
     rest); filter text input with the placeholder `value > 0 AND run_id =
     3`; expander Options with max points (100 to 500,000, default 50,000),
     bins (5 to 500), aggregation, layout radio (auto, one axis, one panel
     per column), series cap; submit button **Plot**;
   - on submit: `queries.plot_data(abs_path, st_size, st_mtime_ns,
     spec_json)` (`st.cache_data`, keyed by the live stat and the spec) ->
     `charts.figure` -> `st.plotly_chart(fig, theme=None, width="stretch")`;
   - caption: strategy in words ("50,000 of 1,234,567 rows: bucketed to 15
     minutes, mean with min to max band", "reservoir sample of 50,000 rows",
     "3 of 11 series shown, 8 folded into (other)"), elapsed; an expander
     **Plotted data** with the table and a **Download CSV** button; an
     expander **SQL** with the final query;
   - `QueryError` shown with the same kind hints as the SQL page; a plot
     with zero rows says so.
3. `views/database.py`: button **Plot this table** next to **Open in SQL**
   that sets `selected_table` and `plot_prefill` (`x` = first temporal
   column, `y` = first numeric column) and switches to the Plot page.
4. `nav.py` gains the page; the Overview's selected database flows through
   as before.

Tests (`test_charts.py`: figure trace counts, colours by series order,
`(other)` grey, panels decision, band traces present for buckets, one
y-axis only; `test_ui_plot.py` with `_plot_app.py` against a scanned demo
fleet: submit a line plot of `events` with max points 100 (forces
bucketing on 1,000 rows), assert the caption mentions a bucket and the
figure has traces; histogram and bar submissions; a bad filter shows a
`sql` error; the Database page button sets the prefill).

### Phase 4: tests, docs, acceptance

1. `docs/FEATURES.md` (F-PLOT-01..03, F-UI-03 to `done v0.3`),
   `docs/LIMITS.md` (L-11, L-18 to L-21 confirmed), `docs/TUNING.md`
   (`bucket-ladder`, `series-cap`, `panel-ratio` markers and rows),
   `docs/ARCHITECTURE.md` (plot data module, charts module, four pages),
   `docs/DECISIONS.md` (TBC outcomes), `CHANGELOG.md` 0.3.0 with the
   acceptance results, `README.md` (the `plot` command and the page).
2. Versions 0.3.0 everywhere.

## 6. Cross-cutting rules (in addition to v0.1 and v0.2)

1. The browser never receives more than `max_points` points; every
   reduction happens in DuckDB under the timeout.
2. No dual y axis anywhere; several scales mean panels.
3. Colours come from `charts.palette` only; no Plotly default colorway, no
   generated hues; `(other)` is grey.
4. Every chart has its table view and CSV download.
5. The filter is the only free SQL text in the plot queries and runs on
   the sandboxed connection like the SQL page.

## 7. Windows and Linux

Unchanged. The CLI `--html` path is written with `Path.write_text(...,
encoding="utf-8")`.

## 8. Acceptance checklist (v0.3 done when all are ticked)

1. CI green on Windows and Linux for the 0.2.1 commit and every v0.3 phase
   commit.
2. Phase 0: a spilling query writes nothing beside the scanned file; a
   timeout during a fetch is `timeout`; CSV output of `日本語 é` round-trips
   through a subprocess as UTF-8; the SQL page honours a timeout of 1 s set
   together with Run; the preview shows a row inserted after the scan.
3. `uv run duckfleet plot ./demo-fleet exp_01/run_01/metrics.duckdb
   main.events --kind line --x ts --y value --max-points 100 --format csv`
   prints bucketed rows (fewer than 100) and the stderr summary names the
   bucket; `--show-sql` prints the query.
4. UI on Windows (recorded in the changelog) and through AppTest in CI:
   Plot page line chart of `events` raw (1,000 points), the same with max
   points 100 showing buckets and the band, a scatter with colour by
   `run_id`, a histogram of `value`, a bar of `config.key` counts; the
   table view and the CSV download work; a bad filter shows the `sql`
   message; **Plot this table** on the Database page lands on the Plot page
   with the table preselected.
5. `make-demo --big` (a new flag; without it the demo fleet and its
   numbers stay as they are) adds `exp_04/run_01/metrics.duckdb` with
   200,000 `events` rows over 30 days; a line plot of it with the default
   budget finishes under the timeout and the caption reports the bucket
   width.
6. Docs updated and versions bumped to 0.3.0.

## 9. Not in v0.3 (do not build)

Cross-database overlays (v0.4), Datashader or WebGL rendering, saved plot
specs, annotations, log axes (open option), a standalone HTML export of a
chart from the CLI (open option; it would need the chart code outside the
UI package), secondary y axis (never),
interactive zoom-to-refetch (open option: zooming refetches finer buckets),
plot export as PNG from the server (the Plotly toolbar's camera button
covers the browser side).

## 10. Open items for the implementer (TBC)

1. Theme detection: `st.context.theme.type` exists in recent Streamlit
   versions; verify on 1.66. If absent, default to light and add a sidebar
   radio "Chart theme: light / dark" (remember it in session state).
2. `st.plotly_chart(theme=None)` keeps the figure's own template; confirm
   the series colours are not repainted.
3. If `persist_state="session"` cannot replace `common.keep` under AppTest
   (Phase 0 item 13), keep `keep()` and say why in DECISIONS.
4. The `(row order)` x on a very large table: `row_number() over ()`
   forces a full scan before the bucket; acceptable under the timeout, but
   if it proves slow, bucket by `rowid` instead (DuckDB exposes `rowid` on
   base tables) and note it.
5. Histogram of a column with NULLs: NULLs are excluded from bins and the
   caption says how many rows had no value.
