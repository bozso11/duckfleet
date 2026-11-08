# Changelog

## Unreleased

1. `docs/PLAN_v0.3.md`: detailed plan for 0.2.1 (Phase 0) and v0.3 (Plot
   page, `duckfleet plot`, chart rules).
2. Decisions D-032 to D-034 and limits L-18 to L-21 added for v0.3.
3. `duckfleet_core.plotdata` (v0.3 Phase 1): `PlotSpec`, `plan_plot`, `build_sql`
   and `fetch_plot_data` reduce a table to a point budget inside DuckDB: time
   buckets (mean, min, max), numeric buckets, equal-width histogram bins,
   reservoir samples, top-N folding of series and bar categories; 140 tests on a
   200,000-row table. No page or command uses it yet (D-036).
4. `duckfleet plot ROOT DATABASE SCHEMA.TABLE --kind line|scatter|histogram|bar`
   prints the reduced chart data as CSV or JSON with a summary line (strategy,
   bucket, points) and, with `--show-sql`, the query on stderr; `make-demo
   --big` adds a 200,000-row table (D-037).
5. **Plot page** (v0.3 Phase 3): pick a database and table, a chart kind (line,
   scatter, histogram, bar), x, y columns, a colour column, an SQL filter and
   the point budget; the data is reduced in DuckDB, drawn with Plotly under the
   fixed chart rules (eight-colour palette in series order, `(other)` grey, one
   y axis or one panel per column, min to max band), and shown with a caption
   about the reduction, a table view, a CSV download and the SQL. **Plot this
   table** on the Database page hands the table over (D-038).

## 0.2.1 (2026-11-08)

Fixes from `docs/REVIEW_v0.2.md` (plan: Phase 0 of `docs/PLAN_v0.3.md`, decision
D-031). The demo fleet and its scan numbers are unchanged.

### Fixes, by review ID

1. **V-01** (high): DuckDB's temp directory is `<cache dir>/tmp/<pid>` on every
   connection duckfleet opens (query layer, profiler, catalog writer), no longer
   `<database path>.tmp` beside the scanned file. The folder is per process,
   created before connecting, removed at exit; `run_scan` removes folders of
   processes that no longer exist. Found on the way: DuckDB refuses the temp
   directory when `enable_external_access` is already off, so the key must come
   after it in the config dict (D-035). Test: a spilling query with
   `memory_limit_mb=64` while a thread lists the scanned folder.
2. **V-02**: an interrupt during the fetch of VARCHAR or list columns is a
   `timeout` (5 of 5 runs), not an `sql` error.
3. **V-03**: `--format csv` and `json` are UTF-8 with LF line ends whatever the
   console code page (tested through a subprocess with a latin-1 stdout);
   `PYTHONUTF8=1` is documented for `table` (README, LIMITS 26).
4. **V-04**: Ctrl-C during a query interrupts it; the timeout waits in 0.25 s
   slices because a Windows lock wait is not interrupted by Ctrl-C.
5. **V-05**: the Database page's preview cache is keyed by the file's live size
   and mtime; a row inserted after the scan shows in the preview.
6. **V-06**: `classify_error` decides by exception type first, so text echoed from
   the SQL (`select 'read-only mode'::INTEGER`) is an `sql` error.
7. **V-07**: JSON output has no `NaN` (null), BLOBs as hex, tuples as lists; CSV
   renders arrays and structs as JSON text.
8. **V-08**: the unreachable `(no result set)` branch is gone.
9. **V-09**: the Summary tab shows "Recompute" in the run that computed the
   summary (`st.rerun()` after a successful first compute only).
10. **V-10**: on the SQL page the Options inputs and **Run** share a form, so Run
    submits what was typed.
11. **V-11**: `persist_state="session"` replaces `common.keep` / `KEPT_KEYS`
    (every kept widget supports it, checkboxes included); a test visits the
    three pages in turn against one session state.
12. **V-12**: **Prepare CSV** and **Prepare Parquet** are disabled, with a
    caption, while the editor text differs from the query behind the result.
13. **P-05**: the profiler's connection has the query layer's sandbox
    (external access off, configuration locked), threads and memory aside.
14. **P-07**: the `get_or_compute` docstring is correct.

### Tests added (review Section 6)

Timeout during a VARCHAR/list fetch; nothing beside a scanned file under memory
pressure; non-ASCII CLI output as bytes; a real cancellation test for P-02 (a
marker file written inside the spawned workers counts the files that really
ran: at most 8 of 20 after the consumer stops, 20 without `cancel_futures`); the
catalog writer's config (R-07); a 2-worker run with a valid tag pattern that
matches nothing (R-03); summaries of a nested-only table, a binary-only table
and a view whose base table was dropped; the stale-folder sweep and the
workers' own clean-up.

### Documentation

ARCHITECTURE (tempdir module, storage layout, shared config rule, UI state),
LIMITS 23 (wording) and new 24 to 26, L-14 (interrupt delay, P-06), D-024
wording and D-035, `# TUNING(timeline-padding)` marker and row, README
(`PYTHONUTF8=1`). The CI result of the 0.2.0 commit below was confirmed green
on both OSes afterwards (run 42218976791).

## 0.2.0 (2026-11-06)

Database deep dive and read-only SQL on top of the fleet overview (plan:
`docs/PLAN_v0.2.md`), after the 0.1.1 review fixes below.

### What it does

1. **Database page** (`duckfleet-ui`): pick one database of the scanned
   fleet; its tables with row counts and time windows; per table the columns
   with the scan's cheap statistics, a per-column summary (DuckDB
   `SUMMARIZE`, computed on request, cached as JSON sidecars until the file
   changes) and a paged, sortable preview with a column selector. **Open in
   SQL** hands a query to the SQL page.
2. **SQL page**: a read-only, sandboxed editor on one database (no writes, no
   file access, no extensions, no `SET`), a row limit, a timeout enforced with
   `interrupt()`, CSV and Parquet downloads and a history of 20 queries.
3. **CLI**: `duckfleet sql`, `summarize`, `preview`.
4. **Core**: `duckfleet_core.query` (sandboxed connections, `run_query`,
   `export_query`, `preview_table`), `duckfleet_core.summary`,
   `store.SummaryStore`; `pytz` is a core dependency, `pyarrow` the optional
   extra `export`.

### Acceptance checklist (plan Section 8)

1. [x] CI green on Windows and Linux for the 0.1.1 commit (`dbfd7bf`) and for
   every v0.2 phase commit (`adb4804`, `fe6ffea`, `506dcdf`, `5a742db`,
   `2cbcb14`); the run of the 0.2.0 commit itself (42218976791) was green on both
   OSes, confirmed after the entry was written.
2. [x] Phase 0: the demo scan reports 16 walked, 13 candidates, 2 rejected, 10
   ok, 3 failed (`corrupt`, `corrupt`, `encrypted`), families unchanged (checked
   with the CLI on a fresh demo fleet); the `infinity` test passes
   (`test_infinite_timestamps_never_open_a_time_window`); `duckfleet scan
   --tag-pattern "("` exits 1 with `invalid tag pattern '(': missing ),
   unterminated subpattern at position 0`; the AppTest with 72 patched CPUs
   renders (`test_the_page_renders_on_a_machine_with_72_cpus`).
3. [x] `duckfleet sql ./demo-fleet exp_01/run_01/metrics.duckdb "select
   count(*) from events"` prints 1000; an `INSERT` exits 3 with `read_only`; a
   `COPY ... TO` exits 3 with `blocked` and writes no file.
4. [x] `duckfleet summarize ./demo-fleet exp_01/run_01/metrics.duckdb
   main.events` prints 3 column rows with min, max, avg and quantiles; the
   second call prints `(cached <time>)`; `--refresh` recomputes.
5. [x] `duckfleet preview ... main.events --rows 5 --offset 5 --order-by ts
   --desc` prints 5 rows in descending time order.
6. [x] UI. Windows, by hand in the built-in browser with `duckfleet-ui` on the
   demo fleet: Overview, Load existing catalog, Database page for
   `exp_01/run_01/metrics.duckdb` with 2 tables, the Columns tab (null share
   0 %), a Summary that was computed (and recomputed) and shows the same
   `computed` time after leaving and returning to the page, a Preview sorted
   by `ts` descending with a column subset; **Open in SQL** landed on the SQL
   page with `SELECT * FROM "main"."events" LIMIT 100` for the same database;
   max rows 5 gave the cut warning with 5 rows; **Prepare CSV** and **Prepare
   Parquet** offered downloads of 4,809 and 2,654 bytes (100 rows); an
   `INSERT` showed the `read_only` message; the cross-join with a timeout of
   1 s showed the `timeout` message and the next query returned 1000. The
   browser's save of a download was not clicked; the Parquet bytes are read
   back in the AppTest. Linux: through AppTest in CI on Ubuntu
   (`test_ui_database.py`, `test_ui_sql.py`), no manual session.
7. [x] `TIMESTAMP WITH TIME ZONE` columns fetch, preview and query without
   error (`test_result_columns_types_and_rows`,
   `test_preview_of_a_timestamptz_table`, `test_timestamptz_values_display`).
8. [x] `FEATURES.md`, `LIMITS.md`, `TUNING.md`, `ARCHITECTURE.md`,
   `DECISIONS.md`, `ROADMAP.md`, `CHANGELOG.md`, `README.md` updated; versions
   0.2.0.

### Deviations from the plan

1. `TimeZone` is not set on the query connection: the connect config does not
   accept it and a `SET` before locking reaches only the first connection of
   an instance (D-026). `to_arrow_table` has a `fetch_arrow_table` fallback.
2. The CLI writes CSV and JSON from the fetched rows (no pyarrow), `preview`
   shows 20 rows by default, and an unknown database or table is exit 1
   (D-027).
3. The Database page uses lazy tabs, which needs `streamlit>=1.66` (D-028);
   `st.switch_page` works under `st.navigation`, so `st.page_link` is only used
   for the way back to the Overview.
4. SQL page: the row limit input accepts 1 to 100,000 (the plan says 100, its
   acceptance case needs 5), the downloads export the text in the editor
   (D-029); the database selectbox lists databases with tables first.
5. Values the grid cannot hold are converted to text in Python, not cast in the
   preview SQL (D-030).
6. A CSV download of a result with nested columns fails with a hint to use
   Parquet (LIMITS 21).

### Open after 0.2.0

1. Linux: no manual browser session, only AppTest in CI (WSL has no uv).
2. The numbers marked (TBC) in `docs/LIMITS.md` and the tuning points are
   untested on a real fleet; summaries of tables above the sampling threshold
   are tested with a low threshold only.
3. `incompatible_version` is still classified by message only.
4. UI details: the Summary tab shows "Compute summary" until the next rerun
   after a first computation; the selected tab and the preview inputs are not
   kept when leaving the Database page.
5. No LICENSE file (D-014), owner decision.

### Build log by phase

### Phase 5: SQL page

1. The SQL page (`views/sql.py`): the database selectbox shared with the other
   pages, an expander **Schema** (tables, columns, types from the catalog), the
   editor, **Run** and **Clear**, an expander **Options** (max rows shown, timeout
   in seconds), a result grid with a caption (seconds, rows shown, number of
   statements) and a warning when the result was cut. Errors show the kind and
   DuckDB's message with a hint: `read_only` (duckfleet opens databases
   read-only), `blocked` (file access, extensions and settings are disabled),
   `timeout` (raise the timeout or narrow the query).
2. **Prepare CSV** and **Prepare Parquet** run the text in the editor through
   `export_query` (up to 1,000,000 rows, built in memory) and offer a download
   button named `<database stem>_<yyyymmdd-hhmmss>.<ext>`; a truncated export
   warns with the cap, and a prepared file is only offered while the editor
   still holds the text it was made from.
3. A history of the last 20 distinct queries (newest first), loaded into the
   editor from a selectbox. The last result and the prepared download live in
   the session state; the result is cleared when the database changes.
4. Defaults of the database selectbox: databases with tables first, empty ones
   next, failed ones last (the first entry is shown by default).
5. Choices the plan left open are in D-029.
6. Checked by hand on Windows in the built-in browser: no-catalog state with a
   link to the Overview, a syntax error, max rows 5 with the cut warning, a
   prepared CSV.

### Phase 4: Database page

1. `st.navigation` now has three pages (`nav.py`): Overview, Database, SQL.
   The Database page (`views/database.py`) shows one database of the scanned
   fleet: header (size, families, tags, time window, health), the tables with
   row counts and time windows, and three tabs: **Columns** (scan statistics
   with the null share), **Summary** (a cached `SUMMARIZE`, computed on
   request, **Recompute** afterwards) and **Preview** (column selector, sort,
   descending, page number, one page of `preview_rows` rows). **Open in SQL**
   sets `sql_text` and switches to the SQL page.
2. A database that failed in the scan shows its error and stops; a file that
   vanished since the scan is explained instead of failing; a file whose size
   or mtime differ from the catalog is marked "changed since the last scan".
3. `query_access.py` (one `QueryConfig` per process, optional env overrides
   `DUCKFLEET_QUERY_THREADS` and `DUCKFLEET_QUERY_MEMORY_MB`; cached preview
   pages; summary helpers; result frames that never break the grid) and
   `common.py` (the helpers the Overview had, plus keeping widget state across
   pages). `views/sql.py` is a stub until Phase 5.
4. `CatalogReader.database(rel_path)`.
5. `streamlit>=1.66` (lazy tabs, D-028); `uv.lock` refreshed. The Overview's
   Workers and Max files defaults live in the session state.
6. Checked by hand on Windows in the built-in browser (`duckfleet-ui`):
   Overview to Database to SQL through `st.switch_page`, the database
   selection survives the page changes, the summary computes and shows.

### Phase 3: CLI

1. New commands `duckfleet sql ROOT DATABASE SQL`, `summarize ROOT DATABASE
   SCHEMA.TABLE` and `preview ROOT DATABASE SCHEMA.TABLE` (module
   `cli_query.py`). `DATABASE` is the `rel_path` listed by `report`; the
   absolute path comes from the catalog. Output as an aligned table, CSV or
   JSON lines. Exit codes: 0 ok, 1 usage, 2 fatal, 3 query error (kind and
   message on stderr).
2. `summarize` prints `(computed in N s)` or `(cached <computed_at>)` on
   stderr; `--refresh` recomputes.
3. Choices the plan left open are in D-027.

### Phase 2: summaries

1. `duckfleet_core.summary`: `ColumnSummary`, `TableSummary` (schema version
   1) and `summarize_table` (DuckDB `SUMMARIZE`, on a sample above
   `stats_row_threshold` rows, under the query timeout). A failure becomes a
   summary with `error` set instead of an exception.
2. `store.SummaryStore`: JSON sidecars
   `roots/<root_key>/summaries/<file_key>/<table_key>.json`, valid while the
   database file's size and mtime, the summary schema version and the DuckDB
   version match (D-020); `get_or_compute(..., refresh=False)`. Error
   summaries are saved but never reused. `ProfileStore.prune` removes the
   summaries folder of every pruned profile.
3. The helpers `as_query_errors`, `database_name` and `execute_limited` of
   `query.py` lost their underscore (shared with `summary.py`).

### Phase 1: query layer

1. `duckfleet_core.query`: `QueryConfig`, `connection_config`, `open_readonly`
   (read-only, extension autoload off, external access off, configuration
   locked, fixed threads and memory), `run_query`, `export_query` (CSV and
   Parquet in memory through Arrow), `preview_table`, `table_columns`,
   `QueryResult`, `QueryError` (kinds `sql`, `read_only`, `blocked`, `timeout`,
   `other`). Every query runs under a timeout enforced with `interrupt()`.
2. `sqlutil.py` holds `quote_ident` and `qualified_name` (moved out of
   `profiler.py`).
3. `pytz>=2024.1` is a core dependency (D-022) and `pyarrow` the optional
   extra `export`; `uv.lock` refreshed. `TIMESTAMP WITH TIME ZONE` rows now
   fetch.
4. Outcomes of the plan's open items: `TimeZone` is not accepted in the connect
   config and a `SET TimeZone` before locking only reaches the first
   connection of a process, so the session zone stays the machine's (D-026);
   `to_arrow_table` is used with a `fetch_arrow_table` fallback.
5. A CSV export of a result with list, struct or map columns fails with a
   message that points to Parquet (the Arrow CSV writer has no nested types).

## 0.1.1 (2026-11-05)

The fixes of `docs/REVIEW_v0.1.md` (Phase 0 of `docs/PLAN_v0.2.md`); no new
features. Review IDs in brackets.

### Fixed

1. [R-01] A file with the DuckDB magic followed by garbage (a storage version
   above 2^63 - 1) made the catalog build fail, and every later scan of that
   root failed the same way. `databases.storage_version` is now `UBIGINT` and
   `read_header` reports no version for such a value. New demo file
   `broken/highbit.duckdb`, profiled as `corrupt`.
2. [R-02] `infinity` and `-infinity` in TIMESTAMP, DATE and TIMESTAMPTZ
   columns no longer open a time window of year 1 to year 9999: the temporal
   `min`/`max` skip non-finite values (exact and sampled query), and
   `_as_datetime` returns `None` for `datetime.min/max` and `date.min/max`.
3. [R-03] An invalid tag regex killed the scan. `ScanConfig.tag_patterns` is
   validated (it must compile and have the named groups `key` and `value`);
   `profile_database` turns an invalid config into an error profile and falls
   back to no tags when tag extraction itself fails. New CLI option
   `scan --tag-pattern REGEX` (repeatable, replaces the defaults); an invalid
   pattern exits 1 with a message naming it.
4. [R-04] On a machine with 64 or more CPUs the default worker count exceeded
   the UI input's maximum (61) and the page raised. `resolved_workers()` is
   capped at `MAX_POOL_WORKERS`.
5. [R-05] A temporal value outside Python's datetime range (for example year
   10000) silently dropped out of the time window. The table now carries the
   error `temporal value outside the supported range in column <name>`, its
   window is cleared, and the database gets `table_error:<schema>.<table>`.
6. [R-06] The cache directory is no longer walked, so a `--cache-dir` inside
   the root does not turn the catalog into a fleet member.
7. [R-07] The catalog writer connects with the standard settings (2 threads,
   1 GB, extension autoload off).
8. [R-08] The Overview timeline draws a single-instant window with a minimum
   width (the larger of 1 hour and 0.5 % of the fleet span), the hover text
   says "single instant", and the caption lists those databases.
9. [R-09] A scan subprocess that exits 0 without a `finished` event is
   reported as such, and stray stdout lines appear in the log expander.
10. [R-10] Removed the vacuous assertion in `test_catalog.py`.
11. [P-01] Cached profiles are reused only when `duckdb_version` matches the
    installed DuckDB (one full re-profile after an upgrade).
12. [P-02] The process pool is shut down with `cancel_futures=True` in a
    `try/finally`, and `run_scan` closes the profile stream, so an error in
    the consumer or Ctrl-C does not profile every queued file first.

### Documentation corrections

1. [R-11] An `encrypted` fixture is producible: the demo fleet has
   `secure/encrypted.duckdb` and a test asserts `error.type == "encrypted"`.
   D-018, F-SCAN-09 and the 0.1.0 open item 1 are corrected.
2. [R-12] `docs/LIMITS.md` L-02 and `docs/ARCHITECTURE.md` Section 3 state the
   cap of 61 workers.
3. [R-13] The plan's Phase 4 test text (a second run reuses every profile)
   cannot hold for the demo fleet because failed files are retried; the test
   asserts 10 reused and the failed files re-profiled, with no timing (noted
   under 0.1.0 deviations).
4. [R-14] `docs/ARCHITECTURE.md` Section 4: cache path wording (D-015) and
   `catalog.duckdb.tmp`.
5. [R-15] F-SCAN-10: reuse also compares the profile schema version (and,
   since P-01, the DuckDB version).
6. [R-16] `docs/ARCHITECTURE.md` Section 7: the sidebar description; the UI
   package description no longer lists pages that do not exist yet.
7. [P-03], [P-04] `docs/LIMITS.md` 15 (profiles with table errors are reused
   until the file changes) and 5 (a click during a scan).
8. `docs/REVIEW_v0.1.md`, `docs/PLAN_v0.2.md`, decisions D-019 to D-024, limits
   L-09 revised and L-14 to L-17 added (written before the fixes).

### Demo fleet

The demo fleet now has 16 files walked, 13 candidates, 2 rejected, 10 ok and
3 failed (`corrupt` twice, `encrypted`); families unchanged. Two files were
added (`broken/highbit.duckdb`, `secure/encrypted.duckdb`; the latter leaves
no `.wal` sibling). Every test and document that stated the old numbers (14,
11, 1 failed) is updated.

### Deviations from the plan

1. The CLI option `--tag-pattern` is new: the acceptance item "an invalid tag
   pattern exits 1" needs a way to pass a pattern from the command line
   (D-025).
2. A config dict that does not validate used to fall back to the defaults in
   `profile_database`; it now gives an error profile (D-025).
3. When several columns of a table hold unrepresentable temporal values the
   error text names all of them (`... in columns a, b`).

## 0.1.0 (2026-11-04)

First usable version: scan a folder tree of DuckDB files and explore it as
one fleet (plan: `docs/PLAN_v0.1.md`).

### What it does

1. `duckfleet scan ROOT`: finds every DuckDB file (extension plus header
   magic, so SQLite files are rejected), profiles each one read-only in a
   spawn process pool (`max(1, cpu_count - 2)` workers, one DuckDB thread and
   a memory cap each), writes one JSON profile per file into the user cache
   and builds a DuckDB catalog from the profiles. Rescans reuse the profiles
   of unchanged files and retry failed ones.
2. `duckfleet report ROOT` (text or Markdown), `duckfleet make-demo DIR`,
   `duckfleet paths ROOT`.
3. Schema fingerprints (strict: names and types, loose: names only, column
   order ignored), families, per-table variants, a diff of two databases or
   families, time windows, health flags, classified errors, path-token tags.
4. `duckfleet-ui`: Streamlit Overview page (sidebar scan, metrics, fleet
   table, families and matrix, table variants, diff, timeline, health,
   rejected files).

### Acceptance checklist (plan Section 8)

1. [x] CI green on Windows and Linux for every phase commit (1 to 6); the
   result for the 0.1.0 commit is in the GitHub Actions run of that commit.
2. [x] `make-demo` + `scan` of the demo fleet: 14 files walked, 11
   candidates, 2 rejected, 10 ok, 1 failed (`corrupt`); a second scan reuses
   10 profiles and retries the failed one (checked with a fresh cache
   directory on Windows, also asserted by `test_cli.py` and
   `test_coordinator.py` on both OSes).
3. [x] `report`: four strict families (3, 3, 2, 1 members), three loose
   families (6, 2, 1), `events` with 3 variants, time windows in order,
   `wal_present` and `empty_database`, the `corrupt` error, the 2 rejected
   files.
4. [x] Lock test (`test_profiler.py`, also end to end in
   `test_coordinator.py`): a file held by a writer process is `locked`, green
   on Windows and Linux CI.
5. [x] Windows: `duckfleet-ui`, demo fleet scanned from the sidebar in the
   built-in browser: progress visible, all sections rendered without an
   exception (8 metrics, 10 tables, the timeline with 4 family traces), a
   full rescan (11 profiled) and a cached load in a fresh session worked.
   Linux: no manual browser session was possible; the same flow (Scan, full
   rescan, cached rescan, every section) runs as an AppTest with a real scan
   subprocess in CI on Ubuntu.
6. [x] Scan of a folder with a space and non-ASCII characters
   (`données été/flotte démo 1`) from the UI on Windows; also tested in the
   CLI, coordinator and scan-runner tests on both OSes.
7. [x] The default `workers` is `max(1, cpu_count - 2)` (22 on the 24-CPU
   development machine) and shows in the `finished` summary and the UI notice.
8. [x] `FEATURES.md`, `LIMITS.md`, `TUNING.md`, `ARCHITECTURE.md` (final
   catalog schema), `CHANGELOG.md`, `README.md` updated; versions 0.1.0.

Also checked: the UI tests pass with pandas 2.2.3 as well as 3.1.1.

### Deviations from the plan

1. `demo.py` and `tags.py` were built in Phase 1 and the fingerprint
   primitives in Phase 2, because tests and `ScanConfig` needed them earlier
   than the plan's phase order.
2. Discovery: when the limit is hit, the first further DuckDB file is listed
   as rejected `over_max_files` and the walk stops (LIMITS L-01).
3. A file held by a writer is unreadable on Windows even for its header;
   discovery keeps it as a candidate and the profiler reports `locked`
   (LIMITS 11).
4. The catalog is filled by unnesting one list per column instead of
   `executemany` (D-016).
5. The reuse check also compares the `.wal` presence flag; `ScanSummary.workers`
   is the configured count while the pool is capped at the number of files
   (LIMITS 15, 16).
6. Timestamps with a time zone are converted to UTC in SQL (no `pytz`
   dependency, LIMITS 13).
7. The default cache directory is `user_cache_dir("duckfleet", appauthor=False)`
   (D-015); `streamlit>=1.50` (the page uses `width="stretch"`).
8. The profiler never opens a file whose header lacks the DuckDB magic, also
   when called directly.
9. The plan's Phase 4 test text (a second run reuses every profile) cannot
   hold for the demo fleet because the corrupt file is retried; the test
   asserts 10 of 11 reused and no timing (R-13).

### Open after 0.1.0

1. `incompatible_version` and `encrypted` are classified by message only; no
   fixture exists (plan Section 10 item 2). *Corrected in 0.1.1:* an
   `encrypted` fixture is producible and part of the demo fleet (R-11);
   `incompatible_version` stays unverified.
2. `st.navigation` is not exercised by AppTest; the page function is.
3. Numbers marked (TBC) in `docs/LIMITS.md` and the tuning points in
   `docs/TUNING.md` are untested on a real fleet.
4. Open options discovered while building: `docs/ROADMAP.md` items 13 and 14.

### Build log by phase

### Phase 0 (2026-11-04)

1. uv workspace with two packages: `duckfleet-core` (duckdb, pydantic,
   platformdirs, psutil) and `duckfleet-ui` (streamlit, plotly, pandas,
   pyarrow). Python 3.11 pinned. Placeholder tests and ruff pass.
2. CI on Windows and Linux (`.github/workflows/ci.yml`).
3. Documentation set: proposal, scope, feature matrix, limits, tuning,
   architecture, decisions, roadmap, and the detailed plan for v0.1.
4. Verified DuckDB 1.5.6 facts recorded in `docs/PLAN_v0.1.md` Section 4
   (header layout, error messages, locking, SQLite files opening without
   error).

No tool functionality yet.

### Phase 1: discovery (2026-11-04)

1. `config.py` (`ScanConfig` with resolved workers, memory limit, cache dir),
   `discovery.py` (walk, header sniff, WAL sibling, `max_files` truncation),
   `tags.py` (path-token tags, TUNING `tag-patterns`).
2. `demo.py` (`make_demo_fleet`) built in this phase, ahead of the plan's
   Phase 5, because the Phase 1 and Phase 4 tests need real DuckDB files.
   `tags.py` came forward from Phase 3 because `ScanConfig` defaults to its
   patterns.
3. Discovery detail the plan left open: when the limit is hit, the first
   further DuckDB file is listed as rejected `over_max_files` and the walk
   stops (LIMITS L-01).
4. Cache directory uses `appauthor=False` (decision D-015).

### Phase 2: profiler (2026-11-04)

1. `models.py` (profile and summary models, `PROFILE_SCHEMA_VERSION = 1`),
   `profiler.py` (`profile_database`, `classify_error`, never raises),
   `fingerprint.py` (canonical tables, strict and loose fingerprints,
   database fingerprint). Family assignment, variants and diffs follow in
   Phase 3; the primitives came forward because the profiler needs them.
2. Timestamps with a time zone are converted to UTC in SQL; the Python client
   would need `pytz` for them (LIMITS 13).
3. A file held by a writer: on Windows even the header read fails, so
   discovery keeps it as a candidate and the profiler classifies it `locked`
   (LIMITS 11). Lock test (`hold_open` fixture in `conftest.py`) covers both.
4. A file whose header lacks the DuckDB magic is never opened by the profiler
   (`not_duckdb`), also when it is called directly.

### Phase 3: fingerprints and families (2026-11-04)

1. `fingerprint.py`: `assign_families` and the richer `family_groups`
   (members, canonical structure, used by the catalog), `table_variants`,
   `SchemaMap` helpers and `diff_schemas` (`SchemaDiff`, `TableDiff`,
   `TypeChange`). Diffs and variants cover tables only; views are not
   fingerprinted (D-007).
2. Tests in `test_families.py` (hand-built profiles and the demo fleet:
   strict families 3, 3, 2, 1; loose 6, 2, 1; `events` has 3 variants).
   Tests for the fingerprint primitives are in `test_fingerprint.py`.

### Phase 4: store, coordinator, catalog (2026-11-04)

1. `store.py` (`ProfileStore`, recent roots), `coordinator.py` (`run_scan`
   with the progress events of plan Section 3.5, spawn process pool, in-process
   run for one worker or one file, `worker_crashed` handling), `catalog.py`
   (`build_catalog`, `CatalogReader`, atomic swap with 5 retries).
2. Deviation from plan 3.4 rule 4: the catalog is filled by unnesting one
   Python list per column instead of `executemany` (decision D-016, 1.1 s
   against 56 s for 120k column rows).
3. The reuse check also compares the `.wal` presence flag (LIMITS 15), and
   `ScanSummary.workers` is the configured count while the pool is capped at
   the number of files to profile (LIMITS 16).
4. `CatalogReader.families()` returns each family with its member list, and
   `table_variants()` / `families()` parse their canonical JSON.

### Phase 5: CLI and report (2026-11-04)

1. `cli.py` (`scan`, `report`, `make-demo`, `paths`; exit codes 0, 1 usage,
   2 fatal; `--progress text|json|none`), `__main__.py` (guarded, so the spawn
   pool can re-import it), `report.py` (text and Markdown from the catalog).
   `make_demo_fleet` was already in place since Phase 1.
2. Checked by hand on Windows with the console script: `duckfleet make-demo`,
   `scan` (11 candidates, 2 rejected, 10 ok, 1 failed, 22 workers = 24 CPUs
   minus 2), a second `scan` (10 reused, the corrupt file retried), `report`
   (strict families 3, 3, 2, 1; loose 6, 2, 1; `events` 3 variants; time
   windows in order; `wal_present`, `empty_database`, the `corrupt` error and
   the 2 rejected files).
3. Scan timestamps in `ScanSummary` now carry milliseconds so the report can
   show a meaningful duration.

### Phase 6: Streamlit Overview (2026-11-04)

1. `duckfleet_ui`: `launch.py` (`duckfleet-ui`), `app.py` (`st.navigation`
   with the Overview page), `scan_runner.py` (scan as a subprocess, JSON-lines
   progress, stderr kept for the error expander), `catalog_access.py`
   (`st.cache_data` wrappers keyed by catalog path and mtime, a reader per
   call), `views/overview.py` (sidebar, metrics, fleet table, families and
   matrix, table variants, diff, timeline, health, rejected files).
2. Tests: AppTest smoke tests on the wrapper script `_overview_app.py`
   (every section, session state, load and scan buttons, a complete
   Scan -> full rescan -> cached rescan flow with a real subprocess) and real
   subprocess tests of the scan runner. `st.navigation` itself is not
   exercised by AppTest; the page function is (plan Section 10 item 3).
3. Manual run on Windows (built-in browser, `duckfleet-ui`): scan of the demo
   fleet from the sidebar with live progress, full rescan (11 profiled),
   cached load in a fresh session, scan of a folder with a space and
   non-ASCII characters; all sections rendered, no exceptions.
4. `streamlit>=1.50` (the page uses `width="stretch"`); lock file refreshed.

