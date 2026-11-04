# Scope

## Problem

A folder tree holds up to a few hundred DuckDB files. Most share a table
structure, some do not, and conventional database tools connect to one
file at a time. The questions are, in order:

1. **Fleet overview**: how many files, what they contain, which structures
   exist and how many files have each, and whether same-structure files
   hold different data, different time windows or different runs.
2. **Deep dive**: for one chosen file, the tables, columns, a summary of
   each column, a data preview, and free read-only SQL.
3. **Visualisation**: plot selected columns of a table, time series first.

## In scope

1. Discover DuckDB files recursively under one root folder, by extension
   and by header check.
2. Profile every file read-only in parallel worker processes, using all
   but two CPU cores by default.
3. Fingerprint table structures at two strictness levels, group files into
   schema families, show how families differ, and show per-table variants.
4. Detect time windows from temporal columns and show them on a timeline.
5. Tag files from folder and file name tokens so runs and experiments can
   be told apart (heuristic, tunable).
6. Cache profiles so rescans only touch changed files.
7. Deep dive, SQL and plotting on one file (v0.2, v0.3), and across files
   of the same structure (v0.4).
8. Windows and Linux, Python 3.11, local single-user use.

## Out of scope

1. Writing to or editing any scanned database.
2. Thousands of files or terabyte-scale data; see `docs/LIMITS.md`.
3. Multi-user deployment, authentication, hosting.
4. General SQL IDE features: autocomplete, ER diagrams, schema migration.
5. Non-DuckDB databases such as Postgres or MySQL. SQLite, Parquet and CSV
   as fleet members are open options, not scope.

## Non-goals

1. Being a general-purpose DuckDB GUI. The fleet view is the reason the
   tool exists; the single-file views serve it.
2. Perfect heuristics. Time-window and run-identity detection are
   documented heuristics with tuning points, not guarantees.
