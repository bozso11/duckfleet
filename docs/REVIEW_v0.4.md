# Review of v0.4.0 against the plan

Date: 2026-11-08. Reviewed commits: `7f51bc7` (0.3.1) to `f01835d`
(0.4.0). Reviewer: the planning session (Opus-class, decision D-012), with
two independent read-and-reproduce passes (core; UI and documentation).
Reference: `docs/PLAN_v0.4.md` and `docs/REVIEW_v0.3.md`.

## Verdict

0.3.1 fixes every finding of the v0.3 review, including the severe shared
temp folder, and v0.4.0 implements the plan faithfully: the flock
connection attaches from catalog paths and locks down before any user SQL
runs, the sandbox holds against every escape tried, union views carry the
label on every branch, content hashes are stable, and the acceptance
checklist is true on re-check. The review found 13 confirmed defects: none
severe, four medium (a content hash that silently covers one column, a
file name that can never be attached, a member view that reads other
members' rows, and an upgrade path that crashes the Overview on a 0.3.x
catalog), the rest low. The fixes ship as 0.4.1 from this document; there
is no v0.5 plan.

## 1. Verified by the reviewer, not self-reported

1. CI: all seven commits (0.3.1, Phases 1 to 5, the changelog note) green
   on `ubuntu-latest` and `windows-latest`.
2. 798 tests pass and ruff is clean on Windows (Python 3.11.9, duckdb
   1.5.6, streamlit 1.66.1, plotly 7.1.0).
3. Fresh demo flock: `flock-tables --family F1` lists `config` and `events`
   in 3 of 3 members; `flock-sql --all` returns one row per readable file
   with the three unreadable files as attach failures on stderr; an
   `INSERT` through the flock is `read_only` and a `COPY TO` is `blocked`;
   `plot --family F1` buckets 3,000 rows to 6 hours with three `database`
   series; `report` shows the "Identical small tables" section.
4. The v0.3 crash fix: two threads sorting 12 M rows each on two files
   under a 64 MB limit finish with correct counts in one process, each in
   its own per-file temp folder.
5. UI on Windows in a browser: the Overview's Identical content section;
   the SQL page scope defaulting to the three F1 files, the flock query
   returning three rows on the first run after a scope switch; the Plot
   page in the same scope colouring by `database` with three series and a
   legend.
6. Conformance read of `flock.py`, `scope.py` and the 0.3.1 diffs against
   the plan.
7. X-01, X-02 and X-03 reproduced by the reviewer; the others by a review
   pass with a script or an AppTest.

## 2. Confirmed findings

Severity: medium = a wrong result or a plausible flock that cannot be used
as intended; low = hygiene, message or cosmetic.

| ID | Sev. | Where | Claim | Scenario | Fix |
|---|---|---|---|---|---|
| X-01 | medium | `profiler.py` content hash query | With a column named `df_row`, the bare identifier binds to the column, not the row alias, so the hash covers that one column and tables that differ elsewhere are called identical | Two files with `t(df_row, other)`, same `df_row`, different `other`: identical md5 (reproduced) | Choose the row alias from a loop over names not among the table's columns; test that the hash changes with `other` |
| X-02 | medium | `flock.py` `_RESERVED_ALIASES` | A file named `main.duckdb` or `main.db` gets the alias `main`, which DuckDB refuses as reserved, so it fails to attach in every selection | `main.duckdb` next to any other file: attach failure of kind `other` (reproduced) | Add `main` to the reserved aliases (the suffix rule gives `main_2`); parametrized test over `main`, `system`, `temp`, `memory` |
| X-03 | medium | `flock.py` `_build_views` | A member view whose base table is gone binds its bare table name to the in-memory union view of that table, so it returns the other members' rows under its own label with no error | Three files with `vw AS SELECT v FROM base`, `base` dropped in one: that member's branch yields the other two members' rows (reproduced) | Probe each member view with `SELECT * FROM <alias>.<schema>.<view> LIMIT 0` before any union view exists in memory; drop failing members from the union and list them in `view_failures`; test with these three files |
| X-04 | medium | `views/overview.py` Identical content, `catalog.content_variants`, `report` | A catalog written by 0.3.x lacks `tables.content_hash`, so the 0.4.0 Overview crashes after the section header until the flock is rescanned, and `report` exits 2 with a raw binder error | Upgrade and start the UI with `DUCKFLOCK_ROOT` set, or press Load existing catalog, before rescanning (reproduced by AppTest and CLI) | `CatalogReader` checks the catalog's columns once and raises a clear "older catalog, run `duckflock scan`" error; the page shows that as an info with the rest of the page intact |
| X-05 | low | `flock.py` `_build_views` | An alias equal to a non-`main` schema name of a member makes `CREATE VIEW "s"."t"` and the user's `SELECT * FROM s.t` ambiguous | Members `s.duckdb` and `u.duckdb` both holding schema `s` | Create the views as `memory."<schema>"."<table>"` and suffix a colliding alias before the lockdown |
| X-06 | low | `cli_flock.py` `flock-sql` | Attach failures are printed only after a successful query, so a query error hides them | `flock-sql ROOT "selec 1" --all` | Attach the summary to the raised `QueryError` and print the failures on the error path |
| X-07 | low | `cli_plot.py` | `plot --kind bar --x database` without `--color` also colours by `database` (duplicate column) | The F1 family | Skip the default colour when `x` is the label column (the UI already does) |
| X-08 | low | `views/sql.py`, `views/plot.py` | `FlockSummary.view_failures` is never shown in the UI, so a union view that could not be built vanishes from the Schema expander and the Table selectbox | A member view that fails to bind | Render `view_failures` as a warning next to the attach warning |
| X-09 | low | `views/database.py` Open in SQL | With the scope set to several databases, Open in SQL runs its template on the union view (undefined rows for a bare `LIMIT`) | Scope several on the SQL page, then Open in SQL from the Database page | Set the scope to one database in that branch, as Plot this table does |
| X-10 | low | `views/sql.py` Schema expander and placeholder | Both assume the label column is `database`; with a member table that has its own `database` column the label is `duckflock_database` and the placeholder groups by the wrong column | Such a table | Name `view.label_column` in the expander and build the placeholder from it |
| X-11 | low | `views/plot.py` flock caption | The caption counts attached databases, not those holding the table | Five files selected, a table present in two | Use the view's member count |
| X-12 | low | `views/overview.py` Identical content | The caption hardcodes "up to 10,000 rows" although the cap is configurable, and the empty state tells a user whose tables are all above the cap to scan again | A flock with no small tables | Read the cap from the scan config; reword the empty state |
| X-13 | low | docs | LIMITS 23 and 31, ARCHITECTURE Section 7 (`views/plot.py`, `views/overview.py`, the persist list) and the TUNING `plot-defaults` row lag the code | | Correct the texts |

## 3. Plausible, not reproduced

| ID | Claim | Decision |
|---|---|---|
| P-12 | `--databases` splits on commas, so a rel_path containing a comma cannot be selected | Document; accept repeated `--databases` flags as the escape |
| P-13 | `CatalogReader._connect` sets no `temp_directory`, so a spilling catalog query would create `catalog.duckdb.tmp` beside the catalog, the writer's build-target name | Set the per-file leaf there too (cheap, closes the gap) |
| P-14 | Every rerun of the SQL page in scope several opens a flock connection for the Schema expander, and Run opens another; a cost only with 50 members on a slow drive | Documented (D-047); leave |
| P-15 | The TUNING `plot-defaults` text says the first table shown has a time window and rows; `default_view` checks the temporal column only | Correct the text |

## 4. Phase 0 (0.3.1) completeness

Every item of `docs/REVIEW_v0.3.md` is fixed in code with a test: the
per-file temp leaf used by the query layer, the profiler and the catalog
writer (the shared-folder test dies and the leaf test passes), ladder
exhaustion, the y-column cap, the kind in the plot request, bar helper
names (fixed by unique names rather than qualification, D-043), seed
bounds, the interrupt wording on `InvalidInputException` only, non-finite
spans, cache isolation in both test suites, timed lookups, `TRY_CAST` on
temporal x, the sweep on first connection, and the test gaps.

## 5. Test gaps to close in 0.4.1

1. X-01 to X-07 have no test (hash changes with the other column; the
   reserved-name alias; the orphaned-view flock; the 0.3.x catalog
   message; the schema-name alias; attach failures on a query error; the
   bar default colour).
2. A spilling flock query that then lists the working directory (the
   lockdown tests list it without spilling).
3. `flock-sql` and `flock-tables` through a subprocess with a latin-1
   stdout; a plot on a `duckflock_database` view; `--family` above 50.
4. `test_cli_flock.py` series assertion weakened by an `or`.
5. The lockdown list lacks `glob()`, `read_text()`, `CREATE SECRET`,
   `IMPORT DATABASE` (all blocked by hand).

## 6. Checked and fine

1. Open sequence: connect with the flock leaf, catalog paths as escaped
   literals, external access off, configuration locked, then the views;
   the handle is yielded after the lockdown on every path. Afterwards
   `ATTACH` of a file, `COPY TO`, `read_csv`, `read_text`, `glob`,
   `INSTALL`, `LOAD`, `IMPORT DATABASE`, `CREATE SECRET`, every `SET` and
   `PRAGMA` are `blocked`; writes to aliases and union views are
   `read_only`; member file mtimes unchanged. Allowed and memory-only:
   `ATTACH ':memory:'`, `DETACH`, `USE`, `CHECKPOINT`, `CREATE TABLE`.
2. The flock temp folder is `<cache>/tmp/<pid>/flock-<8 hex>`; a 20 M-row
   sort spilled there and nothing into the working directory; the leaf is
   removed after success, a SQL error and a timeout.
3. Attach failures per member (locked, encrypted, corrupt, missing) with
   the rest usable; zero attaches raise `QueryError(other)`.
4. Aliases: collisions in rel_path order, leading digits, non-ASCII, the
   48-character cut, `'`, `;` and `--` in rel_paths, stable across
   selection orders.
5. Union views: the label on every branch, NULL fill, up-front VARCHAR
   casts by type family (12 pairs), tables in some members only, intact
   member views, the `duckflock_database` fallback, the L-12 cap.
6. Content hash: stable for identical content, differs for values,
   duplicates, names and order, `empty`, `None` above the cap and for
   views, version-1 profiles re-profiled once.
7. Flock plots: colour by `database`, `(other)` folding at the cap, attach
   failures as notes, the `(row order)` x on a view, the budget guard.
8. UI: results, downloads and plot requests carry the scope key, so a mode
   or selection change drops them; the flock plot cache is keyed by every
   member's live size and mtime; an error-status database in scope several
   gives an empty default and a message; NULL-filled union columns render
   under pandas 3; the Family selectbox fills and resets; 50 defaults fit
   the cap; every `TUNING` row has a marker and the reverse; every README
   command exists.
9. CLI: `--databases`, `--family`, `--all` resolution; exit codes 0 to 3;
   UTF-8 csv and json; no module-level UI import in core; every
   `duckdb.connect` in core is one of the known sandboxed paths.

## 7. Acceptance checklist re-check

| Item | Result |
|---|---|
| 1 CI green for 0.3.1 and every phase | Confirmed for all seven commits |
| 2 Phase 0 items | Confirmed by tests and the review pass |
| 3 `flock-tables`, `flock-sql`, the sandbox exits | Confirmed by the reviewer |
| 4 `plot --family F1` | Confirmed by the reviewer |
| 5 rescan and the report section | The section confirmed; the one-time re-profile by the test suite |
| 6 UI scope on both pages and the Overview section | Confirmed by the reviewer in a browser |
| 7 Docs and versions | Confirmed, with the corrections of X-13 |
