# Changelog

## Unreleased

## 0.5.0 (2026-11-09)

Renamed to duckflock-explorer; no functional change (decision D-051, recipe in
`docs/PLAN_rename.md`, deviations in D-052). The product name changes in the code, the
documentation and the repository, and the domain word "fleet" becomes "flock" everywhere,
including commands, classes, modules, environment variables and prose. Imports, commands and
environment variables change, which is why this is a minor version. The git history keeps the old
names and is not rewritten.

### What changed (Section 1 of the plan, condensed)

| Old | New |
|---|---|
| repository `bozso11/duckfleet` | `bozso11/duckflock-explorer` (the old URL redirects) |
| workspace project `duckfleet-workspace` | `duckflock-explorer-workspace` |
| distributions `duckfleet-core`, `duckfleet-ui` | `duckflock-core`, `duckflock-ui` |
| import packages `duckfleet_core`, `duckfleet_ui` | `duckflock_core`, `duckflock_ui` |
| console scripts `duckfleet`, `duckfleet-ui`; `python -m duckfleet_core` | `duckflock`, `duckflock-ui`; `python -m duckflock_core` |
| environment variables `DUCKFLEET_*` | `DUCKFLOCK_*` |
| cache folder `user_cache_dir("duckfleet")` | `user_cache_dir("duckflock")` |
| excluded directory default `.duckfleet` | `.duckflock` |
| union-view fallback label column `duckfleet_database` | `duckflock_database` |
| logger name `duckfleet` | `duckflock` |
| Streamlit page title and sidebar header | `duckflock explorer` |
| modules `fleet.py`, `cli_fleet.py`; tests `test_fleet.py`, `test_fleet_plot.py`, `test_cli_fleet.py` | `flock.py`, `cli_flock.py`; `test_flock.py`, `test_flock_plot.py`, `test_cli_flock.py` |
| CLI commands `fleet-tables`, `fleet-sql` | `flock-tables`, `flock-sql` |
| classes `FleetSelection`, `FleetSummary`, `FleetHandle`, `FleetQueryError`; functions `open_fleet`, `fleet_tables`, `run_fleet_query`, `export_fleet_query`, `describe_fleet`, `fetch_fleet_plot_data`, `make_demo_fleet`, `ensure_fleet_temp_dir`, ... | the same with `flock` |
| feature IDs `F-FLEET-nn` | `F-FLOCK-nn` |
| demo folder `demo-fleet` | `demo-flock` |
| the word "fleet" in prose, UI text and docs | "flock" |

### Upgrading

1. Run `uv sync --all-packages` (the lock file is regenerated with the new names). The commands
   are `duckflock` and `duckflock-ui`; scripts that set `DUCKFLEET_ROOT`, `DUCKFLEET_CACHE_DIR`,
   `DUCKFLEET_QUERY_THREADS` or `DUCKFLEET_QUERY_MEMORY_MB` must set the `DUCKFLOCK_` names.
2. An existing cache under the old folder is not reused: the first use after the upgrade scans
   again. The old cache folder (`duckfleet` under the user cache directory) can be deleted by hand.
3. A directory named `.duckfleet` is no longer skipped by the scan by default; `.duckflock` is.
4. A member table that has its own `database` column gets the label column `duckflock_database`
   (it was `duckfleet_database`).

### Checks (plan Section 5)

1. [x] `uv run ruff check .` clean; `uv run pytest`: 841 passed (2 min 48 s on Windows). No test
   needed a change other than the renamed names, the version and the page-title strings of the
   AppTest apps.
2. [x] CLI on a freshly generated flock (`duckflock make-demo ./demo-flock`, Windows,
   2026-11-09), with the outputs of 0.4.0 items 3 to 5 under the new names:
   - `duckflock paths ./demo-flock` shows the new cache folder
     (`%LOCALAPPDATA%\duckflock\Cache\roots\<hash>`); `scan`: 13 candidates, 2 rejected, 10 ok,
     3 failed, 0 reused; `report` prints `versions: duckflock 0.5.0, ...` and the content variant
     lines as in 0.4.0 item 5 (`main.config` 1 variant over 8 databases, `main.events` 8 variants
     over 8 databases, `main.notes` 1 over 2, `main.samples` 1 over 1).
   - `flock-tables ./demo-flock --family F1` lists `main.config` and `main.events` with 3 members
     of 3; stderr starts `duckflock: flock: 3 of 3 databases attached`.
   - `flock-sql ./demo-flock "select database, count(*) n from events group by 1 order by 1" --all`
     prints the 8 files with `events` (1000 rows each) and three `duckflock: attach failed:` lines
     on stderr (`broken/corrupt.duckdb` and `broken/highbit.duckdb` `corrupt`,
     `secure/encrypted.duckdb` `encrypted`).
   - `insert into events ...` exits 3 as `read_only` (`duckflock: query error (read_only): ...`);
     `copy events to 'x.csv'` exits 3 as `blocked` and writes no file.
   - `plot ./demo-flock --family F1 main.events --kind line --x ts --y value --max-points 300
     --format csv` exits 0 and prints 84 bucketed rows with the three `database` series; the
     stderr line reads `duckflock: plot: databases=3 strategy=time_bucket bucket=6 hours
     points=84 rows_scanned=3000 ...`.
3. [x] `duckflock-ui` starts (headless, port 8765). In the built-in browser the tab title, the
   Overview title and the sidebar header read "duckflock explorer". Only the start page was
   looked at by hand; the four pages render through AppTest in the suite.
4. [ ] CI on `ubuntu-latest` and `windows-latest`: recorded below once the run has finished.

### Deviations from the plan (D-052)

1. `docs/PLAN_rename.md`, the D-051 row and the former Unreleased line were left out of the text
   pass because they quote the old names on purpose. This table and D-052 quote them too, so
   `git ls-files | xargs grep -il fleet` lists `CHANGELOG.md`, `docs/DECISIONS.md` and
   `docs/PLAN_rename.md`; `git ls-files | grep -i fleet` lists nothing.
2. The sidebar header and the Overview title read "duckflock explorer" like the page title.
3. `README.md` and `CLAUDE.md` had no repository URL; a "Repository:" line was added to both.
4. The ignored local folder `demo-fleet/` was deleted and the demo generated again as
   `demo-flock`.

## 0.4.1 (2026-11-08)

Fixes from `docs/REVIEW_v0.4.md` (decision D-049; choices in D-050), shipped as one commit
directly from the review document: Section 2 (X-01 to X-13), Section 3 (P-12 to P-15) and the
test gaps of Section 5. Also in this release: `docs/REVIEW_v0.4.md` (review of 0.4.0, 13 confirmed
defects, four medium) and D-049 (planning documents only). The demo flock and its default numbers
are unchanged. Upgrading needs no rescan; a catalog written by 0.3.x still needs one for the
Identical content section (X-04). There is no v0.5 plan.

### Fixes, by review ID

1. **X-01 (medium)** The content hash query referenced the row through the alias `df_row`; a
   table with a column of that name bound the identifier to the column, so the hash covered that
   one column and tables that differed elsewhere were called identical. The alias is now the first
   of `df_row`, `df_row_2`, ... that is no column of the table (case-insensitive). The alias is not
   part of the hash, so every other hash is unchanged. A table with a `df_row` column keeps its
   old, wrong hash in the cache until its file is profiled again (`--full` or any change of the
   file); the profile schema version stays 2 (D-050, LIMITS 35).
2. **X-02 (medium)** `main` joins the reserved aliases, so `main.duckdb` becomes `main_2` and
   attaches (`memory`, `system`, `temp` were reserved already).
3. **X-03 (medium)** Every member view is probed (`SELECT * FROM <alias>.<schema>.<view> LIMIT 0`)
   after the lockdown and before the first union view exists. A view that no longer binds (its
   base table is gone) used to fall back to the in-memory union view of that name and return the
   other members' rows under its own label. Such a member is now left out of that union view and
   listed in `FlockSummary.view_failures` as `("main.vw", "<rel_path>: <message>")`; a table whose
   holders all fail has no union view.
4. **X-04 (medium)** `CatalogReader` checks the catalog's columns once (`_ADDED_COLUMNS`) and
   `content_variants()` raises `CatalogOutdatedError` ("older catalog: ... run `duckflock scan`")
   for a catalog without `tables.content_hash`. The Overview shows it as an info in the Identical
   content section with the rest of the page intact; `duckflock report` prints it with the command
   to run and exits 2 (it was a raw binder error).
5. **X-05** An alias equal to a non-`main` schema name of the chosen files made `CREATE VIEW
   "s"."t"` and the user's `SELECT * FROM s.t` ambiguous. Such a member is detached and attached
   again as `<alias>_2`, `_3`, ... before the lockdown, and the union views are created as
   `memory."<schema>"."<table>"`.
6. **X-06** A failing `flock-sql` query hid the attach failures. `run_flock_query` raises
   `FlockQueryError` (a `QueryError`) that carries the connection's summary, and the command prints
   the failures before the error (exit 3 as before).
7. **X-07** `plot --kind bar --x database` without `--color` also coloured by `database` (a
   duplicate column); the default colour is skipped when `--x` is the label column.
8. **X-08** `FlockSummary.view_failures` is shown as a warning next to the attach warning on the
   SQL page (Schema expander and above the result) and on the Plot page.
9. **X-09** **Open in SQL** on the Database page sets the scope to one database (a button callback,
   like **Plot this table**), so the template no longer runs on a union view.
10. **X-10** The Schema expander names each view's label column and the placeholder query is built
    from it (`duckflock_database` where a member table has its own `database` column), using
    `events` when there is one, else the first union view.
11. **X-11** The Plot page caption counts the databases that hold the table (the view's members),
    not the attached ones.
12. **X-12** The Identical content caption reads the cap from the scan's configuration; the empty
    state says that no base table is that small (or that the comparison was switched off) instead of
    asking for another scan.
13. **X-13** `LIMITS.md` 23 and 31, `ARCHITECTURE.md` Section 7 (`views/plot.py`,
    `views/overview.py`, the persist list), the data flow and component 18, and the `plot-defaults`
    row of `TUNING.md` match the code.
14. **P-12** `--databases` is repeatable, and a value that is exactly one catalog rel_path is taken
    whole, so a rel_path with a comma can be chosen (documented in LIMITS 34 and the option's help).
15. **P-13** `CatalogReader` connections use the catalog's own temp leaf under the cache
    (`<cache>/tmp/<pid>/<sha1(path)[:16]>`); an unwritable cache is ignored.
16. **P-14** Left as documented (D-047); now also in LIMITS 34.
17. **P-15** The `plot-defaults` text names `default_table` (time window and rows) and
    `default_view` (a temporal column) separately.

### Tests added (review Section 5)

X-01: the hash changes with the other column, the alias skips column names in any case, the alias
does not enter the hash. X-02: `main`, `system`, `temp`, `memory` (also upper case) attach next to
another file. X-03: the three-file flock of the review, a view that fails in every member, healthy
views stay. X-04: the reader, `report` (exit 2 and the message, then a scan) and the Overview with a
catalog whose `content_hash` column is dropped. X-05: the review's `s.duckdb`/`u.duckdb`, suffixes
that skip taken names, a schema in another case. X-06: the error carries the summary, the CLI prints
the failures first. X-07: the bar chart by `database`. X-08 to X-12: AppTest of both pages, the
Database page and the Overview. P-12: repeated flags and a comma in a rel_path. P-13: the reader's
config. Gaps: a spilling flock query that then lists the working directory; `flock-sql` and
`flock-tables` through a subprocess with a latin-1 stdout (csv and json); a plot over a
`duckflock_database` view; `--family` above 50; the series assertion of `test_cli_flock.py` without
its `or`; `glob()`, `read_text()`, `CREATE SECRET` and `IMPORT DATABASE` in the lockdown list.

### Deviations from the review's fix column

1. X-05: the alias is renamed after the attach, so it depends on the schemas of the chosen files,
   not only on the file names (TUNING `alias-rule`, D-050).
2. P-12: the review asks for repeated flags as the escape; a single flag whose value is exactly a
   rel_path works as well.

Choices the fix column left open (D-050): the probes of X-03 run after the lockdown (they are not
user SQL and show what a query will get) and before the first union view exists; X-01 does not
raise the profile schema version, because only tables with a column named `df_row` had a wrong hash
and a rescan of every file for them is not worth it.

### Open after 0.4.1

1. Linux and a real flock: as after 0.4.0 (AppTest in CI only; the numbers marked (TBC) in
   `docs/LIMITS.md` are untested on a real flock).
2. The UI does not show the attach failures of a query that failed (the Schema expander warns
   already); only the CLI does.
3. No LICENSE file (D-014), owner decision.

## 0.4.0 (2026-11-08)

Cross-database work on top of the 0.3 visualisation (plan: `docs/PLAN_v0.4.md`), after the 0.3.1
review fixes below. **Upgrading re-profiles every file once**: the profile schema version is 2, so
the first scan after the upgrade profiles all files again (the next one reuses them as before).

### What it does

1. **Flock connection** (`duckflock_core.flock`): the chosen databases (at most 50) are attached
   read-only, from catalog paths, to one in-memory DuckDB connection; then external access is
   turned off and the configuration locked; then one union view per table name holds the rows of
   every member with a `database` column (the rel_path). A member that cannot be attached is
   reported (locked, encrypted, corrupt, missing) and the others still work. Every connection has its
   own temp folder and is closed after the request.
2. **Scope on the SQL and Plot pages**: *One database* (as before) or *Several databases*, a
   multiselect that starts as the strict family of the selected database, a Family selectbox, a cap
   of 50 with a caption. SQL runs against the union views and the per-file aliases; plots are
   coloured by `database` and say how many files were folded into `(other)`.
3. **Content fingerprints** of tables up to 10,000 rows (md5 of the sorted rows) in the scan, the
   catalog, the report (**Identical small tables**) and the Overview (**Identical content**).
4. **CLI**: `flock-tables`, `flock-sql` and `plot --databases/--family`.
5. **0.3.1 fixes**: see the entry below.

### Acceptance checklist (plan Section 8)

1. [x] CI green on Windows and Linux for the 0.3.1 commit `7f51bc7` (run 42462230265) and for
   every phase commit: phase 1 `b9753d9` (42463125448), phase 2 `cb96242` (42463850373), phase 3
   `9e11c77` (42464481443), phase 4 `5dbee4e` (42465180749; the first Ubuntu attempt died
   downloading uv from GitHub with HTTP 504 before any test ran, the re-run of that job passed),
   and phase 5, the 0.4.0 commit `33520db` (42465975959; Ubuntu green, the first Windows attempt
   died on the same HTTP 504 download before any test, the re-run of that job passed: 798 tests).
   This entry's own commit changes documentation only.
2. [x] Phase 0: every item of `docs/REVIEW_v0.3.md` fixed with its test (entry 0.3.1; 75 tests).
3. [x] On the demo flock: `duckflock flock-tables ./demo-flock --family F1` lists `main.config`
   and `main.events` with 3 members each; `flock-sql ./demo-flock "select database, count(*) n from
   events group by 1 order by 1" --all` prints the 8 files with `events` (1000 rows each) and
   three attach failures on stderr (`broken/corrupt.duckdb` and `broken/highbit.duckdb` `corrupt`,
   `secure/encrypted.duckdb` `encrypted`); `insert into events ...` exits 3 as `read_only` (also
   `insert into <alias>.main.events ...`); `copy events to 'x.csv'` exits 3 as `blocked`.
4. [x] `duckflock plot ./demo-flock --family F1 main.events --kind line --x ts --y value
   --max-points 300 --format csv` exits 0 and prints 84 bucketed rows (6 hours) with the three
   `database` series; the stderr summary says `databases=3 strategy=time_bucket bucket=6 hours`.
5. [x] A scanned cache rewritten as 0.3.x left it (profile version 1) is profiled again in full by
   the first scan (13 profiled, 0 reused) and reused afterwards (the 3 failed files are always
   retried: 3 profiled, 10 reused); `report` prints `main.config: 1 content variant over 8
   databases, largest group 8 databases` (the demo's `config` is the same in all runs, so also
   within each experiment), `main.notes: 1 content variant over 2 databases` and `main.events: 8
   content variants over 8 databases, no two are identical`. Also
   `test_a_cache_from_version_one_is_profiled_again_in_full`.
6. [x] UI. Windows, by hand in the built-in browser with `duckflock-ui` (dark theme) on a freshly
   generated `make-demo` flock scanned from the page: the Overview has the section **Identical
   content** (`main.config` 1 variant over 8 databases, `main.events` 8 variants, `main.notes` and
   `main.samples` 1 variant each) and its expander; the SQL page in scope *Several databases* with
   the F1 family selected (3 of 10 readable databases, the placeholder query
   `SELECT database, count(*) FROM events GROUP BY 1` as the editor's placeholder) shows 3 rows
   (`0.00 s | 3 rows shown`) after typing and running that query; the Plot page keeps the scope and
   the selection, draws `main.events` as 3,000 points in 3 coloured series (legend: the three
   databases) with the caption `3 databases | 3,000 points, every one of 3,000 rows | 0.04 s`; no
   browser console errors. Through AppTest in CI (`test_ui_scope.py`, `test_ui_smoke.py`): the
   family default, the placeholder query on the F1 family (3 rows), a query across 6 files (6
   groups), a plot of `events` coloured by `database` (6 series, 3 folded beyond a cap of 3), the
   cap caption for a family of 60, the Identical content section.
7. [x] `FEATURES.md`, `LIMITS.md`, `TUNING.md`, `ARCHITECTURE.md`, `DECISIONS.md`, `ROADMAP.md`,
   `CHANGELOG.md`, `README.md` updated; versions 0.4.0.

### Deviations from the plan

1. `UNION ALL BY NAME` does not fail at `CREATE VIEW` for incompatible column types (120 type pairs
   tried); it fails when rows are read, which `LIMIT 0` and a NULL-constant probe do not show. The
   casts to VARCHAR are therefore decided up front by type family (numeric, temporal, else text);
   the plan's retry on a `BinderException` stays as a safety net (D-044; plan Section 10 item 1).
2. A member table with its own `database` column breaks the union binder, so that view labels with
   `duckflock_database` (`UnionView.label_column`; plan Section 10 item 3, D-044).
3. Additions to the contracts: `FlockSummary` (what `run_flock_query` returns instead of the
   handle), `FlockHandle.view_failures`, `UnionView.label_column`, `flock.describe_flock`, an error
   for a y or x column that is text in the union view (D-044, D-046).
4. The hash depends on column names, their order and the values as printed; a type counts only
   through its printed form (the plan says "types"; D-045). The demo flock is unchanged: its
   `config` is the same in all 8 files, so the Overview shows 1 variant for it (an existing demo
   test relies on that).
5. `--all` (the default of `flock-tables` and `flock-sql`) includes databases whose scan failed
   (D-046); a write to a union view is `read_only` (D-044).
6. W-05 is fixed with unique helper column names instead of qualifying every column (D-043).
7. The Overview section Identical content was built in Phase 2 with the report section (the plan
   lists it under Phases 2 and 4).
8. Plot page defaults: the colour is `database` except for a bar chart whose x already is it
   (D-047).

Outcomes of the plan's (TBC) items: Section 10 item 1 and 3 in D-044; item 2 in D-048.

### Open after 0.4.0

1. Linux: no manual browser session, only AppTest in CI (WSL has no uv).
2. In the browser only the dark theme, a family of 3 and the line chart were seen; the 50-database
   selection, the Family selectbox and the folded-files caption are checked through AppTest.
3. The numbers marked (TBC) in `docs/LIMITS.md` and the tuning points are untested on a real flock;
   the attach cost was only measured for 8 files (0.01 s) and is paid on every query and every
   rerun of the Plot page in scope *several databases*.
4. Nested columns of unequal types (a STRUCT with other fields, a LIST of another type) are shown
   as text in a union view; a smarter rule is a tuning point (`union-casts`).
5. No LICENSE file (D-014), owner decision.

### Build log by phase

### Phase 4: UI scope

CI: commit `9e11c77` (phase 3) was green on both OSes (run 42464481443).

1. **Scope** (`duckflock_ui/scope.py`): a radio *One database / Several databases* on the SQL and
   Plot pages, shared through the session state (`scope_mode`, `scope_rel_paths`). In *several
   databases* a multiselect (at most 50, status `ok` only) starts as the strict family of the
   selected database, or that database alone; a **Family** selectbox fills it with a family; a
   family above 50 is cut to the first 50 with a caption (L-12).
2. **SQL page** in *several databases*: the Schema expander lists the union views with their
   database counts and columns, and the aliases; the placeholder query is
   `SELECT database, count(*) FROM events GROUP BY 1`; results and CSV/Parquet downloads work as
   before (`flock_*` file names); databases that cannot be attached show as a warning above the
   result.
3. **Plot page** in *several databases*: the Table selectbox lists the union views, the colour
   defaults to `database` (a bar chart of the databases needs none), and the caption names the
   number of databases and the databases folded into `(other)` beyond the series cap. **Plot this
   table** on the Database page keeps scope *one database*.

### Phase 3: plots and CLI

CI: commit `cb96242` (phase 2) was green on both OSes (run 42463850373).

1. `plotdata.fetch_plot_data_on(con, db_name, spec, config)` runs the plot on an open connection;
   `fetch_plot_data` is unchanged for callers and `fetch_flock_plot_data(selection, paths, spec,
   config)` plots a union view (`db_name="memory"`); the members that could not be attached are
   `PlotData.notes`. A column that is text in the view because its types differ cannot be a y
   column or a line/scatter x (a clear error). `flock.describe_flock` returns the union views
   without a query.
2. `duckflock flock-tables ROOT [--databases a,b | --family F1 | --all]` lists each table with
   its member count and the members that lack it; `duckflock flock-sql ROOT SQL [...] [--max-rows]
   [--timeout] [--format table|csv|json]` runs SQL on the flock connection. Both print attach
   failures on stderr; the default selection is `--all`; more than 50 databases is a usage error.
3. `duckflock plot ROOT --family F1 main.events ...` (or `--databases a,b`) plots the union view
   and colours by `database` unless `--color` says otherwise (D-046).

### Phase 2: content hash

1. **Profile schema version 2: the next scan after upgrading re-profiles every file once.**
   `TableProfile.content_hash` holds `md5(string_agg(row::VARCHAR, chr(10) ORDER BY row::VARCHAR))`
   for base tables of up to `ScanConfig.content_hash_rows` rows (L-07, default 10,000; 0 turns it
   off), `"empty"` for an empty table, nothing for a larger table, a view or a table whose rows
   cannot be hashed (that is not a table error). The catalog has `tables.content_hash` and
   `CatalogReader.content_variants()`.
2. `duckflock report` has the section **Identical small tables** (per table: content variants and
   the largest group); the Overview page has the section **Identical content** after Table
   variants. On the demo flock `config` is the same in all 8 databases, `notes` in both exp_03 runs,
   and no two `events` tables are alike.
3. The hash depends on the column names, their order and the values as DuckDB prints them; a type
   matters only through its printed form (INTEGER 1 and VARCHAR '1' print alike; D-045).

### Phase 1: flock connection

CI: commit `b9753d9` was green on both OSes (run 42463125448).

1. `duckflock_core.flock`: `FlockSelection` (1 to 50 distinct rel_paths, `aliases()`),
   `open_flock` (an in-memory DuckDB, the members attached read-only, then external
   access off and the configuration locked, then one union view per table name with a
   `database` column), `flock_tables`, `run_flock_query`, `export_flock_query`. A member
   that cannot be attached is an `AttachFailure` (locked, encrypted, corrupt, missing, ...)
   and the others still work. Every flock connection has its own temp folder
   `<cache>/tmp/<pid>/flock-<8 hex>`, removed when it closes.
2. `UNION ALL BY NAME` does not fail when the view is created, whatever the column
   types: it fails when rows are read (`Unimplemented type for cast`), which neither
   `LIMIT 0` nor a probe with NULL constants shows. A column whose types differ across the
   members and are not all numeric or all temporal is therefore cast to VARCHAR up front
   (`UnionView.casts`); the plan's retry on a `BinderException` stays as a safety net (D-044).
3. A member table with its own `database` column breaks the union binder (duplicate
   names), so that view labels with `duckflock_database` (`UnionView.label_column`, D-044).
4. `query.classify_error`: an INSERT, UPDATE or DELETE on a view (the union views of a
   flock) is `read_only`; `execute_limited` and `export_query` are split into reusable parts
   (`fetch_arrow`, `write_arrow`, `require_export_format`).

## 0.3.1 (2026-11-08)

Fixes from `docs/REVIEW_v0.3.md` (plan: Phase 0 of `docs/PLAN_v0.4.md`, decisions
D-042 and D-043). The demo flock and its default numbers are unchanged. Also in
this release: `docs/REVIEW_v0.3.md` (review of 0.3.0), `docs/PLAN_v0.4.md` (plan for
0.3.1 and v0.4), decisions D-040 to D-042, limit L-22 (planning documents only).

### Fixes, by review ID

1. **W-01 (high)** Two DuckDB instances in one process shared one temp folder and
   the process died when both spilled. The temp directory is now one leaf per
   file per process, `<cache>/tmp/<pid>/<sha1(absolute path)[:16]>`
   (`tempdir.file_temp_dir`), derived identically by the query layer
   (`connection_config(config, db_path)`), the profiler and the catalog writer,
   so a scan and a query connection to one file still share a config (D-021).
   The pid sweep and the exit cleanup stay. Test: two threads, two files,
   `memory_limit_mb=64`, a 12 M-row sort each, in a child process
   (`_spill_threads.py`); with the old shared folder the same script ends in a
   segmentation fault on Windows (checked), with the leaves both counts are right.
2. **W-02** Above the 365-day rung `choose_bucket_seconds` derives a width in
   whole days (`N days`), so the data query never exceeds the budget and no
   `KeyError` or guard error is possible; test: 200 yearly rows over 200 years
   with a budget of 100.
3. **W-03** `PlotSpec.y` accepts at most 8 columns (plain message, usage error in
   `plot`); `max_selections=8` on the page; L-19 gains the clause.
4. **W-04** The remembered plot request carries its chart kind; another kind
   shows nothing until Plot is pressed.
5. **W-05** The bar query names its helper columns so that no column of the table
   can collide (`df_c`, `df_rk`, `df_m` and the review's list of hostile names are
   tested) and aliases its source subquery (D-043 item 2).
6. **W-06** `seed` is bounded to 0 .. 2^31 - 1.
7. **W-07** The `interrupt error` wording counts for `InvalidInputException` only;
   `select 'interrupt error'::INTEGER` is a `sql` error (in the V-06 test list).
8. **W-08** A non-finite span (a range beyond a double) falls back to the first
   rows with a note, a non-finite histogram width is skipped with a note, and no
   bare `ValueError` can leave `fetch_plot_data` (it becomes `QueryError`).
9. **W-09** An autouse fixture in both `conftest.py` files points
   `default_cache_dir` at a folder of the run; a test asserts that the real cache
   directory of this process is untouched after a query and a plot with a bare
   `QueryConfig()`.
10. **W-10** The database name and the column lookup of `fetch_plot_data` run
    through `execute_limited` under the shared deadline (`execute_limited` takes
    bound `params`).
11. **W-11** Documented (LIMITS 29): a real `(other)` or `(null)` value merges with
    the fold.
12. **W-12** DATE and TIMESTAMP_S/MS/NS x use `TRY_CAST`; rows that do not convert
    are counted in the "left out" note (a year-1,000,000 date no longer fails the plot).
13. **W-13** A histogram of several columns counts the columns as series for the
    legend rule (a legend for two or more, one colour per column).
14. **W-14** The histogram caption counts the drawn bins and the binned rows.
15. **W-15** A problem with the choices (no y column, a spec error) shows as a
    warning without a kind prefix.
16. **W-16** Documented in D-038: the `(other)` bar category keeps the series colour
    when a colour column is set.
17. **W-17** `ARCHITECTURE.md` covers **Plot this table** and `common.file_state`;
    LIMITS 23 names every place the environment overrides are read; `# TUNING`
    markers and rows for `plot-defaults` (`LOW_CARDINALITY`, `default_table`) and
    `chart-render` (`_WEBGL_FROM`, `_MARKERS_UP_TO`, the opacity threshold).
18. **P-08** Stale `tmp/<pid>` folders are also swept on a process's first connection.
19. **P-09, P-10** Documented (LIMITS 32 and 33).
20. **P-11** `PlotPlan.series_kept` is removed; the budget guard also covers
    histogram and bar (documented in L-11 and the module docstring).

### Tests added (review Section 6)

Two concurrent spilling instances; ladder exhaustion; hostile column names
(`df_c`, `df_rk`, `df_m`, `x`, `s`, `n`, `cat`, `rk`, `v0`, `yi`, `b`, a quote, a
space, `é`) on all four kinds; TIMESTAMP_S/MS/NS x; empty and one-row tables for
every kind; `seed` bounds; `plot --format csv` and `json` through a subprocess
with a latin-1 stdout; a guard that no test touches the real cache.

### Notes

1. `PROFILE_SCHEMA_VERSION` is unchanged in 0.3.1, so a rescan reuses all profiles.
2. 660 tests (585 in 0.3.0) and `ruff check .` clean on Windows (Python 3.11.9, duckdb
   1.5.6, streamlit 1.66.1). CI: commit `7f51bc7` was green on `ubuntu-latest` and
   `windows-latest` (run 42462230265).

## 0.3.0 (2026-11-08)

Visualisation on top of the database deep dive (plan: `docs/PLAN_v0.3.md`),
after the 0.2.1 review fixes below.

### What it does

1. **Plot page** (`duckflock-ui`): pick a database and a table, a chart kind
   (line, scatter, histogram, bar), x (temporal columns first, numeric next,
   or the row order), y columns, a colour column for series, an SQL filter and
   the point budget. DuckDB reduces the data (time buckets with mean, min and
   max, numeric buckets, histogram bins, a reservoir sample for scatter,
   folding of series and bar categories), so the browser never receives more
   than the budget. The chart follows fixed rules (D-032): an eight-colour
   palette in series order, `(other)` in grey, one y axis or one panel per y
   column, the min to max band at 20 % opacity. The page shows a caption about
   the reduction, the plotted data as a table, a CSV download and the SQL.
2. **Plot this table** on the Database page hands the table over.
3. **CLI** `duckflock plot ROOT DATABASE SCHEMA.TABLE --kind ...` prints the
   same reduced data as CSV or JSON, a summary line on stderr and, with
   `--show-sql`, the query (D-037).
4. **Core**: `duckflock_core.plotdata` (`PlotSpec`, `plan_plot`, `build_sql`,
   `fetch_plot_data`, `PlotData`); `duckflock_ui.charts` holds the colours,
   the template and the Plotly figures.
5. `duckflock make-demo --big` adds `exp_04/run_01/metrics.duckdb` (200,000
   `events` rows over 30 days); without the flag the demo flock is unchanged.

### Acceptance checklist (plan Section 8)

1. [x] CI green on Windows and Linux for the 0.2.1 commit `ab4e720` (run
   42433450823) and for every v0.3 phase commit: phase 1 `13aa762` (run
   42434323748), phase 2 `960d92d` (42434671639), phase 3 `ad8268d`
   (42436243430), and phase 4 `db8b58b` (42436587529), the 0.3.0 commit
   itself, was green on both OSes too (confirmed after this entry was written).
2. [x] Phase 0: a spilling query (`memory_limit_mb=64`) writes nothing beside
   the scanned file while a thread lists the folder, and the temp files appear
   under the cache (`test_a_spilling_query_writes_nothing_beside_the_scanned_file`);
   a timeout during the fetch of text and list columns is `timeout` in 5 of 5
   runs; CSV and JSON output of `日本語 é` round-trips through a subprocess with
   a latin-1 stdout as UTF-8 bytes with LF line ends; the SQL page honours a
   timeout of 1 s set together with Run (`test_the_timeout_option_stops_a_runaway_query_and_the_next_one_runs`);
   the preview shows a row inserted after the scan
   (`test_preview_shows_rows_added_after_the_scan`).
3. [x] `duckflock plot ./demo-flock exp_01/run_01/metrics.duckdb main.events
   --kind line --x ts --y value --max-points 100 --format csv` prints 56
   bucketed rows (a header and 56 rows, fewer than 100), the stderr summary says
   `strategy=time_bucket bucket=3 hours points=56 rows_scanned=1000`;
   `--show-sql` prints `SELECT time_bucket(INTERVAL '3 hours', "ts") ...`.
4. [x] UI. Windows, by hand in the built-in browser with `duckflock-ui` (dark
   theme, detected automatically) on a freshly generated `make-demo --big` flock
   scanned from the sidebar: Plot page, `main.events` of
   `exp_01/run_01/metrics.duckdb`: a line chart raw ("1,000 points, every one of
   1,000 rows"); the same with max points 100 ("56 points from 1,000 rows:
   bucketed to 3 hours, mean with min to max band", three traces, the band
   filled to the min trace); a scatter coloured by `run_id` (one series, 8 px
   markers, "reservoir sample of 100 of 1,000 rows"); a histogram of `value`
   (50 bars summing to 1,000, no borders, `bargap` 0.1); a bar of `config.key`
   counts (three categories of 1); the **Plotted data** table and **Download
   CSV** (the media URL of the button served the CSV with the header and 56
   rows, `text/csv`, no CR; the file was not saved); a filter `no_such_column >
   1` showed `sql: Binder Error: Referenced column "no_such_column" not found
   ...` and no chart; **Plot this table** on the Database page landed on the
   Plot page with `main.events`, line, x `ts` and y `value` preselected. Through
   AppTest in CI (Windows and Linux): `test_ui_plot.py` covers all of these
   plus panels, the series cap and the hand-off through one session state,
   `test_charts.py` the colours, the band traces and the single y axis.
5. [x] `make-demo --big` (tests: `test_the_big_flag_adds_one_large_table_and_nothing_else`);
   `plot` on `exp_04/run_01/metrics.duckdb` with the default budget finishes in
   0.06 s and reports `bucket=1 minute` (43,200 points from 200,000 rows); the
   page shows the same caption in 0.05 s; bucketing by the row order of the same
   table takes 0.06 s.
6. [x] `FEATURES.md`, `LIMITS.md`, `TUNING.md`, `ARCHITECTURE.md`,
   `DECISIONS.md`, `ROADMAP.md`, `CHANGELOG.md`, `README.md` updated; versions
   0.3.0.

### Deviations from the plan

1. `USING SAMPLE` is applied before `WHERE`, so the scatter sample wraps the
   filtered subquery (plan Section 4 item 3 verified only the syntax; D-036).
2. Float values enter the SQL as exact DOUBLE literals; a plain `0.1` is a
   DECIMAL and pushed a histogram minimum into bin -1 (D-036).
3. A bucketed line counts one more bucket per series than the plan's formula
   (a span can straddle a grid boundary), so the budget guard cannot fire
   (D-036).
4. `temp_directory` must come after `enable_external_access` in the connect
   config (plan Section 4 item 7 holds for that order only; D-035).
5. The chart kind and the table selector sit above the form, because the
   choices below depend on them (D-038); the layout option is a radio as planned.
6. Streamlit's frontend paints the plot area and font of a figure whose layout
   leaves them out, also with `theme=None`; the charts set both on the layout
   (D-038).
7. `classify_error` matches `interrupt error`, not the bare word; the timeout
   waits in slices so Ctrl-C works on Windows (D-035).
8. `plan_plot`, `PlotPlan` and `PlotData` have extra optional fields (`notes`,
   `row_limit`, bucket details); the contract of the plan is unchanged (D-036).
9. The plan's Phase 2 (CLI) and Phase 3 (page) were committed in that order.

Outcomes of the plan's (TBC) items: Section 10 item 1 and 2 in D-038, item 3 in
D-035, item 4 in D-037, item 5 in D-036 (summary in D-039).

### Open after 0.3.0

1. Linux: no manual browser session, only AppTest in CI (WSL has no uv).
2. In the browser only the dark theme and the single-panel layout were seen;
   the light theme and the panels are checked through the figures in the tests.
3. The numbers marked (TBC) in `docs/LIMITS.md` and the tuning points (bucket
   ladder, series cap, panel ratio) are untested on a real flock.
4. Open options (docs/ROADMAP.md): an HTML export of a chart from the CLI, log
   axes, zoom that refetches finer buckets, saved plot specs, annotations,
   Datashader.
5. `row_number() over ()` for the row-order x was only timed on 200,000 rows.

## 0.2.1 (2026-11-08)

Fixes from `docs/REVIEW_v0.2.md` (plan: Phase 0 of `docs/PLAN_v0.3.md`, decision
D-031). The demo flock and its scan numbers are unchanged.

### Fixes, by review ID

1. **V-01** (high): DuckDB's temp directory is `<cache dir>/tmp/<pid>` on every
   connection duckflock opens (query layer, profiler, catalog writer), no longer
   `<database path>.tmp` beside the scanned file. The folder is per process,
   created before connecting, removed at exit; `run_scan` removes folders of
   processes that no longer exist. Found on the way: DuckDB refuses the temp
   directory when `enable_external_access` is already off, so the key must come
   after it in the config dict (D-035). Test: a spilling query with
   `memory_limit_mb=64` while a thread lists the scanned folder.
2. **V-02**: an interrupt during the fetch of VARCHAR or list columns is a
   `timeout` (5 of 5 runs), not an `sql` error.
3. **V-03**: `--format csv` and `json` are UTF-8 with LF line ends whatever the
   console code page (tested through a subprocess with a latin-1 stdout);
   `PYTHONUTF8=1` is documented for `table` (README, LIMITS 26).
4. **V-04**: Ctrl-C during a query interrupts it; the timeout waits in 0.25 s
   slices because a Windows lock wait is not interrupted by Ctrl-C.
5. **V-05**: the Database page's preview cache is keyed by the file's live size
   and mtime; a row inserted after the scan shows in the preview.
6. **V-06**: `classify_error` decides by exception type first, so text echoed from
   the SQL (`select 'read-only mode'::INTEGER`) is an `sql` error.
7. **V-07**: JSON output has no `NaN` (null), BLOBs as hex, tuples as lists; CSV
   renders arrays and structs as JSON text.
8. **V-08**: the unreachable `(no result set)` branch is gone.
9. **V-09**: the Summary tab shows "Recompute" in the run that computed the
   summary (`st.rerun()` after a successful first compute only).
10. **V-10**: on the SQL page the Options inputs and **Run** share a form, so Run
    submits what was typed.
11. **V-11**: `persist_state="session"` replaces `common.keep` / `KEPT_KEYS`
    (every kept widget supports it, checkboxes included); a test visits the
    three pages in turn against one session state.
12. **V-12**: **Prepare CSV** and **Prepare Parquet** are disabled, with a
    caption, while the editor text differs from the query behind the result.
13. **P-05**: the profiler's connection has the query layer's sandbox
    (external access off, configuration locked), threads and memory aside.
14. **P-07**: the `get_or_compute` docstring is correct.

### Tests added (review Section 6)

Timeout during a VARCHAR/list fetch; nothing beside a scanned file under memory
pressure; non-ASCII CLI output as bytes; a real cancellation test for P-02 (a
marker file written inside the spawned workers counts the files that really
ran: at most 8 of 20 after the consumer stops, 20 without `cancel_futures`); the
catalog writer's config (R-07); a 2-worker run with a valid tag pattern that
matches nothing (R-03); summaries of a nested-only table, a binary-only table
and a view whose base table was dropped; the stale-folder sweep and the
workers' own clean-up.

### Documentation

ARCHITECTURE (tempdir module, storage layout, shared config rule, UI state),
LIMITS 23 (wording) and new 24 to 26, L-14 (interrupt delay, P-06), D-024
wording and D-035, `# TUNING(timeline-padding)` marker and row, README
(`PYTHONUTF8=1`). The CI result of the 0.2.0 commit below was confirmed green
on both OSes afterwards (run 42218976791).

## 0.2.0 (2026-11-06)

Database deep dive and read-only SQL on top of the flock overview (plan:
`docs/PLAN_v0.2.md`), after the 0.1.1 review fixes below.

### What it does

1. **Database page** (`duckflock-ui`): pick one database of the scanned
   flock; its tables with row counts and time windows; per table the columns
   with the scan's cheap statistics, a per-column summary (DuckDB
   `SUMMARIZE`, computed on request, cached as JSON sidecars until the file
   changes) and a paged, sortable preview with a column selector. **Open in
   SQL** hands a query to the SQL page.
2. **SQL page**: a read-only, sandboxed editor on one database (no writes, no
   file access, no extensions, no `SET`), a row limit, a timeout enforced with
   `interrupt()`, CSV and Parquet downloads and a history of 20 queries.
3. **CLI**: `duckflock sql`, `summarize`, `preview`.
4. **Core**: `duckflock_core.query` (sandboxed connections, `run_query`,
   `export_query`, `preview_table`), `duckflock_core.summary`,
   `store.SummaryStore`; `pytz` is a core dependency, `pyarrow` the optional
   extra `export`.

### Acceptance checklist (plan Section 8)

1. [x] CI green on Windows and Linux for the 0.1.1 commit (`dbfd7bf`) and for
   every v0.2 phase commit (`adb4804`, `fe6ffea`, `506dcdf`, `5a742db`,
   `2cbcb14`); the run of the 0.2.0 commit itself (42218976791) was green on both
   OSes, confirmed after the entry was written.
2. [x] Phase 0: the demo scan reports 16 walked, 13 candidates, 2 rejected, 10
   ok, 3 failed (`corrupt`, `corrupt`, `encrypted`), families unchanged (checked
   with the CLI on a fresh demo flock); the `infinity` test passes
   (`test_infinite_timestamps_never_open_a_time_window`); `duckflock scan
   --tag-pattern "("` exits 1 with `invalid tag pattern '(': missing ),
   unterminated subpattern at position 0`; the AppTest with 72 patched CPUs
   renders (`test_the_page_renders_on_a_machine_with_72_cpus`).
3. [x] `duckflock sql ./demo-flock exp_01/run_01/metrics.duckdb "select
   count(*) from events"` prints 1000; an `INSERT` exits 3 with `read_only`; a
   `COPY ... TO` exits 3 with `blocked` and writes no file.
4. [x] `duckflock summarize ./demo-flock exp_01/run_01/metrics.duckdb
   main.events` prints 3 column rows with min, max, avg and quantiles; the
   second call prints `(cached <time>)`; `--refresh` recomputes.
5. [x] `duckflock preview ... main.events --rows 5 --offset 5 --order-by ts
   --desc` prints 5 rows in descending time order.
6. [x] UI. Windows, by hand in the built-in browser with `duckflock-ui` on the
   demo flock: Overview, Load existing catalog, Database page for
   `exp_01/run_01/metrics.duckdb` with 2 tables, the Columns tab (null share
   0 %), a Summary that was computed (and recomputed) and shows the same
   `computed` time after leaving and returning to the page, a Preview sorted
   by `ts` descending with a column subset; **Open in SQL** landed on the SQL
   page with `SELECT * FROM "main"."events" LIMIT 100` for the same database;
   max rows 5 gave the cut warning with 5 rows; **Prepare CSV** and **Prepare
   Parquet** offered downloads of 4,809 and 2,654 bytes (100 rows); an
   `INSERT` showed the `read_only` message; the cross-join with a timeout of
   1 s showed the `timeout` message and the next query returned 1000. The
   browser's save of a download was not clicked; the Parquet bytes are read
   back in the AppTest. Linux: through AppTest in CI on Ubuntu
   (`test_ui_database.py`, `test_ui_sql.py`), no manual session.
7. [x] `TIMESTAMP WITH TIME ZONE` columns fetch, preview and query without
   error (`test_result_columns_types_and_rows`,
   `test_preview_of_a_timestamptz_table`, `test_timestamptz_values_display`).
8. [x] `FEATURES.md`, `LIMITS.md`, `TUNING.md`, `ARCHITECTURE.md`,
   `DECISIONS.md`, `ROADMAP.md`, `CHANGELOG.md`, `README.md` updated; versions
   0.2.0.

### Deviations from the plan

1. `TimeZone` is not set on the query connection: the connect config does not
   accept it and a `SET` before locking reaches only the first connection of
   an instance (D-026). `to_arrow_table` has a `fetch_arrow_table` fallback.
2. The CLI writes CSV and JSON from the fetched rows (no pyarrow), `preview`
   shows 20 rows by default, and an unknown database or table is exit 1
   (D-027).
3. The Database page uses lazy tabs, which needs `streamlit>=1.66` (D-028);
   `st.switch_page` works under `st.navigation`, so `st.page_link` is only used
   for the way back to the Overview.
4. SQL page: the row limit input accepts 1 to 100,000 (the plan says 100, its
   acceptance case needs 5), the downloads export the text in the editor
   (D-029); the database selectbox lists databases with tables first.
5. Values the grid cannot hold are converted to text in Python, not cast in the
   preview SQL (D-030).
6. A CSV download of a result with nested columns fails with a hint to use
   Parquet (LIMITS 21).

### Open after 0.2.0

1. Linux: no manual browser session, only AppTest in CI (WSL has no uv).
2. The numbers marked (TBC) in `docs/LIMITS.md` and the tuning points are
   untested on a real flock; summaries of tables above the sampling threshold
   are tested with a low threshold only.
3. `incompatible_version` is still classified by message only.
4. UI details: the Summary tab shows "Compute summary" until the next rerun
   after a first computation; the selected tab and the preview inputs are not
   kept when leaving the Database page.
5. No LICENSE file (D-014), owner decision.

### Build log by phase

### Phase 5: SQL page

1. The SQL page (`views/sql.py`): the database selectbox shared with the other
   pages, an expander **Schema** (tables, columns, types from the catalog), the
   editor, **Run** and **Clear**, an expander **Options** (max rows shown, timeout
   in seconds), a result grid with a caption (seconds, rows shown, number of
   statements) and a warning when the result was cut. Errors show the kind and
   DuckDB's message with a hint: `read_only` (duckflock opens databases
   read-only), `blocked` (file access, extensions and settings are disabled),
   `timeout` (raise the timeout or narrow the query).
2. **Prepare CSV** and **Prepare Parquet** run the text in the editor through
   `export_query` (up to 1,000,000 rows, built in memory) and offer a download
   button named `<database stem>_<yyyymmdd-hhmmss>.<ext>`; a truncated export
   warns with the cap, and a prepared file is only offered while the editor
   still holds the text it was made from.
3. A history of the last 20 distinct queries (newest first), loaded into the
   editor from a selectbox. The last result and the prepared download live in
   the session state; the result is cleared when the database changes.
4. Defaults of the database selectbox: databases with tables first, empty ones
   next, failed ones last (the first entry is shown by default).
5. Choices the plan left open are in D-029.
6. Checked by hand on Windows in the built-in browser: no-catalog state with a
   link to the Overview, a syntax error, max rows 5 with the cut warning, a
   prepared CSV.

### Phase 4: Database page

1. `st.navigation` now has three pages (`nav.py`): Overview, Database, SQL.
   The Database page (`views/database.py`) shows one database of the scanned
   flock: header (size, families, tags, time window, health), the tables with
   row counts and time windows, and three tabs: **Columns** (scan statistics
   with the null share), **Summary** (a cached `SUMMARIZE`, computed on
   request, **Recompute** afterwards) and **Preview** (column selector, sort,
   descending, page number, one page of `preview_rows` rows). **Open in SQL**
   sets `sql_text` and switches to the SQL page.
2. A database that failed in the scan shows its error and stops; a file that
   vanished since the scan is explained instead of failing; a file whose size
   or mtime differ from the catalog is marked "changed since the last scan".
3. `query_access.py` (one `QueryConfig` per process, optional env overrides
   `DUCKFLOCK_QUERY_THREADS` and `DUCKFLOCK_QUERY_MEMORY_MB`; cached preview
   pages; summary helpers; result frames that never break the grid) and
   `common.py` (the helpers the Overview had, plus keeping widget state across
   pages). `views/sql.py` is a stub until Phase 5.
4. `CatalogReader.database(rel_path)`.
5. `streamlit>=1.66` (lazy tabs, D-028); `uv.lock` refreshed. The Overview's
   Workers and Max files defaults live in the session state.
6. Checked by hand on Windows in the built-in browser (`duckflock-ui`):
   Overview to Database to SQL through `st.switch_page`, the database
   selection survives the page changes, the summary computes and shows.

### Phase 3: CLI

1. New commands `duckflock sql ROOT DATABASE SQL`, `summarize ROOT DATABASE
   SCHEMA.TABLE` and `preview ROOT DATABASE SCHEMA.TABLE` (module
   `cli_query.py`). `DATABASE` is the `rel_path` listed by `report`; the
   absolute path comes from the catalog. Output as an aligned table, CSV or
   JSON lines. Exit codes: 0 ok, 1 usage, 2 fatal, 3 query error (kind and
   message on stderr).
2. `summarize` prints `(computed in N s)` or `(cached <computed_at>)` on
   stderr; `--refresh` recomputes.
3. Choices the plan left open are in D-027.

### Phase 2: summaries

1. `duckflock_core.summary`: `ColumnSummary`, `TableSummary` (schema version
   1) and `summarize_table` (DuckDB `SUMMARIZE`, on a sample above
   `stats_row_threshold` rows, under the query timeout). A failure becomes a
   summary with `error` set instead of an exception.
2. `store.SummaryStore`: JSON sidecars
   `roots/<root_key>/summaries/<file_key>/<table_key>.json`, valid while the
   database file's size and mtime, the summary schema version and the DuckDB
   version match (D-020); `get_or_compute(..., refresh=False)`. Error
   summaries are saved but never reused. `ProfileStore.prune` removes the
   summaries folder of every pruned profile.
3. The helpers `as_query_errors`, `database_name` and `execute_limited` of
   `query.py` lost their underscore (shared with `summary.py`).

### Phase 1: query layer

1. `duckflock_core.query`: `QueryConfig`, `connection_config`, `open_readonly`
   (read-only, extension autoload off, external access off, configuration
   locked, fixed threads and memory), `run_query`, `export_query` (CSV and
   Parquet in memory through Arrow), `preview_table`, `table_columns`,
   `QueryResult`, `QueryError` (kinds `sql`, `read_only`, `blocked`, `timeout`,
   `other`). Every query runs under a timeout enforced with `interrupt()`.
2. `sqlutil.py` holds `quote_ident` and `qualified_name` (moved out of
   `profiler.py`).
3. `pytz>=2024.1` is a core dependency (D-022) and `pyarrow` the optional
   extra `export`; `uv.lock` refreshed. `TIMESTAMP WITH TIME ZONE` rows now
   fetch.
4. Outcomes of the plan's open items: `TimeZone` is not accepted in the connect
   config and a `SET TimeZone` before locking only reaches the first
   connection of a process, so the session zone stays the machine's (D-026);
   `to_arrow_table` is used with a `fetch_arrow_table` fallback.
5. A CSV export of a result with list, struct or map columns fails with a
   message that points to Parquet (the Arrow CSV writer has no nested types).

## 0.1.1 (2026-11-05)

The fixes of `docs/REVIEW_v0.1.md` (Phase 0 of `docs/PLAN_v0.2.md`); no new
features. Review IDs in brackets.

### Fixed

1. [R-01] A file with the DuckDB magic followed by garbage (a storage version
   above 2^63 - 1) made the catalog build fail, and every later scan of that
   root failed the same way. `databases.storage_version` is now `UBIGINT` and
   `read_header` reports no version for such a value. New demo file
   `broken/highbit.duckdb`, profiled as `corrupt`.
2. [R-02] `infinity` and `-infinity` in TIMESTAMP, DATE and TIMESTAMPTZ
   columns no longer open a time window of year 1 to year 9999: the temporal
   `min`/`max` skip non-finite values (exact and sampled query), and
   `_as_datetime` returns `None` for `datetime.min/max` and `date.min/max`.
3. [R-03] An invalid tag regex killed the scan. `ScanConfig.tag_patterns` is
   validated (it must compile and have the named groups `key` and `value`);
   `profile_database` turns an invalid config into an error profile and falls
   back to no tags when tag extraction itself fails. New CLI option
   `scan --tag-pattern REGEX` (repeatable, replaces the defaults); an invalid
   pattern exits 1 with a message naming it.
4. [R-04] On a machine with 64 or more CPUs the default worker count exceeded
   the UI input's maximum (61) and the page raised. `resolved_workers()` is
   capped at `MAX_POOL_WORKERS`.
5. [R-05] A temporal value outside Python's datetime range (for example year
   10000) silently dropped out of the time window. The table now carries the
   error `temporal value outside the supported range in column <name>`, its
   window is cleared, and the database gets `table_error:<schema>.<table>`.
6. [R-06] The cache directory is no longer walked, so a `--cache-dir` inside
   the root does not turn the catalog into a flock member.
7. [R-07] The catalog writer connects with the standard settings (2 threads,
   1 GB, extension autoload off).
8. [R-08] The Overview timeline draws a single-instant window with a minimum
   width (the larger of 1 hour and 0.5 % of the flock span), the hover text
   says "single instant", and the caption lists those databases.
9. [R-09] A scan subprocess that exits 0 without a `finished` event is
   reported as such, and stray stdout lines appear in the log expander.
10. [R-10] Removed the vacuous assertion in `test_catalog.py`.
11. [P-01] Cached profiles are reused only when `duckdb_version` matches the
    installed DuckDB (one full re-profile after an upgrade).
12. [P-02] The process pool is shut down with `cancel_futures=True` in a
    `try/finally`, and `run_scan` closes the profile stream, so an error in
    the consumer or Ctrl-C does not profile every queued file first.

### Documentation corrections

1. [R-11] An `encrypted` fixture is producible: the demo flock has
   `secure/encrypted.duckdb` and a test asserts `error.type == "encrypted"`.
   D-018, F-SCAN-09 and the 0.1.0 open item 1 are corrected.
2. [R-12] `docs/LIMITS.md` L-02 and `docs/ARCHITECTURE.md` Section 3 state the
   cap of 61 workers.
3. [R-13] The plan's Phase 4 test text (a second run reuses every profile)
   cannot hold for the demo flock because failed files are retried; the test
   asserts 10 reused and the failed files re-profiled, with no timing (noted
   under 0.1.0 deviations).
4. [R-14] `docs/ARCHITECTURE.md` Section 4: cache path wording (D-015) and
   `catalog.duckdb.tmp`.
5. [R-15] F-SCAN-10: reuse also compares the profile schema version (and,
   since P-01, the DuckDB version).
6. [R-16] `docs/ARCHITECTURE.md` Section 7: the sidebar description; the UI
   package description no longer lists pages that do not exist yet.
7. [P-03], [P-04] `docs/LIMITS.md` 15 (profiles with table errors are reused
   until the file changes) and 5 (a click during a scan).
8. `docs/REVIEW_v0.1.md`, `docs/PLAN_v0.2.md`, decisions D-019 to D-024, limits
   L-09 revised and L-14 to L-17 added (written before the fixes).

### Demo flock

The demo flock now has 16 files walked, 13 candidates, 2 rejected, 10 ok and
3 failed (`corrupt` twice, `encrypted`); families unchanged. Two files were
added (`broken/highbit.duckdb`, `secure/encrypted.duckdb`; the latter leaves
no `.wal` sibling). Every test and document that stated the old numbers (14,
11, 1 failed) is updated.

### Deviations from the plan

1. The CLI option `--tag-pattern` is new: the acceptance item "an invalid tag
   pattern exits 1" needs a way to pass a pattern from the command line
   (D-025).
2. A config dict that does not validate used to fall back to the defaults in
   `profile_database`; it now gives an error profile (D-025).
3. When several columns of a table hold unrepresentable temporal values the
   error text names all of them (`... in columns a, b`).

## 0.1.0 (2026-11-04)

First usable version: scan a folder tree of DuckDB files and explore it as
one flock (plan: `docs/PLAN_v0.1.md`).

### What it does

1. `duckflock scan ROOT`: finds every DuckDB file (extension plus header
   magic, so SQLite files are rejected), profiles each one read-only in a
   spawn process pool (`max(1, cpu_count - 2)` workers, one DuckDB thread and
   a memory cap each), writes one JSON profile per file into the user cache
   and builds a DuckDB catalog from the profiles. Rescans reuse the profiles
   of unchanged files and retry failed ones.
2. `duckflock report ROOT` (text or Markdown), `duckflock make-demo DIR`,
   `duckflock paths ROOT`.
3. Schema fingerprints (strict: names and types, loose: names only, column
   order ignored), families, per-table variants, a diff of two databases or
   families, time windows, health flags, classified errors, path-token tags.
4. `duckflock-ui`: Streamlit Overview page (sidebar scan, metrics, flock
   table, families and matrix, table variants, diff, timeline, health,
   rejected files).

### Acceptance checklist (plan Section 8)

1. [x] CI green on Windows and Linux for every phase commit (1 to 6); the
   result for the 0.1.0 commit is in the GitHub Actions run of that commit.
2. [x] `make-demo` + `scan` of the demo flock: 14 files walked, 11
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
5. [x] Windows: `duckflock-ui`, demo flock scanned from the sidebar in the
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
7. The default cache directory is `user_cache_dir("duckflock", appauthor=False)`
   (D-015); `streamlit>=1.50` (the page uses `width="stretch"`).
8. The profiler never opens a file whose header lacks the DuckDB magic, also
   when called directly.
9. The plan's Phase 4 test text (a second run reuses every profile) cannot
   hold for the demo flock because the corrupt file is retried; the test
   asserts 10 of 11 reused and no timing (R-13).

### Open after 0.1.0

1. `incompatible_version` and `encrypted` are classified by message only; no
   fixture exists (plan Section 10 item 2). *Corrected in 0.1.1:* an
   `encrypted` fixture is producible and part of the demo flock (R-11);
   `incompatible_version` stays unverified.
2. `st.navigation` is not exercised by AppTest; the page function is.
3. Numbers marked (TBC) in `docs/LIMITS.md` and the tuning points in
   `docs/TUNING.md` are untested on a real flock.
4. Open options discovered while building: `docs/ROADMAP.md` items 13 and 14.

### Build log by phase

### Phase 0 (2026-11-04)

1. uv workspace with two packages: `duckflock-core` (duckdb, pydantic,
   platformdirs, psutil) and `duckflock-ui` (streamlit, plotly, pandas,
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
2. `demo.py` (`make_demo_flock`) built in this phase, ahead of the plan's
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
2. Tests in `test_families.py` (hand-built profiles and the demo flock:
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
   `make_demo_flock` was already in place since Phase 1.
2. Checked by hand on Windows with the console script: `duckflock make-demo`,
   `scan` (11 candidates, 2 rejected, 10 ok, 1 failed, 22 workers = 24 CPUs
   minus 2), a second `scan` (10 reused, the corrupt file retried), `report`
   (strict families 3, 3, 2, 1; loose 6, 2, 1; `events` 3 variants; time
   windows in order; `wal_present`, `empty_database`, the `corrupt` error and
   the 2 rejected files).
3. Scan timestamps in `ScanSummary` now carry milliseconds so the report can
   show a meaningful duration.

### Phase 6: Streamlit Overview (2026-11-04)

1. `duckflock_ui`: `launch.py` (`duckflock-ui`), `app.py` (`st.navigation`
   with the Overview page), `scan_runner.py` (scan as a subprocess, JSON-lines
   progress, stderr kept for the error expander), `catalog_access.py`
   (`st.cache_data` wrappers keyed by catalog path and mtime, a reader per
   call), `views/overview.py` (sidebar, metrics, flock table, families and
   matrix, table variants, diff, timeline, health, rejected files).
2. Tests: AppTest smoke tests on the wrapper script `_overview_app.py`
   (every section, session state, load and scan buttons, a complete
   Scan -> full rescan -> cached rescan flow with a real subprocess) and real
   subprocess tests of the scan runner. `st.navigation` itself is not
   exercised by AppTest; the page function is (plan Section 10 item 3).
3. Manual run on Windows (built-in browser, `duckflock-ui`): scan of the demo
   flock from the sidebar with live progress, full rescan (11 profiled),
   cached load in a fresh session, scan of a folder with a space and
   non-ASCII characters; all sections rendered, no exceptions.
4. `streamlit>=1.50` (the page uses `width="stretch"`); lock file refreshed.

