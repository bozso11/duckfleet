# Review of v0.3.0 against the plan

Date: 2026-11-08. Reviewed commits: `ab4e720` (0.2.1) to `a083801`
(0.3.0). Reviewer: the planning session (Opus-class, decision D-012), with
two independent read-and-reproduce passes (core; UI and documentation).
Reference: `docs/PLAN_v0.3.md` and `docs/REVIEW_v0.2.md`.

## Verdict

0.2.1 fixes every finding of the v0.2 review, and v0.3.0 implements the
plan faithfully: the plot data layer reduces inside DuckDB exactly as
specified, the chart rules hold (fixed palette, one y axis, band trace
order, legend rule, table view), and the acceptance checklist is true on
re-check. The review found 17 confirmed defects. One is severe and comes
from the 0.2.1 temp-directory fix itself (W-01: two spilling connections in
one process share a temp folder and crash the process); the rest are
medium or low. All fixes go into Phase 0 of `docs/PLAN_v0.4.md` and ship as
0.3.1.

## 1. Verified by the reviewer, not self-reported

1. CI: all six commits (0.2.1 and Phases 1 to 4 plus the changelog note)
   green on `ubuntu-latest` and `windows-latest`.
2. 585 tests pass and ruff is clean on Windows (Python 3.11.9, duckdb
   1.5.6, streamlit 1.66.1, plotly 7.1.0).
3. Fresh demo flock with `--big`: 17 walked, 14 candidates, 2 rejected, 3
   failed; `plot` buckets 1,000 rows to 3 hours (56 points) under a budget
   of 100 and 200,000 rows to 1 minute (43,200 points) under the default
   budget in 0.06 s; histogram and bar output as planned; a bad filter
   exits 3 as `sql`; the temp directory is a per-process folder under the
   cache.
4. UI on Windows in a browser: Plot page raw line (1,000 points), bucketed
   line with the band and the caption "56 points from 1,000 rows: bucketed
   to 3 hours, mean with min to max band", histogram with 50 bins; the
   form committed a typed point budget together with the Plot click; no
   browser console errors.
5. Conformance read of `plotdata.py`, `charts.py`, `views/plot.py`,
   `tempdir.py` and the 0.2.1 diffs against the plan.
6. W-01 and W-02 reproduced by the reviewer; the others by a review pass
   with a script or an AppTest.

## 2. Confirmed findings

Severity: high = a crash or a rule violation a real flock can hit; medium
= a wrong result or message in a plausible situation; low = cosmetic,
hygiene or documentation.

| ID | Sev. | Where | Claim | Scenario | Fix |
|---|---|---|---|---|---|
| W-01 | high | `tempdir.process_temp_dir`, `query.connection_config`, `profiler._profile` | Two DuckDB instances in one process share `<cache>/tmp/<pid>` and both write `duckdb_temp_storage_DEFAULT-0.tmp`; when both spill at once the process dies | Two threads, two files, `memory_limit_mb=64`, one 12 M-row sort each: the process dies (an access violation in the review pass, 2 of 2 runs; a segmentation fault reported by the shell with no output in the reviewer's run); separate folders give correct results. The Streamlit server is one process serving every browser tab | Per-file leaf under the per-process folder: `<cache>/tmp/<pid>/<sha1(abs path)[:16]>`, derived identically by `open_readonly`, the profiler and the catalog writer (one config per file per process, D-021 holds); keep the pid sweep and the atexit removal; add a two-thread, two-file spill test |
| W-02 | medium | `plotdata.choose_bucket_seconds`, `_fetch_cap` | When even the widest ladder width (365 days) exceeds the budget, the data query returns more rows than the budget and the user sees "internal error in the plot plan" | 200 yearly rows over 200 years with a budget of 100: 101 rows for 100 (reproduced); plan 3.2 says the guard passes | Above the ladder derive a width that fits (`INTERVAL '<n> days'`, label "N days") and make the test assert the branch |
| W-03 | medium | `charts.series_color`, `views/plot.py` y multiselect, `PlotSpec.y` | More than eight y columns draw the extra ones in the same grey as `(other)`, with separate legend entries | A table with ten numeric columns, all selected | `PlotSpec` validator `len(y) <= 8` (shared by page and CLI) and `max_selections=8` on the multiselect; a clause in L-19 |
| W-04 | medium | `views/plot.py` request matching | Switching the chart kind leaves the previous kind's chart and caption on screen until Plot is pressed | Plot a line of `events`, click `bar` | Store `kind` in the request and require it to match, or clear `plot_request` when the kind changes |
| W-05 | low | `plotdata._sql_bar` | A table with a column named `df_c` or `df_rk` makes the bar query ambiguous | Those exact column names | Alias the source subquery and qualify the join columns |
| W-06 | low | `PlotSpec.seed` | A seed outside 0 to 2^31-1 is a parser error at `REPEATABLE` | `seed=-5` from the CLI | `Field(ge=0, le=2**31 - 1)` |
| W-07 | low | `query.classify_error` | Any DuckDB error whose text contains `interrupt error` becomes `timeout`, so SQL text can still set the kind | `select 'interrupt error'::INTEGER` is a timeout | Apply the substring to `InvalidInputException` only (the V-02 case); add the string to the V-06 test list |
| W-08 | low | `plotdata._literal` | An infinite x or y span raises a bare `ValueError` (traceback in the CLI, uncaught in the page) | A column holding -1.7e308 and 1.7e308 | Treat a non-finite span like the zero-span fallback, or raise `QueryError` |
| W-09 | low | `tests/test_plotdata.py`, `tests/test_query.py` | A bare `QueryConfig()` resolves its temp directory under the real user cache, so the suite writes there despite `conftest.py` | Any test run | An autouse fixture pointing `default_cache_dir` at `tmp_path` |
| W-10 | low | `plotdata.fetch_plot_data` | `database_name` and the column lookup run outside the timeout (four statements, two untimed) | Plan Phase 1 item 2 | Run them through `execute_limited(..., remaining())` or document |
| W-11 | low | `plotdata` labels | A real category or colour value spelled `(other)` or `(null)` merges with the folded tail or the NULL series | Such values in the data | A LIMITS note, or distinguishable internal labels |
| W-12 | low | `plotdata.x_expression` | A DATE beyond the TIMESTAMP range anywhere in x fails the whole plot, even `raw` | A year-1,000,000 date (artificial) | Filter such rows out with `TRY_CAST` and count them in the notes |
| W-13 | low | `charts._histogram_traces` | A two-column histogram in panels has no legend while a two-column line has one | `y=["a","b"]` histogram | Count y columns as series, or record the exception in D-038 |
| W-14 | low | `views/plot.py` histogram caption | The caption reports the requested bins and all rows, not the drawn bins and the binned rows | A constant column (one bin, caption says 50); NULLs in 600 of 6,000 rows | Count the drawn bins and subtract the note's count |
| W-15 | low | `views/plot.py` spec errors | A spec problem is shown as `sql: choose at least one y column` | Submit without a y column | `st.warning(message)` without a kind prefix |
| W-16 | low | `charts._bar_traces` | The `(other)` bar category takes the series colour when a colour column is set | Three series with folded categories | Defensible (series must stay tellable); document in D-038 |
| W-17 | low | docs, markers | ARCHITECTURE omits **Plot this table** and `common.file_state`; LIMITS 23 names only two places where the environment overrides are read; five heuristics lack `# TUNING` markers or rows (`LOW_CARDINALITY`, `_WEBGL_FROM`, `_MARKERS_UP_TO`, the 0.7 opacity above 1,000 points, `default_table`) | | Correct the texts; add rows or state in TUNING.md that D-038 fixes those values |

## 3. Plausible, not reproduced

| ID | Claim | Decision |
|---|---|---|
| P-08 | Stale `tmp/<pid>` folders are swept only by `run_scan`; a killed CLI or UI process leaves its folder until the next scan | Adopt: sweep on a process's first connection too |
| P-09 | `st.context.theme.type` may be wrong on first load and right after a theme change; the chart keeps the wrong palette until the next rerun | Document under LIMITS; the Chart theme option is the workaround |
| P-10 | `--show-sql`, the summary and notes go to stderr in the console encoding; non-ASCII identifiers print as `??` | Cosmetic; document with `PYTHONUTF8=1` |
| P-11 | The budget guard also covers histogram and bar (harmless); `PlotPlan.series_kept` is never filled | Document; remove the unused field |

## 4. Documentation corrections

W-17 above, plus LIMITS lines for W-01 (one temp folder per file per
process) and W-11, and the `(other)` colour clause of W-16 in D-038.

## 5. Phase 0 (0.2.1) completeness

Every item of `docs/REVIEW_v0.2.md` is fixed in code with a test, including
the test gaps (real cancellation with worker markers, the catalog writer's
config, the 2-worker tag run, nested-only and binary-only summaries, the
orphaned view). Two regressions came with the fixes: W-01 (the shared
per-process temp folder) and W-07 (the `interrupt error` substring applied
to every DuckDB error). V-03's UTF-8 output is tested for `sql` but only
verified by hand for `plot`.

## 6. Test gaps to close in 0.3.1

1. Two concurrent spilling connections in one process (W-01).
2. Ladder exhaustion (W-02; the existing test skips its assertion exactly
   on that branch).
3. Hostile column names in `plotdata` (`df_c`, `df_rk`, `x`, `s`, `n`,
   quotes); TIMESTAMP_S/MS/NS x; empty and single-row tables; `seed`
   bounds; `plot --format csv|json` through a subprocess; a guard that no
   test touches the real cache.

## 7. Checked and fine

1. Every scanned-file connection goes through `open_readonly` or the
   profiler's identical sandbox; spill files land under the cache and
   nothing appears beside the scanned file across five large `GROUP BY` and
   `ORDER BY` shapes; the folder is removed on normal exit; the sweep
   removes dead pids only.
2. `plotdata`: all four kinds on empty, one-row, zero-after-filter,
   all-NULL colour and all-NULL y tables; DECIMAL, HUGEINT and UHUGEINT y;
   DATE, TIMESTAMPTZ and TIMESTAMP_S/MS/NS x through every strategy; series
   folding through the CTE with no data values in SQL; histogram coverage,
   constant column, NULL/NaN/inf notes; bar `(other)` last; the sample
   taken after the filter and repeatable; the bucket bound held on 300
   random spans; `--` and `;` in the filter; the CLI exit codes and UTF-8
   output.
3. `charts`: palette and grey in every kind on both surfaces; band order per
   subplot with panels and ten series; no second y axis anywhere; the
   panel ratio strict at 100; `to_frame` equals the plotted rows; CSV UTF-8
   with LF.
4. Plot page: `plot_data` keyed by the live size and mtime; a failed request
   keeps its error; the request is hidden for another table; the
   `(row order)` x; filter errors show `sql:` and no chart; the missing file
   disables Plot; the prefill from **Plot this table** applies once.
5. 0.2.1 UI fixes: live-stat preview, rerun after a summary compute,
   `st.form` with a typed timeout honoured in one submission,
   `persist_state` everywhere with `keep()` gone, Prepare disabled when the
   editor differs; the review's documentation corrections applied.
6. No module-level UI imports in core, no `duckflock_ui` import; every
   `TUNING` marker has a row; versions 0.3.0 everywhere; README commands
   exist and run.

## 8. Acceptance checklist re-check

| Item | Result |
|---|---|
| 1 CI green for 0.2.1 and every phase | Confirmed for all six commits |
| 2 Phase 0 items | Confirmed by tests and the review pass |
| 3 `plot` bucketed CSV and `--show-sql` | Confirmed by the reviewer |
| 4 UI cases on Windows | Raw and bucketed line, histogram and the form commit confirmed by the reviewer; scatter by `run_id` and bar by `config.key` through AppTest |
| 5 `make-demo --big` and the 200,000-row plot | Confirmed by the reviewer (1-minute buckets, 0.06 s) |
| 6 Docs and versions | Confirmed, with the corrections of Section 4 |
