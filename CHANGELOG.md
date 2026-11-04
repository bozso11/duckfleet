# Changelog

## Unreleased

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

