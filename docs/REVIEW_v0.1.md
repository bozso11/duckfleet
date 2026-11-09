# Review of v0.1.0 against the plan

Date: 2026-11-04. Reviewed commit: `0d8918e` (v0.1.0). Reviewer: the planning
session (Opus-class, decision D-012), with two independent read-and-reproduce
passes (core; UI and documentation). Reference: `docs/PLAN_v0.1.md`.

## Verdict

v0.1.0 implements the plan faithfully. Every contract of plan Sections 3
and 5 is present, the cross-cutting rules of Sections 6 and 7 hold, the
documented deviations are sensible, and the acceptance checklist is true on
re-check. The review found 10 confirmed defects, none on the main path of
the demo flock, and 3 of them should be fixed before v0.2 work starts
(R-01, R-02, R-03). All fixes are Phase 0 of `docs/PLAN_v0.2.md` and ship
as 0.1.1.

## 1. Verified by the reviewer, not self-reported

1. CI: all eight phase commits green on `ubuntu-latest` and
   `windows-latest` (`gh run list`).
2. 181 tests pass and ruff is clean on Windows (Python 3.11.9, duckdb
   1.5.6, streamlit 1.66.1, pandas 3.1.1).
3. Demo flock via CLI: 14 files walked, 11 candidates, 2 rejected, 10 ok,
   1 failed (`corrupt`); rescan reuses 10 and retries 1; strict families of
   3, 3, 2, 1 members and loose families of 6, 2, 1; `events` with 3
   variants; 22 workers resolved on 24 CPUs.
4. UI on Windows in a browser: root typed in the sidebar, Scan, progress
   bar, then metrics, flock table, families and matrix, variants, compare,
   timeline with 9 bars, health, rejected; no browser console errors.
5. Conformance read of every core module and of the UI against the plan.
6. Every confirmed finding below was reproduced by the reviewer or by a
   review pass with a script against a temporary flock.

## 2. Confirmed findings

Severity: high = a real flock can hit it and the tool then fails or
misleads; medium = a crash or wrong result in an uncommon setup; low =
cosmetic, hygiene or documentation.

| ID | Sev. | Where | Claim | Scenario | Fix |
|---|---|---|---|---|---|
| R-01 | high | `catalog.py` (`storage_version BIGINT`), `discovery.read_header` | A file with `DUCK` at byte 8 and the high bit set in bytes 12..19 makes the catalog build fail, and every later scan of that root fails the same way until the file is removed | Garbage or truncated file with a valid magic; `build_catalog` raises `ConversionException` (value above 2^63), `catalog.duckdb.tmp` is left behind, the error profile is cached so `--full` does not help | `storage_version UBIGINT`; `read_header` returns `None` for values above 2^63-1 as a second guard; a demo file `broken/highbit.duckdb` |
| R-02 | high | `profiler._run_aggregates`, `_as_datetime` | `'infinity'` and `'-infinity'` TIMESTAMP or DATE values become year 9999 or year 1 time windows | Any `valid_to = 'infinity'` column (common in slowly changing dimension tables); the timeline bar spans ten millennia | `min(c) FILTER (WHERE isfinite(c))` and `max(...)` for temporal columns in both the exact and the sampled query; reject `datetime.max` and `datetime.min` in `_as_datetime`; note under TUNING(time-window-scope) |
| R-03 | medium | `profiler.profile_database`, `config.ScanConfig` | An invalid tag regex raises out of `profile_database` (plan: never raises) and kills the scan with 1 and with N workers | `tag_patterns=["("]`; `_file_facts` runs outside the `try` and `error_profile` repeats it in the parent | `field_validator("tag_patterns")` compiling each pattern and requiring the `key` and `value` groups (usage error, exit 1 in the CLI); `_file_facts` inside the `try` with tag-less fallback |
| R-04 | medium | `views/overview.py` sidebar, `config.resolved_workers` | On a machine with 64 or more logical CPUs the page raises before anything renders (`value` 62+ above `max_value` 61) | Workstations and servers with 64+ CPUs | Cap `resolved_workers()` at `MAX_POOL_WORKERS` in core (summary and pool then agree); the sidebar uses the capped value |
| R-05 | low | `profiler._table_stats` | Temporal values outside Python's datetime range come back as strings and silently drop out of the window; LIMITS 14 claims a `table_error` that is not recorded | `TIMESTAMP '10000-01-01'` next to a normal row: `time_min` set, `time_max` NULL, no error | Record a table error and clear that table's window when a temporal min or max is not a date or datetime; correct LIMITS 14 |
| R-06 | low | `discovery.discover` | The cache directory is not excluded from discovery | `--cache-dir` inside the root, or the user cache under a scanned home folder: the catalog becomes a flock member on the next scan | Prune the resolved cache directory (realpath, case-normalised) from `dirnames` |
| R-07 | low | `catalog.build_catalog` | The catalog writer connects without the four standard settings (extension autoload on) | Hygiene only; fixed SQL today | Pass the same config dict (minus `read_only`) |
| R-08 | low | `views/overview.py` timeline | A database whose time window is a single instant draws a zero-width bar and is not listed below the chart either | A file with one event row, or a DATE column with one value | Pad the displayed bar to a minimum width and say so in the hover text; list single-instant databases in the caption |
| R-09 | low | `views/overview.py` `_scan`, `scan_runner.py` | When the scan subprocess exits 0 without a `finished` event the page says "exit code 0"; stray stdout lines are never shown | A print statement in a future core change, or a crash after the catalog step | Say that no `finished` event arrived and append `stray_stdout` to the log expander |
| R-10 | low | `tests/test_catalog.py` lines 77-81 | `assert (... or True)` always passes | Dead assertion | Delete it; `test_column_row_count_matches_the_profiles` covers the count |

## 3. Documentation corrections

| ID | Where | Correction |
|---|---|---|
| R-11 | `CHANGELOG.md` 0.1.0 open item 1, `docs/DECISIONS.md` D-018, `docs/FEATURES.md` F-SCAN-09 | An `encrypted` fixture is producible with duckdb 1.5.6: `ATTACH 'x.duckdb' (ENCRYPTION_KEY 'k')` writes a file whose header keeps the magic; opening it read-only without a key raises `Cannot open encrypted database`, classified `encrypted`. Add the fixture to the demo flock and a test; `incompatible_version` stays unverified |
| R-12 | `docs/LIMITS.md` L-02, `docs/ARCHITECTURE.md` Section 3 | The pool is capped at 61 workers (`MAX_POOL_WORKERS`, the Windows limit of `ProcessPoolExecutor`); undocumented |
| R-13 | `CHANGELOG.md` 0.1.0 deviations | The plan's Phase 4 test text (second run reuses every profile) cannot hold for the demo flock because the corrupt file is retried; the test asserts 10 of 11 and no timing |
| R-14 | `docs/ARCHITECTURE.md` Section 4 | Cache path wording predates D-015 (`appauthor=False`; Windows `%LOCALAPPDATA%\duckflock\Cache`); `catalog.duckdb.tmp` missing from the listing |
| R-15 | `docs/FEATURES.md` F-SCAN-10 | Reuse also compares the profile schema version |
| R-16 | `docs/ARCHITECTURE.md` Section 7, `packages/duckflock-ui/pyproject.toml` | Sidebar description omits the recent-roots selectbox and the Load button; the package description lists pages that do not exist yet |

## 4. Plausible, not reproduced

| ID | Claim | Decision |
|---|---|---|
| P-01 | Reuse ignores `duckdb_version`; after a DuckDB upgrade a partial rescan can mix type spellings and split a family | Adopt: add `duckdb_version` to the reuse rule (one full re-profile after an upgrade) |
| P-02 | `_profiles_in_pool` shuts the pool down without `cancel_futures`, so an error in the consumer loop or Ctrl-C still profiles every queued file first | Adopt: `shutdown(cancel_futures=True)` in a `try/finally` |
| P-03 | Profiles with `table_error:*` have status `ok` and are reused until the file changes | Document in LIMITS 15 |
| P-04 | A click during a scan raises Streamlit's rerun inside `on_event`; the scan finishes (stdout is drained) but the finished notice and `active_root` are lost until "Load existing catalog" | Document in LIMITS 5 |

## 5. Checked and fine

1. Read-only rule: the only connection to a scanned file is in the
   profiler, `read_only=True` with the four settings; the catalog reader is
   read-only with the four settings; nothing is written inside the scanned
   folder (tested with 1 and 2 workers).
2. No `fork`, `fcntl`, `signal`, `shell=True`; spawn context on both OSes;
   no module-level pandas, streamlit or plotly in core (enforced by a
   subprocess test).
3. SQL: identifiers always go through `quote_ident`, user strings are bound
   as parameters. Verified end to end with a schema named `a.b`, a table
   named `x;y"` with a newline, columns `é.ü`, `back\slash`, `*`, `--c`,
   reserved names, and a file stem `we'ird.a b`.
4. Degenerate inputs: empty database, 0-row table, BLOB-only and
   STRUCT-only tables, BIT, INTERVAL, TIME, TIMETZ, UUID, JSON, HUGEINT,
   DECIMAL(38,10), FLOAT `nan`, ENUM, array, struct and union columns, 200
   and 1,000 column tables, a view whose base table was dropped, dates in
   year 1 and 1850, TIMESTAMP_NS, TIMESTAMPTZ converted to UTC in SQL.
5. Fingerprints: compact JSON, order-insensitive, pinned values,
   deterministic family numbering, variants, diffs.
6. Store and catalog: atomic JSON writes, key-collision guard, prune,
   schema-version filter, reuse rule including the WAL flag and `full`,
   atomic swap with retries, stale temp removal, readers never keep a
   handle (a second scan replaced the catalog while the page had rendered
   it).
7. Coordinator: workers share no mutable state; `BrokenProcessPool` on
   submit and on result handled; events in the plan's order; CLI exit codes
   and JSON-only stdout.
8. UI: every element of plan Phase 6 item 5 present; `st.cache_data`
   keyed by catalog path and mtime (a rescan that adds a file changes the
   metrics); `sys.executable`, `PYTHONUTF8=1`, stdin devnull, stderr
   drained and shown on failure; no `pages/` directory; session-state keys
   distinct; an empty root renders every section.
9. Documentation: the 10 `# TUNING(<id>)` markers in code match the 10
   rows of `docs/TUNING.md`; `docs/ARCHITECTURE.md` Section 5 equals
   `SCHEMA_SQL`; versions are 0.1.0 everywhere.

## 6. Acceptance checklist re-check

| Item | Result |
|---|---|
| 1 CI green on both OSes | Confirmed for all eight commits |
| 2 Demo scan numbers and rescan reuse | Confirmed by the reviewer's own run |
| 3 Report families, variants, windows, health, errors, rejected | Confirmed |
| 4 Lock test on both OSes | Confirmed on Windows locally; Linux through CI |
| 5 UI scan and all sections on Windows; Linux through AppTest in CI | Windows confirmed by the reviewer in a browser; Linux through CI |
| 6 Non-ASCII path with a space | Covered by tests on both OSes; the exact folder name in the changelog is self-reported |
| 7 Workers default and summary | Confirmed (22 of 24) |
| 8 Docs updated, versions bumped | Confirmed, with the corrections of Section 3 |
