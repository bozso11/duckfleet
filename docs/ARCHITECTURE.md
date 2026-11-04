# Architecture

Last updated: 2026-11-04 (Phase 0). After v0.1 ships, copy the final
catalog schema from `docs/PLAN_v0.1.md` Section 3.4 into Section 5 here.

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
   header magic, records size, mtime, WAL sibling, storage version.
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

## 3. Data flow

```
root folder
   |  discovery (extension + header magic)
   v
candidates (largest first)  --reuse check against cached profiles-->  to_profile
   |  ProcessPoolExecutor(spawn), N = cpu_count - 2 workers, each: read_only, threads=1, memory cap
   v
DatabaseProfile JSON per file   <cache>/roots/<root_key>/profiles/*.json   (source of truth)
   |  build_catalog: all profiles -> catalog.duckdb.tmp -> os.replace
   v
catalog.duckdb (derived index)  --CatalogReader (read-only per request)-->  CLI report / Streamlit UI
```

The UI runs the scan as a subprocess (`python -m duckfleet_core scan
--progress json`) and reads progress events from its stdout. The process
pool therefore never lives inside the Streamlit server process.

## 4. Storage locations

```
<user cache dir>/duckfleet/           platformdirs.user_cache_dir("duckfleet")
  recent_roots.json
  roots/<root_key>/
    root.txt
    profiles/<file_key>.json
    catalog.duckdb
    last_scan.json
```

Nothing is written inside the scanned folder.

## 5. Catalog schema

See `docs/PLAN_v0.1.md` Section 3.4 until v0.1 ships; then the final schema
lives here.

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
app.py            st.set_page_config, st.navigation([Overview])
views/overview.py render(): sidebar (root, options, Scan), metrics, fleet table,
                  families + matrix, table variants, diff, timeline, health, rejected
scan_runner.py    subprocess + JSON-lines progress
catalog_access.py st.cache_data wrappers keyed by catalog path + mtime
```

Later versions add `views/database.py`, `views/sql.py`, `views/plot.py`.
