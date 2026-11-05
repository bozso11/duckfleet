# Changelog

## Unreleased

v0.2 in progress (plan: `docs/PLAN_v0.2.md`).

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

