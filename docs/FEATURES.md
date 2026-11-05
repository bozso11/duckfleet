# Feature matrix

The first file an LLM or a person reads to learn what duckfleet does today.
Status values: `done vX` (shipped in version X), `planned vX` (in the
version ladder), `open option` (not scheduled), `out of scope`. Update the
row in the same commit that changes the behaviour.

Last updated: 2026-11-05 (0.1.1; v0.2 in progress).

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
| F-FLEET-08 | Content fingerprint of small tables (identical config detection) | planned v0.4 | L-07 | |
| F-FLEET-09 | Identity query run against every file (user-supplied SQL) | open option | | depends on real metadata layout |
| F-FLEET-10 | Type normalisation for fingerprints (INT vs INTEGER aliases) | open option | | TUNING type-normalization |
| F-FLEET-11 | Column search across the fleet ("which files have column X") | open option | | |
| F-FLEET-12 | Views and indexes in fingerprints | open option | | views listed, not fingerprinted |

## Single database

| ID | Feature | Status | Limits | Notes |
|---|---|---|---|---|
| F-DB-01 | Table list with row counts, columns and types | planned v0.2 | | |
| F-DB-02 | Per-column summary (SUMMARIZE style) | planned v0.2 | L-05 | lazy, cached in catalog |
| F-DB-03 | Paged data preview | planned v0.2 | L-08 | |
| F-DB-04 | Read-only SQL editor with result grid | planned v0.2 | L-09 | |
| F-DB-05 | Download results as CSV or Parquet | planned v0.2 | L-09 | |
| F-DB-06 | Query cancel or timeout | open option | | DuckDB `interrupt()` |
| F-DB-07 | Saved queries | open option | | |

## Visualisation

| ID | Feature | Status | Limits | Notes |
|---|---|---|---|---|
| F-PLOT-01 | Line, scatter, histogram, bar for chosen columns | planned v0.3 | L-11 | Plotly |
| F-PLOT-02 | Temporal x axis offered first; DuckDB-side time bucketing and downsampling | planned v0.3 | L-11 | |
| F-PLOT-03 | Colour or group-by column; optional SQL filter | planned v0.3 | | |
| F-PLOT-04 | Overlay the same table across files | planned v0.4 | L-12 | |
| F-PLOT-05 | Very large plots (Datashader) | open option | | would move the UI to Panel |

## Cross-database

| ID | Feature | Status | Limits | Notes |
|---|---|---|---|---|
| F-X-01 | Attach a chosen subset read-only in one connection | planned v0.4 | L-12 | |
| F-X-02 | Same table across files as one result with a `database` column | planned v0.4 | L-12 | |

## Interfaces and documentation

| ID | Feature | Status | Limits | Notes |
|---|---|---|---|---|
| F-CLI-01 | `duckfleet scan`, `report`, `make-demo`, `paths` | done v0.1 | | |
| F-CLI-02 | Report export to Markdown or HTML file | open option | | |
| F-UI-01 | Streamlit Overview page | done v0.1 | | blocking scan behind a progress bar; metrics, fleet table, families + matrix, table variants, diff, timeline, health, rejected |
| F-UI-02 | Database page | planned v0.2 | | |
| F-UI-03 | Plot page | planned v0.3 | | |
| F-UI-04 | SQL page | planned v0.2 | | |
| F-DOC-01 | Feature matrix, limits, tuning, decisions, roadmap kept current | done v0.1 | | this file |
| F-OPS-01 | Windows and Linux CI | done v0.0 | | Phase 0 |
| F-OPS-02 | Multi-user, auth, hosting | out of scope | | |
| F-OPS-03 | Writes to scanned data | out of scope | | |
