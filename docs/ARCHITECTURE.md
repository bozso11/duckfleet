# Architecture

Last updated: 2026-11-05 (0.1.1). Section 5 holds the final catalog schema,
copied from `duckfleet_core/catalog.py` (`SCHEMA_SQL`).

## 1. Two packages, one boundary

```
packages/duckfleet-core    library + CLI; depends on duckdb, pydantic, platformdirs, psutil
packages/duckfleet-ui      Streamlit app; depends on duckfleet-core, streamlit, plotly, pandas, pyarrow
```

The boundary rule: core never imports the UI or its libraries at module
level; the UI contains no profiling, fingerprinting or SQL generation of
its own. Anything the UI needs from the data goes through a core function.
This keeps the UI swappable (Panel, a desktop app) and the core usable from
the CLI, notebooks and tests.

## 2. Components (core)

1. **discovery**: walks the root, filters by extension, checks the DuckDB
   header magic, records size, mtime, WAL sibling, storage version (none when
   the header value is above 2^63 - 1). The cache directory is pruned from the
   walk. A file a writer holds open (unreadable on Windows) stays a candidate
   and is reported `locked` by the profiler.
2. **profiler**: worker entry point; opens one file read-only with capped
   threads and memory, lists schemas, tables, views, columns, counts rows,
   computes cheap column stats and time windows, classifies errors. Never
   raises, never writes.
3. **fingerprint**: canonical table and database structures, strict and
   loose fingerprints, family assignment, per-table variants, diffs. Pure
   functions.
4. **tags**: path-token tags (heuristic, `TUNING(tag-patterns)`).
5. **store**: per-file JSON profiles and scan summaries under the user
   cache directory; recent roots.
6. **coordinator**: discovery, reuse decision, process pool (spawn),
   result collection, prune, catalog build, progress events.
7. **catalog**: builds the DuckDB catalog file from all profiles (single
   writer, atomic replace) and reads it (per-request read-only
   connections).
8. **report**: text and Markdown summaries from the catalog.
9. **cli**: `scan`, `report`, `make-demo`, `paths`.
10. **demo**: generator of a small fleet with known properties, used by
    tests and the acceptance checklist.
11. **config / models**: `ScanConfig` (every option and its resolved
    default) and the pydantic models of a profile (`DatabaseProfile`,
    `TableProfile`, `ColumnInfo`, `ProfileError`) and of a scan
    (`ScanSummary`).

## 3. Data flow

```
root folder
   |  discovery (extension + header magic)
   v
candidates (largest first)  --reuse check against cached profiles-->  to_profile
   |  ProcessPoolExecutor(spawn), N = min(max(1, cpu_count - 2), 61) workers, each: read_only, threads=1, memory cap
   v
DatabaseProfile JSON per file   <cache>/roots/<root_key>/profiles/*.json   (source of truth)
   |  build_catalog: all profiles -> catalog.duckdb.tmp -> os.replace
   v
catalog.duckdb (derived index)  --CatalogReader (read-only per request)-->  CLI report / Streamlit UI
```

The reuse check compares size, mtime, WAL presence, profile schema version
and DuckDB version. The pool is capped at 61 workers (`MAX_POOL_WORKERS`, the
limit of `ProcessPoolExecutor` on Windows), also for an explicit `--workers`.

The UI runs the scan as a subprocess (`python -m duckfleet_core scan
--progress json`) and reads progress events from its stdout. The process
pool therefore never lives inside the Streamlit server process.

## 4. Storage locations

```
<user cache dir>                      platformdirs.user_cache_dir("duckfleet", appauthor=False)
                                      (D-015; Windows %LOCALAPPDATA%\duckfleet\Cache, Linux ~/.cache/duckfleet)
  recent_roots.json
  roots/<root_key>/
    root.txt
    profiles/<file_key>.json
    catalog.duckdb
    catalog.duckdb.tmp                build target, replaced atomically, gone after a scan
    last_scan.json
```

Nothing is written inside the scanned folder.

## 5. Catalog schema

`<cache>/roots/<root_key>/catalog.duckdb`, rebuilt from all profiles at the
end of every scan (build into `catalog.duckdb.tmp`, then `os.replace`).

```sql
CREATE TABLE scan_runs (
    run_id VARCHAR, root VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP,
    duckfleet_version VARCHAR, duckdb_version VARCHAR, workers INTEGER,
    files_walked INTEGER, files_candidates INTEGER, files_rejected INTEGER,
    files_profiled INTEGER, files_reused INTEGER, files_failed INTEGER,
    truncated BOOLEAN, config_json VARCHAR
);
CREATE TABLE databases (
    rel_path VARCHAR PRIMARY KEY, file_name VARCHAR, abs_path VARCHAR,
    size_bytes BIGINT, mtime_ns BIGINT, has_wal BOOLEAN, storage_version UBIGINT,
    status VARCHAR, error_type VARCHAR, error_message VARCHAR,
    profiled_at TIMESTAMP, profile_seconds DOUBLE,
    n_schemas INTEGER, n_tables INTEGER, n_views INTEGER, total_rows BIGINT,
    fp_strict VARCHAR, fp_loose VARCHAR, family_strict VARCHAR, family_loose VARCHAR,
    time_min TIMESTAMP, time_max TIMESTAMP, tags_json VARCHAR, health_json VARCHAR
);
CREATE TABLE tables (
    rel_path VARCHAR, schema_name VARCHAR, table_name VARCHAR, kind VARCHAR,
    n_columns INTEGER, row_count BIGINT, estimated_size BIGINT,
    fp_strict VARCHAR, fp_loose VARCHAR, time_min TIMESTAMP, time_max TIMESTAMP,
    error VARCHAR
);
CREATE TABLE columns (
    rel_path VARCHAR, schema_name VARCHAR, table_name VARCHAR, ordinal INTEGER,
    column_name VARCHAR, data_type VARCHAR, nullable BOOLEAN,
    is_temporal BOOLEAN, is_numeric BOOLEAN, non_null_count BIGINT,
    min_value VARCHAR, max_value VARCHAR, approx_distinct BIGINT, sampled BOOLEAN
);
CREATE TABLE families (
    level VARCHAR, family_id VARCHAR, fingerprint VARCHAR, n_members INTEGER,
    n_tables INTEGER, canonical_json VARCHAR
);
CREATE TABLE table_variants (
    schema_name VARCHAR, table_name VARCHAR, fp_strict VARCHAR,
    n_members INTEGER, n_columns INTEGER, canonical_json VARCHAR
);
CREATE TABLE rejected (rel_path VARCHAR, reason VARCHAR);
```

Rules:

1. `families.level` is `strict` or `loose`. `family_id` is `F1, F2, ...` for
   strict and `L1, L2, ...` for loose, numbered by `n_members` descending,
   then fingerprint ascending (deterministic).
2. Databases with `status = 'error'` or without tables have NULL
   fingerprints and family ids; they appear in `databases` and in the health
   list, not in `families`.
3. `tags_json` and `health_json` are JSON text; `CatalogReader` parses them.
   `families.canonical_json` is `[[schema, table, canonical columns], ...]`
   of the first member, `table_variants.canonical_json` the strict
   `[[name, type], ...]` of that variant.
4. Timestamps are naive UTC; `time_min` and `time_max` are the minimum and
   maximum over all temporal columns of all tables (TUNING time-window-scope).
5. `CatalogReader` opens a read-only connection inside each method and closes
   it in `finally`; an open handle would block the next scan's `os.replace`
   on Windows. Methods: `scan_run`, `databases`, `tables`, `columns`,
   `families`, `family_members`, `family_table_matrix`, `table_variants`,
   `timeline`, `health`, `rejected`, `diff`, `df` (pandas, UI only).

## 6. Concurrency and locking rules

1. Scanned files: read-only connections only. Several processes may read
   the same file; a file held read-write elsewhere is reported `locked`.
2. Catalog: one writer (the scan), written to a temp file and swapped in
   with `os.replace`. Readers open per request and close, so the swap is
   never blocked on Windows.
3. Workers: `spawn` context on both OSes; entry points in importable
   modules with plain-type arguments; DuckDB `threads=1` per worker so N
   workers use N cores.

## 7. UI structure (v0.1)

```
launch.py         console script duckfleet-ui -> streamlit run app.py
app.py            st.set_page_config, st.navigation([Overview])
views/overview.py render(): sidebar (recent-roots selectbox, root folder, workers, max files,
                  full rescan, sniff all, Scan and Load existing catalog buttons), metrics,
                  fleet table, families + matrix, table variants, diff, timeline, health, rejected
scan_runner.py    subprocess (python -m duckfleet_core scan --progress json) + JSON-lines
catalog_access.py st.cache_data wrappers keyed by catalog path + mtime, DataFrames
```

The page reads `DUCKFLEET_ROOT` and `DUCKFLEET_CACHE_DIR` for its defaults
(the smoke test relies on them). Later versions add `views/database.py`,
`views/sql.py`, `views/plot.py`.
