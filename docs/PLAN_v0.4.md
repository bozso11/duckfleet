# Implementation plan v0.4: cross-database

Status: ready for implementation, 2026-11-08. Written for Sonnet 5.5 or any
Claude Code session. Read `CLAUDE.md` first, then this file top to bottom,
then `docs/REVIEW_v0.3.md` (Phase 0 fixes its findings). Everything here is
decided unless marked (TBC) or listed in Section 10. Facts marked
(verified) were checked on 2026-11-08 against duckdb 1.5.6, streamlit
1.66.1, plotly 7.1.0, Python 3.11.9, Windows 11.

## Key immediate next steps

1. Phase 0 first: the v0.3 review fixes, shipped as 0.3.1 in one commit.
2. Then Phases 1 to 5 in order, one commit per phase on `main`, `uv run
   ruff check .` clean and `uv run pytest` green locally, CI green on both
   OSes before the next phase.
3. After Phase 5, tick the acceptance checklist (Section 8), bump to
   0.4.0, and stop.
4. Never end a turn on a browser check. A turn ends with a commit pushed
   or with a written status of what is left.

## 1. What v0.4 delivers

1. **Flock connection**: several databases of the scanned flock attached
   read-only to one sandboxed in-memory connection, with one union view per
   table name (`main.events` holds the `events` rows of every attached file
   with a `database` column), so one query or one plot spans files.
2. **SQL page and Plot page get a scope**: one database (as today) or
   several databases (a multiselect, defaulting to the members of the
   selected database's strict family). Queries run against the union views
   and the per-file aliases; plots colour by `database` by default.
3. **Content fingerprints of small tables** in the scan: tables up to L-07
   rows get a hash of their sorted content, so the Overview and the report
   can say "`config` has identical content in 6 of 8 files, 2 variants".
4. **CLI**: `duckflock flock-tables`, `duckflock flock-sql`, and
   `duckflock plot --databases ...`.
5. **0.3.1 fixes** from `docs/REVIEW_v0.3.md` (Phase 0).
6. Everything works on Windows and Linux; CI runs the suite on both.

## 2. Repository layout after v0.4 (additions only)

```
packages/duckflock-core/src/duckflock_core/
  flock.py             FlockSelection, aliases, open_flock, flock_tables, union views
  cli_flock.py         flock-tables, flock-sql
  plotdata.py          + fetch_plot_data_on(con, ...) and fetch_flock_plot_data(...)
  profiler.py          + content hash of small tables
  catalog.py           + tables.content_hash, CatalogReader.content_variants()
packages/duckflock-core/tests/
  test_flock.py, test_cli_flock.py, test_content_hash.py
packages/duckflock-ui/src/duckflock_ui/
  scope.py             the scope control shared by the SQL and Plot pages
  views/sql.py, views/plot.py, views/overview.py   (scope; identical-content section)
packages/duckflock-ui/tests/
  test_ui_scope.py
```

## 3. Data contracts

### 3.1 FlockSelection and aliases (`flock.py`)

```python
class FlockSelection(BaseModel):
    rel_paths: list[str]           # 1 to L-12 (50) members, distinct, in the given order

    def aliases(self) -> dict[str, str]: ...   # alias -> rel_path
```

Alias rule: the rel_path without its extension, lower-cased, every run of
characters outside `[a-z0-9]` replaced by `_`, leading digits prefixed with
`d_`, cut to 48 characters; collisions get `_2`, `_3`, ... in rel_path
order. Example: `exp_01/run_03/metrics.duckdb` -> `exp_01_run_03_metrics`.
Aliases are identifiers built by us from file names, never from user text,
and are always quoted with `quote_ident`.

### 3.2 The flock connection (`flock.py`, decision D-040)

```python
@dataclass
class AttachFailure:
    rel_path: str; alias: str; kind: str; message: str   # kind as ProfileError.type

@dataclass
class FlockHandle:
    con: duckdb.DuckDBPyConnection
    aliases: dict[str, str]            # attached only
    failures: list[AttachFailure]
    views: list[UnionView]

@dataclass
class UnionView:
    schema_name: str; table_name: str
    members: list[str]                 # rel_paths that have the table
    casts: list[str]                   # columns cast to VARCHAR because their types differed
    columns: list[tuple[str, str]]     # (name, type) of the view

@contextlib.contextmanager
def open_flock(selection: FlockSelection, paths: dict[str, str], config: QueryConfig,
               *, views: bool = True) -> Iterator[FlockHandle]: ...
```

Open sequence (every step verified):

1. `duckdb.connect()` (in-memory) with `threads`, `memory_limit`,
   `autoinstall_known_extensions=False`, `autoload_known_extensions=False`
   and `temp_directory` = a leaf of its own, `<cache>/tmp/<pid>/flock-<8 hex
   chars of uuid4>`, created before `connect` and removed in the `finally`
   after `con.close()`. Every flock connection is a separate DuckDB
   instance (two browser tabs are two instances in one process), and two
   instances must never share a temp folder (review W-01). The in-memory
   default `temp_directory` is `.tmp` relative to the working directory
   (verified), so this key is mandatory in any case.
2. For each member, in order: `ATTACH '<abs path>' AS <alias> (READ_ONLY)`.
   The path comes from the catalog row (`abs_path`), as a SQL string
   literal with `'` doubled. A failure (locked, encrypted, missing, corrupt)
   becomes an `AttachFailure` classified with `profiler.classify_error`,
   and the open continues with the others; zero successful attaches raise
   `QueryError(kind="other")`.
3. `SET enable_external_access = false`, then `SET lock_configuration =
   true`. After this, `ATTACH`, `COPY TO`, `read_csv`, `INSTALL`, `LOAD` and
   every `SET` are refused, while `DETACH` and queries work (verified).
4. Union views (when `views=True`): for every `(schema, table)` present in
   at least one attached database (from `duckdb_tables()` and
   `duckdb_views()` filtered to the aliases), `CREATE SCHEMA IF NOT EXISTS
   "<schema>"` in the in-memory database (the `main` schema exists already)
   and

   ```sql
   CREATE VIEW "<schema>"."<table>" AS
   SELECT '<rel_path>' AS database, * FROM "<alias>"."<schema>"."<table>"
   UNION ALL BY NAME
   SELECT '<rel_path>' AS database, * FROM "<alias2>"."<schema>"."<table>"
   ...
   ```

   Every branch carries `AS database` (a branch without the alias yields
   NULL labels, verified). `UNION ALL BY NAME` fills missing columns with
   NULL and widens FLOAT and DOUBLE (verified). If the `CREATE VIEW` raises
   a `BinderException` because a column has incompatible types across
   members (TBC which pairs), retry once with every column whose type
   differs across members (types from the catalog `columns` rows) cast to
   VARCHAR in each branch, and record those columns in `UnionView.casts`.
5. A query against an attached database that is held by a writer fails at
   attach time, not later; the page shows the failure next to the result.

Rules: the handle is opened per request and closed in `finally` (no cached
connections, as everywhere); attaching a file that is open through
`open_readonly` elsewhere in the process works (verified, no config
conflict); attach cost is negligible (8 files in 0.01 s, verified).

### 3.3 Content hash (`profiler.py`, `catalog.py`, decision D-041)

`TableProfile.content_hash: str | None`: for base tables with `row_count <=
config.content_hash_rows` (L-07, default 10,000),

```sql
SELECT md5(string_agg("df_row"::VARCHAR, chr(10) ORDER BY "df_row"::VARCHAR))
FROM <table> AS "df_row"
```

(the alias is unusual on purpose: with a plain `t`, a column named `t`
would win the reference).

(verified: identical `config` tables in two files give the same hash; 1,000
rows cost nothing measurable). An empty table gets `"empty"`; a table above
the cap or a view gets `None`; the hash depends on column order and types
as well as values (documented, TUNING `content-hash`). `PROFILE_SCHEMA_VERSION`
becomes 2, which re-profiles every file once on the next scan (say so in the
changelog). The catalog gains `tables.content_hash` and
`CatalogReader.content_variants()`: per `(schema, table)` with a hash, the
distinct hashes with member counts and members, ordered by count descending.

### 3.4 Plot data on a given connection (`plotdata.py`)

```python
def fetch_plot_data_on(con, db_name: str, spec: PlotSpec, config: QueryConfig,
                       *, started: float | None = None) -> PlotData: ...
def fetch_plot_data(db_path, spec, config) -> PlotData: ...        # unchanged signature, calls the above
def fetch_flock_plot_data(selection, paths, spec, config) -> PlotData: ...
```

`fetch_flock_plot_data` opens the flock, points the spec at the union view
(`db_name="memory"`, `schema_name`, `table_name`), and runs the same plan.
`database` is an ordinary VARCHAR column of the view: the page offers it as
the default colour column, and the series cap (L-19) folds the rest into
`(other)` as for any series. `PlotData.notes` carries the attach failures.

### 3.5 Scope (`duckflock_ui/scope.py`)

```python
@dataclass
class Scope:
    mode: Literal["one", "many"]
    rel_paths: list[str]          # one entry in mode "one"

def scope_control(dbs: pd.DataFrame, families: pd.DataFrame) -> Scope: ...
```

A radio `Scope: one database / several databases`, then in mode `many` a
multiselect of rel_paths (status `ok` only) whose default is the strict
family of the currently selected database (or the selected database alone
when it has no family), capped at L-12 with a caption when the family is
larger; a **Family** selectbox fills the multiselect with a family's
members. The selection is kept in session state (`scope_mode`,
`scope_rel_paths`) with `persist_state="session"` and shared by the SQL
and Plot pages.

## 4. Verified facts the code relies on

1. Section 3.2 items 1 to 5: in-memory default `temp_directory` is `.tmp`;
   `ATTACH ... (READ_ONLY)` then `SET enable_external_access = false` and
   `SET lock_configuration = true` at runtime; afterwards `ATTACH`, `COPY
   TO` and `SET` are refused, `DETACH` and queries work; writes into an
   attached database are refused as `read-only mode`; `CREATE TABLE` in the
   in-memory database is allowed (memory only, gone on close).
2. `duckdb_databases()` lists the attached aliases with `readonly = true`
   and the in-memory database as `memory`; `information_schema.tables` and
   `duckdb_tables()` list the attached tables under their alias.
3. `UNION ALL BY NAME` across files with differing columns fills NULLs; a
   branch literal without `AS database` gives NULL labels.
4. Attaching a file that another connection in the same process holds open
   with a different config works (no `ConnectionException`).
5. `md5(string_agg(t::VARCHAR, chr(10) ORDER BY t::VARCHAR))` is stable
   across files with identical content.
6. `database_name(con)` on the flock connection returns `memory`;
   `duckdb_columns()` lists the columns of the union views under
   `database_name = 'memory'`, so `validate_spec` and `_table_columns` work
   unchanged.

## 5. Phases

### Phase 0: 0.3.1, the review fixes

Implements every item of `docs/REVIEW_v0.3.md` Sections 2 to 4 and 6,
shipped as one commit `v0.3.1: review fixes`.

Core (`packages/duckflock-core`):

1. W-01: the temp directory becomes `<cache>/tmp/<pid>/<sha1(abs
   path)[:16]>`, one leaf per file per process. `QueryConfig.temp_dir`
   keeps the per-process root; `tempdir.file_temp_dir(root, abs_path)`
   builds the leaf; `open_readonly(db_path, config)` computes it from
   `db_path` and passes it to `connection_config(config, db_path)`; the
   profiler and the catalog writer call the same helper, so a scan and a
   query connection to one file in one process still share a config
   (`test_a_scan_and_a_query_connection_to_one_file_can_overlap_in_a_process`
   must keep passing). This works because two connections to the same file
   with an identical config share one DuckDB instance (the shared-instance
   case was verified safe), while different files are different instances
   and must not share a folder. The pid sweep and the atexit removal stay;
   sweeping also runs on a process's first connection (P-08). Test: two
   threads, two files, `memory_limit_mb=64`, a 12 M-row sort each, both
   finish with the right counts and the process survives; keep the earlier
   spill test.
2. W-02: above the ladder, `choose_bucket_seconds` derives `width =
   ceil(span / max(1, budget_rows // n_series - 1))` rounded up to whole
   days; such a derived width is not in `BUCKET_NAMES`, so `_sql_buckets`
   builds `INTERVAL '<N> days'` from the seconds and the label is `N days`
   (no `KeyError` path); the ladder test asserts the branch (200 yearly rows,
   budget 100 -> at most 100 rows, no guard error).
3. W-03: `PlotSpec` validator `len(y) <= 8` with a plain message; the CLI
   `--y` reports it as a usage error; L-19 gains the clause.
4. W-05: `_sql_bar` aliases the source subquery and qualifies the join
   columns; test with `df_c` and `df_rk` column names plus the hostile
   names list of the review.
5. W-06: `seed` bounded `0 <= seed <= 2**31 - 1`.
6. W-07: the `interrupt error` substring applies to `InvalidInputException`
   only; `select 'interrupt error'::INTEGER` joins the V-06 test list.
7. W-08: a non-finite span falls back like a zero span (raw, limited, a
   note); no bare `ValueError` can escape `fetch_plot_data`.
8. W-09: an autouse fixture in both `conftest.py` files points
   `default_cache_dir` at `tmp_path` (monkeypatch where it is looked up);
   a test asserts that the real cache directory is untouched after a plot
   and a query with a bare `QueryConfig()`.
9. W-10: `database_name` and the column lookup run through
   `execute_limited(..., remaining())`.
10. W-12: temporal x uses `TRY_CAST` where a cast happens; rows that do not
    convert are counted in the "left out" note.
11. P-11: remove `PlotPlan.series_kept`; document that the guard also
    covers histogram and bar.
12. Test gaps of review Section 6 item 3: TIMESTAMP_S/MS/NS x, empty and
    single-row tables, `plot --format csv|json` through a subprocess.

UI (`packages/duckflock-ui`):

13. W-03: `max_selections=8` on the y multiselect.
14. W-04: the remembered request includes `kind`; a request for another
    kind is not shown.
15. W-13: a histogram of several columns counts the columns as series for
    the legend rule (legend shown for two or more).
16. W-14: the histogram caption counts the drawn bins and the binned rows.
17. W-15: spec problems show as `st.warning(message)` without a kind.
18. W-16: documented in D-038 (the `(other)` category keeps the series
    colour when a colour column is set, so the series stay tellable).

Documentation: W-17 (ARCHITECTURE lines for **Plot this table** and
`common.file_state`; LIMITS 23 names every place the environment overrides
are read; `# TUNING` markers and rows for `LOW_CARDINALITY`, `_WEBGL_FROM`,
`_MARKERS_UP_TO`, the opacity threshold and `default_table`, or a TUNING.md
note that D-038 fixes them); LIMITS lines for W-01 (one temp folder per
file per process), W-11 (`(other)` and `(null)` as data values), P-09
(theme detection after a theme change) and P-10 (stderr encoding);
CHANGELOG 0.3.1 entry listing the fixes by review ID; versions 0.3.1 in the
three `pyproject.toml` files and both `__init__.py`.

### Phase 1: flock connection (`flock.py`)

Implements Sections 3.1 and 3.2. Also `flock_tables(con, aliases) ->
dict[tuple[str, str], list[str]]` (which members have which table) and
`run_flock_query(selection, paths, sql, config, *, max_rows=None) ->
tuple[QueryResult, FlockHandle summary]` using `execute_limited` under the
timeout, plus `export_flock_query(...)` mirroring `export_query`.

Tests (`test_flock.py`, on the demo flock): aliases (collisions, leading
digits, non-ASCII); attach of 8 files and the union views (`events` has
8,000 rows, `database` labels equal the rel_paths, `quality` is NULL for
files without it); a member held by a writer becomes an `AttachFailure`
of kind `locked` and the others still work; an encrypted member is
`encrypted`; after lockdown `ATTACH`, `COPY TO`, `SET` are refused and an
`INSERT` into an alias is `read_only`; no file is written beside any member
(list the folders); the connection is closed after an error; the timeout
applies; the type-conflict retry (build two files whose `value` column is
INTEGER and VARCHAR) produces a view with the cast recorded.

### Phase 2: content hash

`ScanConfig.content_hash_rows` (default 10,000, L-07), the profiler query of
Section 3.3 inside the per-table try block, `PROFILE_SCHEMA_VERSION = 2`,
the catalog column, `CatalogReader.content_variants()`, a `report`
section "Identical small tables" (per table: number of content variants,
largest group), and the Overview section **Identical content** (table:
schema.table, variants, members per variant, expandable list).

Tests (`test_content_hash.py`): identical `config` across the demo files
share one hash and `events` do not; the empty table hashes `empty`; a
table above the cap has `None`; the catalog and the reader return the
variants; the report prints the section; a rescan after the version bump
re-profiles every file.

### Phase 3: plots and CLI

1. `plotdata.py` refactor of Section 3.4 (no behaviour change for one
   database; `test_plotdata.py` must pass unchanged).
2. `cli_flock.py`: `duckflock flock-tables ROOT [--databases a,b | --family
   F1 | --all]` prints each table with its member count and the members
   that lack it; `duckflock flock-sql ROOT SQL [--databases | --family |
   --all] [--max-rows] [--timeout] [--format table|csv|json]` runs on the
   flock connection with the union views; both print attach failures on
   stderr. `duckflock plot` gains `--databases` and `--family` (then the
   table is the union view and `--color database` is the default). Exit
   codes as before; a selection above L-12 is a usage error.

Tests (`test_cli_flock.py`): the commands on the demo flock, `--family`
resolution through the catalog, the L-12 limit, exit codes.

### Phase 4: UI scope

1. `scope.py` (Section 3.5) used by the SQL and Plot pages: in mode `many`
   the SQL page's Schema expander lists the union views with member counts
   and the aliases; the placeholder query becomes `SELECT database,
   count(*) FROM events GROUP BY 1`; results and downloads work as before;
   attach failures show as a warning above the result.
2. Plot page in mode `many`: the Table selectbox lists the union views; the
   colour selectbox defaults to `database`; the caption names the number
   of files and any folded files; **Plot this table** from the Database
   page keeps mode `one`.
3. Overview: the **Identical content** section after Table variants.

Tests (`test_ui_scope.py`): scope default from the family; a flock SQL
query across 6 files returns 6 groups; a flock plot of `events` coloured by
`database` has 6 series; the cap caption at more than L-12 members; the
Overview section renders the demo's `config` variants.

### Phase 5: tests, docs, acceptance

`docs/FEATURES.md` (F-X-01, F-X-02, F-PLOT-04, F-FLOCK-08 to `done v0.4`),
`docs/LIMITS.md` (L-07, L-12 confirmed; L-22 added), `docs/TUNING.md`
(`content-hash`, `alias-rule` rows), `docs/ARCHITECTURE.md` (flock
connection, scope, the content hash in the profile), `docs/DECISIONS.md`
(TBC outcomes), `CHANGELOG.md` 0.4.0 with the acceptance results,
`README.md`; versions 0.4.0.

## 6. Cross-cutting rules (in addition to v0.1 to v0.3)

1. The flock connection attaches only paths from the catalog, before the
   lockdown; user SQL never runs before `lock_configuration` is set.
2. Aliases and view names are built from file and table names and always
   quoted; rel_paths enter SQL only as escaped string literals in the view
   definitions.
3. No cached flock connections: open per request, close in `finally`.
4. The temp directory of every flock connection is its own leaf under the
   process folder, removed when the connection closes; two DuckDB instances
   never share a temp folder.

## 7. Windows and Linux

Unchanged. Alias generation lower-cases with `str.lower()` (not locale
dependent).

## 8. Acceptance checklist (v0.4 done when all are ticked)

1. CI green on Windows and Linux for the 0.3.1 commit and every v0.4 phase
   commit.
2. Phase 0: every item of `docs/REVIEW_v0.3.md` fixed with its test.
3. `uv run duckflock flock-tables ./demo-flock --family F1` lists `events`
   and `config` with 3 members each; `uv run duckflock flock-sql
   ./demo-flock "select database, count(*) n from events group by 1 order by
   1" --all` prints one row per readable file with `events` (the encrypted
   and corrupt files appear as attach failures on stderr); an `INSERT`
   exits 3 as `read_only`; `COPY TO` exits 3 as `blocked`.
4. `uv run duckflock plot ./demo-flock --family F1 main.events --kind line
   --x ts --y value --max-points 300 --format csv` prints bucketed rows
   with three `database` series.
5. A rescan after 0.4.0 re-profiles every file once and `report` shows the
   "Identical small tables" section with `config` identical within each
   experiment.
6. UI on Windows (recorded in the changelog) and through AppTest in CI:
   SQL page in scope `many` with the F1 family runs the placeholder query
   and shows 3 rows; Plot page in scope `many` draws `events` with 3
   coloured series; the Overview shows the Identical content section.
7. Docs updated and versions bumped to 0.4.0.

## 9. Not in v0.4 (do not build)

Cross-flock joins beyond what the user writes in SQL, persistent flock
views or materialised unions, scanning two roots at once, writes of any
kind, Datashader, identity queries (open option F-FLOCK-09).

## 10. Open items for the implementer (TBC)

1. Which type pairs make `UNION ALL BY NAME` fail (for example INTEGER and
   VARCHAR): verify, and keep the VARCHAR cast retry of Section 3.2 item 4
   only if needed; record the outcome in DECISIONS.
2. Streamlit multiselect with 50 entries: if it becomes unwieldy, add a
   text filter above it; keep L-12.
3. The `database` column name on the union views: verify it never clashes
   with a column of the same name in a member table; if it does, name the
   label column `duckflock_database` and say so in the Schema expander.
