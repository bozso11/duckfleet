# Roadmap

Each version gets a detailed plan (`docs/PLAN_vX.md`) before
implementation and a `CHANGELOG.md` entry after. Feature IDs refer to
`docs/FEATURES.md`.

Last updated: 2026-11-04.

## v0.1 Scan + fleet overview (in implementation)

Discovery with header check, parallel read-only profiling, profile cache,
catalog, strict/loose/per-table fingerprints, families, family diff,
time-window timeline, health list, path-token tags, CLI (`scan`, `report`,
`make-demo`, `paths`), Streamlit Overview page with blocking scan. Plan:
`docs/PLAN_v0.1.md`.

## v0.2 Single-database deep dive

Database page: table list with row counts, columns and types, per-column
summary (`SUMMARIZE`, lazy, cached in the catalog), paged preview, read-only
SQL page with result grid and CSV/Parquet download. F-DB-01..05, F-UI-02,
F-UI-04.

## v0.3 Visualisation

Plot page: pick table, x column (temporal first), several y columns, colour
or group-by, optional SQL filter; line, scatter, histogram, bar; DuckDB-side
time bucketing and downsampling to L-11 points. F-PLOT-01..03, F-UI-03.

## v0.4 Cross-database

Attach a chosen subset read-only; same table across files as one result
with a `database` column; overlay plots across files; content fingerprint
of small tables. F-X-01..02, F-PLOT-04, F-FLEET-08.

## Open options (not scheduled)

1. Non-blocking scan and scan cancel (F-SCAN-14, F-SCAN-15).
2. Identity query run against every file (F-FLEET-09), once the metadata
   layout is known.
3. Type normalisation for fingerprints (F-FLEET-10).
4. Column search across the fleet (F-FLEET-11).
5. Report export to Markdown or HTML (F-CLI-02).
6. Watch folder with automatic rescan (F-SCAN-17).
7. SQLite, Parquet, CSV as fleet members (F-SCAN-19).
8. Encrypted DuckDB files (F-SCAN-18).
9. Query cancel or timeout on the SQL page (F-DB-06); saved queries
   (F-DB-07).
10. Very large plots with Datashader, which would move the UI to Panel
    (F-PLOT-05).
11. Per-file time budget in the scan (F-SCAN-16).
12. Stable family labels across scans (TUNING family-labels).

## Out of scope

Writes to scanned data; multi-user deployment, authentication, hosting;
thousands of files or TB-scale data; general SQL IDE features; non-DuckDB
server databases. See `docs/SCOPE.md`.
