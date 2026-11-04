# Changelog

## Unreleased

Nothing yet.

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

### Open after 0.1.0

1. `incompatible_version` and `encrypted` are classified by message only; no
   fixture exists (plan Section 10 item 2).
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

