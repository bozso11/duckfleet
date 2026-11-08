# Feature matrix

The first file an LLM or a person reads to learn what duckfleet does today.
Status values: `done vX` (shipped in version X), `planned vX` (in the
version ladder), `open option` (not scheduled), `out of scope`. Update the
row in the same commit that changes the behaviour.

Last updated: 2026-11-08 (0.4.0).

## Discovery and scanning

| ID | Feature | Status | Limits | Notes |
|---|---|---|---|---|
| F-SCAN-01 | Recursive discovery of DuckDB files by extension list | done v0.1 | L-01 max files | default `.duckdb`, `.db`, `.ddb` |
| F-SCAN-02 | Header check (magic bytes) on every candidate; non-DuckDB files rejected with reason | done v0.1 | | mandatory, DuckDB itself opens SQLite files |
| F-SCAN-03 | Optional header sniff of files with any extension | done v0.1 | | `--sniff-all` |
| F-SCAN-04 | Parallel read-only profiling in worker processes, all but two cores | done v0.1 | L-02, L-03, L-04 | spawn context on both OSes |
| F-SCAN-05 | Per-file profile: schemas, tables, views, columns, types, exact row counts | done v0.1 | | |
| F-SCAN-06 | Cheap column stats: non-null count, min/max for numeric and temporal, approx distinct | done v0.1 | L-05, L-06 | sampled above threshold |
| F-SCAN-07 | Time window per table and per file from temporal columns | done v0.1 | | TUNING temporal-types, timezones |
| F-SCAN-08 | Health flags: WAL sibling, empty database, no temporal columns, per-table errors | done v0.1 | | |
| F-SCAN-09 | Error classification: locked, not_duckdb, corrupt, incompatible_version, encrypted, missing, worker_crashed | done v0.1 | L-10 | substrings per installed duckdb; locked, not_duckdb, corrupt, encrypted and missing are covered by tests on both OSes (the demo fleet has a corrupt, a high-bit-header and an encrypted file); incompatible_version matches the message only (no fixture); a writer-held file is `locked` on both OSes |
| F-SCAN-10 | Profile cache; rescan reuses unchanged files (size + mtime + WAL presence + profile schema version + DuckDB version) | done v0.1 | | `--full` to force; failed files are always retried |
| F-SCAN-11 | Catalog as a DuckDB file rebuilt from cached profiles | done v0.1 | | user cache dir |
| F-SCAN-12 | Tags from path tokens (exp_12, run-03, key=value) | done v0.1 | | TUNING tag-patterns; `scan --tag-pattern REGEX` (0.1.1) replaces the defaults, patterns are validated |
| F-SCAN-13 | Scan progress as JSON lines for the UI | done v0.1 | | |
| F-SCAN-14 | Non-blocking scan (UI usable while scanning) | open option | | v0.1 blocks behind a progress bar |
| F-SCAN-15 | Cancel a running scan | open option | | |
| F-SCAN-16 | Per-file time budget | open option | | |
| F-SCAN-17 | Watch folder, automatic rescan | open option | | |
| F-SCAN-18 | Encrypted DuckDB files | open option | | needs key handling |
| F-SCAN-19 | SQLite, Parquet, CSV files as fleet members | open option | | |

## Fleet analysis

| ID | Feature | Status | Limits | Notes |
|---|---|---|---|---|
| F-FLEET-01 | Strict schema fingerprint (table + column names + types, order ignored) | done v0.1 | | |
| F-FLEET-02 | Loose schema fingerprint (names only) | done v0.1 | | |
| F-FLEET-03 | Schema families with member counts, both levels | done v0.1 | | |
| F-FLEET-04 | Per-table variants across the fleet | done v0.1 | | |
| F-FLEET-05 | Diff between two files or families (tables, columns, types) | done v0.1 | | |
| F-FLEET-06 | Family-by-table matrix | done v0.1 | | |
| F-FLEET-07 | Time-window timeline, one bar per file | done v0.1 | | files without temporal columns listed separately |
| F-FLEET-08 | Content fingerprint of small tables (identical config detection) | done v0.4 | L-07 | md5 of the sorted rows of tables up to 10,000 rows (`ScanConfig.content_hash_rows`), stored in the profile and the catalog; the report section Identical small tables and the Overview section Identical content (D-041, D-045); the upgrade re-profiles every file once |
| F-FLEET-09 | Identity query run against every file (user-supplied SQL) | open option | | depends on real metadata layout |
| F-FLEET-10 | Type normalisation for fingerprints (INT vs INTEGER aliases) | open option | | TUNING type-normalization |
| F-FLEET-11 | Column search across the fleet ("which files have column X") | open option | | |
| F-FLEET-12 | Views and indexes in fingerprints | open option | | views listed, not fingerprinted |

## Single database

| ID | Feature | Status | Limits | Notes |
|---|---|---|---|---|
| F-DB-01 | Table list with row counts, columns and types | done v0.2 | | Database page: tables with rows and time windows, Columns tab with the scan's stats and null share |
| F-DB-02 | Per-column summary (SUMMARIZE style) | done v0.2 | L-05, L-16 | on request, cached as JSON sidecars (D-020), `duckfleet summarize` |
| F-DB-03 | Paged data preview | done v0.2 | L-08, L-15 | column subset, sort, page number; `duckfleet preview` |
| F-DB-04 | Read-only SQL editor with result grid | done v0.2 | L-09, L-14, L-17 | sandboxed connection (D-021), history of 20 queries; `duckfleet sql` |
| F-DB-05 | Download results as CSV or Parquet | done v0.2 | L-09 | built in memory through Arrow (D-023); CSV cannot hold nested columns; Prepare is disabled while the editor differs from the shown result (0.2.1, D-035) |
| F-DB-06 | Query timeout | done v0.2 | L-14 | `interrupt()` from the calling thread (D-024); a cancel button stays an open option |
| F-DB-07 | Saved queries | open option | | |

## Visualisation

| ID | Feature | Status | Limits | Notes |
|---|---|---|---|---|
| F-PLOT-01 | Line, scatter, histogram, bar for chosen columns | done v0.3 | L-11, L-18, L-20, L-21 | Plotly; fixed chart rules (D-032); table view and CSV of the plotted data; `duckfleet plot` prints the same data |
| F-PLOT-02 | Temporal x axis offered first; DuckDB-side time bucketing and downsampling | done v0.3 | L-11, L-21 | mean with min to max band; numeric buckets, reservoir sample, bins; row order as x (D-033, D-036) |
| F-PLOT-03 | Colour or group-by column; optional SQL filter | done v0.3 | L-19 | colour folds into `(other)` beyond the cap; the filter runs on the sandboxed connection |
| F-PLOT-04 | Overlay the same table across files | done v0.4 | L-12, L-19 | Plot page scope *several databases* and `duckfleet plot --databases/--family`: the union view of the table, coloured by `database`, files beyond the series cap folded into `(other)` |
| F-PLOT-05 | Very large plots (Datashader) | open option | | would move the UI to Panel |

## Cross-database

| ID | Feature | Status | Limits | Notes |
|---|---|---|---|---|
| F-X-01 | Attach a chosen subset read-only in one connection | done v0.4 | L-12, L-22 | `duckfleet_core.fleet.open_fleet`: in-memory connection, files attached read-only, external access off, configuration locked (D-040); a member that cannot be attached is reported and the others work |
| F-X-02 | Same table across files as one result with a `database` column | done v0.4 | L-12, L-22 | one union view per table (`UNION ALL BY NAME`); columns with unrelated types are cast to text (D-044); SQL page scope *several databases*, CLI `fleet-sql` and `fleet-tables` |

## Interfaces and documentation

| ID | Feature | Status | Limits | Notes |
|---|---|---|---|---|
| F-CLI-01 | `duckfleet scan`, `report`, `make-demo`, `paths` | done v0.1 | | |
| F-CLI-02 | Report export to Markdown or HTML file | open option | | |
| F-CLI-03 | `duckfleet sql`, `summarize`, `preview` | done v0.2 | L-09, L-14 | table, CSV or JSON output; exit code 3 for a query error (D-027) |
| F-CLI-04 | `duckfleet plot`: the reduced data of a chart as CSV or JSON | done v0.3 | L-11, L-18 to L-21 | same reduction as the Plot page; summary and `--show-sql` on stderr; no chart or HTML export (D-037); `--databases` and `--family` plot a union view (0.4, D-046) |
| F-CLI-05 | `duckfleet fleet-tables`, `fleet-sql` | done v0.4 | L-09, L-12, L-14 | `--databases`, `--family`, `--all` (default); attach failures on stderr; exit code 3 for a query error (D-046) |
| F-UI-01 | Streamlit Overview page | done v0.1 | | blocking scan behind a progress bar; metrics, fleet table, families + matrix, table variants, identical content (0.4), diff, timeline, health, rejected |
| F-UI-02 | Database page | done v0.2 | | lazy tabs (D-028) |
| F-UI-03 | Plot page | done v0.3 | | **Plot this table** on the Database page hands the table over (D-038) |
| F-UI-04 | SQL page | done v0.2 | | D-029; scope *several databases* since 0.4 (D-047) |
| F-UI-05 | Scope control (one database or several) on the SQL and Plot pages | done v0.4 | L-12 | multiselect of up to 50 readable databases, default the strict family of the selected database, a Family selectbox (D-047) |
| F-DOC-01 | Feature matrix, limits, tuning, decisions, roadmap kept current | done v0.1 | | this file |
| F-OPS-01 | Windows and Linux CI | done v0.0 | | Phase 0 |
| F-OPS-02 | Multi-user, auth, hosting | out of scope | | |
| F-OPS-03 | Writes to scanned data | out of scope | | |
