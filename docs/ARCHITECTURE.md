# Architecture

Last updated: 2026-11-08 (0.4.1). Section 5 holds the final catalog schema,
copied from `duckfleet_core/catalog.py` (`SCHEMA_SQL`).

## 1. Two packages, one boundary

```
packages/duckfleet-core    library + CLI; depends on duckdb, pydantic, platformdirs, psutil, pytz
                           (optional extra `export`: pyarrow)
packages/duckfleet-ui      Streamlit app; depends on duckfleet-core, streamlit>=1.66, plotly, pandas, pyarrow
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
   connections, each with the catalog's own temp leaf under the cache, P-13).
   `CatalogReader` checks once that the catalog has the columns added after 0.3 and
   raises `CatalogOutdatedError` (X-04).
8. **report**: text and Markdown summaries from the catalog.
9. **cli**: `scan`, `report`, `make-demo`, `paths`, and (v0.2) `sql`,
   `summarize`, `preview`, (v0.3) `plot`.
10. **demo**: generator of a small fleet with known properties, used by
    tests and the acceptance checklist.
11. **config / models**: `ScanConfig` (every option and its resolved
    default) and the pydantic models of a profile (`DatabaseProfile`,
    `TableProfile`, `ColumnInfo`, `ProfileError`) and of a scan
    (`ScanSummary`).
12. **query** (v0.2): the only way to open a scanned file in the UI or CLI
    process. `QueryConfig`, `open_readonly` (one sandboxed connection config
    per process, D-021), `run_query`, `export_query` (CSV and Parquet through
    Arrow, in memory), `preview_table`, `table_columns`, `QueryError` with
    kinds. Every query runs in a one-thread executor under a timeout
    enforced with `interrupt()` (D-024). `sqlutil.py` holds `quote_ident`.
13. **summary** (v0.2): `summarize_table` (DuckDB `SUMMARIZE`, sampled above
    the row threshold) and the `TableSummary` models; `store.SummaryStore`
    caches them as JSON sidecars (D-020).
14. **cli_query** (v0.2): the commands `sql`, `summarize`, `preview` (D-027).
15. **tempdir** (0.2.1, per file since 0.3.1): the DuckDB temp folders under
    `<cache dir>/tmp/<pid>`, one leaf per file per process,
    `<pid>/<sha1(absolute path)[:16]>` (`file_temp_dir`, `ensure_file_temp_dir`), used by the
    query layer, the profiler and the catalog writer; two DuckDB instances in one process must
    never share a folder (W-01, D-042). The `<pid>` folder is created before connecting and
    removed at exit; stale folders are swept by `run_scan` and on a process's first connection
    (D-034, D-035, D-043).
16. **plotdata** (v0.3): `PlotSpec`, `plan_plot`, `build_sql`, `fetch_plot_data`; turns a spec
    into one bounded DuckDB query (time buckets, numeric buckets, bins, reservoir sample, top-N
    folding) on the sandboxed query connection and returns `PlotData` (D-033, D-036).
17. **cli_plot** (v0.3): the command `plot` (D-037); since 0.4 also `--databases` and `--family`.
18. **fleet** (v0.4): `FleetSelection` (1 to 50 distinct rel_paths) and its aliases, `open_fleet` (an
    in-memory DuckDB; the members attached read-only from catalog paths, aliases that equal a union
    schema renamed, then external access off and the configuration locked, then every member view
    probed, then one union view per table with a `database` column), `fleet_tables`,
    `describe_fleet`, `run_fleet_query` (an error of the query is a `FleetQueryError` that carries the
    connection's summary), `export_fleet_query`. Every connection has its own temp folder (D-040,
    D-044, D-050).
19. **cli_fleet** (v0.4): the commands `fleet-tables` and `fleet-sql` and the selection options
    `--databases` (repeatable), `--family`, `--all` (D-046, D-050).
20. **content hash** (v0.4): `profiler._content_hash` stores `TableProfile.content_hash` for tables up to
    L-07 rows; the catalog has `tables.content_hash` and `CatalogReader.content_variants()`; the report
    and the Overview read it (D-041, D-045).

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

Reading one database (v0.2):

```
catalog.duckdb  --abs_path, size, mtime-->  Database page / SQL page / CLI sql, summarize, preview
                                               |  query.open_readonly (read_only, external access off,
                                               |  configuration locked, fixed threads and memory)
                                               v
                                          scanned file   (one connection per call, closed in finally)
summaries/<file_key>/<table_key>.json  <--  SummaryStore (valid while size, mtime, versions match)
```

Plotting (v0.3) goes through the same sandboxed connection (`fetch_plot_data_on` runs the same plan on any open
connection, `fetch_plot_data` and `fetch_fleet_plot_data` open one):

```
Plot page / CLI plot --PlotSpec--> plotdata.fetch_plot_data
        column lookup (duckdb_columns) -> one counts statement -> plan_plot (strategy, bucket)
        -> one data query (time_bucket | numeric buckets | bins | reservoir sample | top-N folding)
        -> PlotData (at most max_points points) --> charts.figure (UI only) / CSV, JSON (CLI)
```

Cross-database work (v0.4) goes through a fleet connection, opened per request:

```
SQL page / Plot page / CLI fleet-sql, fleet-tables, plot --family
     |  catalog: rel_path -> abs_path of the chosen databases (at most 50)
     v
fleet.open_fleet:  duckdb.connect() in memory, own temp folder <pid>/fleet-<8 hex>
     ATTACH '<abs path>' AS <alias> (READ_ONLY) for each member   (a failure is an AttachFailure)
     DETACH and ATTACH again as <alias>_2 where an alias equals a schema of the union views
     SET enable_external_access = false; SET lock_configuration = true
     SELECT * FROM <alias>.<schema>.<view> LIMIT 0 for every member view (a failure leaves that
       member out of the union view of that name, and is a view failure)
     CREATE VIEW memory."<schema>"."<table>" AS SELECT '<rel_path>' AS database, * FROM <alias>... UNION ALL BY NAME ...
     |  user SQL / the plot plan runs here, under the timeout
     v
result + FleetSummary (aliases, attach failures, union views)      connection closed, folder removed
```

## 4. Storage locations

```
<user cache dir>                      platformdirs.user_cache_dir("duckfleet", appauthor=False)
                                      (D-015; Windows %LOCALAPPDATA%\duckfleet\Cache, Linux ~/.cache/duckfleet)
  recent_roots.json
  tmp/<pid>/<file hash>/              DuckDB spill files of one process, one folder per file (0.3.1), removed at exit
                                      (also the leaf of the catalog reader, 0.4.1); fleet-<8 hex>/ per fleet connection
  roots/<root_key>/
    root.txt
    profiles/<file_key>.json
    catalog.duckdb
    catalog.duckdb.tmp                build target, replaced atomically, gone after a scan
    last_scan.json
    summaries/<file_key>/<table_key>.json    SUMMARIZE results (v0.2), removed with the profile
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
    error VARCHAR, content_hash VARCHAR
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
   on Windows. Its connections use the catalog's temp leaf under the cache as
   `temp_directory` (the cache is read off the layout `<cache>/roots/<root_key>/`). Methods: `scan_run`, `databases`, `tables`, `columns`,
   `families`, `family_members`, `family_table_matrix`, `table_variants`,
   `content_variants`, `timeline`, `health`, `rejected`, `diff`, `df` (pandas, UI only).

## 6. Concurrency and locking rules

1. Scanned files: read-only connections only. Several processes may read
   the same file; a file held read-write elsewhere is reported `locked`.
2. Catalog: one writer (the scan), written to a temp file and swapped in
   with `os.replace`. Readers open per request and close, so the swap is
   never blocked on Windows.
3. Workers: `spawn` context on both OSes; entry points in importable
   modules with plain-type arguments; DuckDB `threads=1` per worker so N
   workers use N cores.
4. Queries (v0.2): inside the UI or CLI process every connection to a scanned
   file uses the same config dict, because DuckDB refuses a second
   connection to the same file with another config. Threads, memory and the
   temp directory are resolved once per process and never page inputs; the
   temp directory is the file's own leaf under the process folder, so the config of
   one file is the same everywhere and two files never share a folder (W-01). The
   profiler's config is the same sandbox (read-only, external access off,
   configuration locked) except threads and memory, so an in-process scan
   next to an open query connection works when those two agree (P-05). The
   `temp_directory` key must come after `enable_external_access` (D-035). The lock is per
   database instance, the time zone per session, so no zone is set and
   TIMESTAMPTZ values show in the machine's zone (D-026).
5. Query timeout: a watcher in the calling thread calls `interrupt()` on the
   connection; no signals, so Windows and Linux behave alike.
6. Fleet connections (v0.4): attached only from catalog paths and before the lockdown; user SQL
   never runs before `lock_configuration` is set; aliases and view names come from file and table
   names and are always quoted, rel_paths enter SQL only as escaped string literals; no cached
   connections; each connection is its own DuckDB instance with its own temp folder, because two
   instances must never share one (W-01). The open sequence is attach, separate aliases from
   schema names, lock down, probe the member views, then build the union views as
   `memory."<schema>"."<table>"`: no union view may exist while a member view is probed, because a
   stored view falls back to the default catalog for a table name it cannot find (X-03).

## 7. UI structure (v0.4)

```
launch.py         console script duckfleet-ui -> streamlit run app.py
app.py            st.set_page_config, st.navigation(list(nav.PAGES.values()))
nav.py            the st.Page objects: Overview, Database, Plot, SQL (views import it inside functions)
common.py         environment, root and catalog resolution, keep_valid, the shared database
                  selectbox, file_state (missing, changed or current against the scan; its live
                  size and mtime key the preview and plot caches)
views/plot.py     render(): scope control, table (the union views in scope several databases, with
                  the attach and view warnings) and chart kind, form (x, y, colour, filter, options,
                  Plot), chart, caption (the databases that hold the table), Plotted data (table,
                  CSV), SQL
charts.py         palette, template, needs_panels, Plotly figures of PlotData, to_frame (no widgets)
views/overview.py render(): sidebar (recent-roots selectbox, root folder, workers, max files,
                  full rescan, sniff all, Scan and Load existing catalog buttons), metrics,
                  fleet table, families + matrix, table variants, identical content (the cap and
                  an older catalog's info), diff, timeline, health, rejected
views/database.py render(): database selectbox, header, tables, lazy tabs Columns, Summary,
                  Preview, Open in SQL (sets scope one database), and Plot this table (hands the
                  table, its first temporal column and first numeric column to the Plot page
                  through plot_prefill, also in scope one database)
views/sql.py      render(): scope control, schema expander (union views and aliases in scope
                  several, naming each view's label column, with the attach and view warnings),
                  editor (placeholder built from the label column), form (Options + Run), Clear,
                  result grid, downloads, history
scope.py          scope_control(dbs, families) -> Scope(mode, rel_paths): radio One database /
                  Several databases, multiselect (at most 50), Family selectbox (D-047)
scan_runner.py    subprocess (python -m duckfleet_core scan --progress json) + JSON-lines
catalog_access.py st.cache_data wrappers keyed by catalog path + mtime, DataFrames
query_access.py   the process-wide QueryConfig (temp dir under the cache), cached preview pages and
                  plot data keyed by the file's live size and mtime, summary helpers, result
                  DataFrames (never cached for ad-hoc SQL)
```

The pages read `DUCKFLEET_ROOT` and `DUCKFLEET_CACHE_DIR` for their defaults
(the tests rely on them); `DUCKFLEET_QUERY_THREADS` and
`DUCKFLEET_QUERY_MEMORY_MB` override the query connection's resources. State
shared between pages lives in the session state: `active_root`,
`selected_rel_path`, `selected_table`, `sql_text`. Widgets whose value must survive a visit to
another page (the six sidebar inputs, `selected_rel_path`, `selected_table`, `sql_text`,
`sql_max_rows`, `sql_timeout`, `scope_mode`, `scope_rel_paths`, the plot options) carry a `key` and
`persist_state="session"`.
The Plot page keeps its last request (the spec as JSON, not the data) in `plot_request`, and
`plot_prefill` carries what **Plot this table** hands over. The scope lives in `scope_mode` and
`scope_rel_paths` (shared by the SQL and Plot pages, `persist_state="session"`); results, downloads and
plot requests carry `Scope.key` so a result never shows for another scope.
