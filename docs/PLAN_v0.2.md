# Implementation plan v0.2: single-database deep dive and SQL

Status: ready for implementation, 2026-11-04. Written for Sonnet 5.5 or any
Claude Code session. Read `CLAUDE.md` first, then this file top to bottom,
then `docs/REVIEW_v0.1.md` (Phase 0 fixes its findings). Everything here is
decided unless marked (TBC) or listed in Section 10. Facts marked
(verified) were checked on 2026-11-04 against duckdb 1.5.6, streamlit
1.66.1, Python 3.11.9, Windows 11.

## Key immediate next steps

1. Phase 0 first: the v0.1 review fixes, shipped as 0.1.1 in one commit.
2. Then Phases 1 to 6 in order, one commit per phase on `main`, `uv run
   ruff check .` clean and `uv run pytest` green locally, CI green on both
   OSes before the next phase.
3. After Phase 6, tick the acceptance checklist (Section 8), bump to
   0.2.0, and stop. Do not start v0.3 features.

## 1. What v0.2 delivers

1. **Database page** (Streamlit): pick one database of the scanned fleet;
   see its tables with row counts and time windows; per table the columns
   with the cheap stats from the scan, a full per-column summary (DuckDB
   `SUMMARIZE`, computed on demand and cached), and a paged, sortable data
   preview. A button opens the table in the SQL page.
2. **SQL page**: a read-only, sandboxed SQL editor on one database with a
   result grid, a row cap, a timeout, CSV and Parquet download, and a
   per-session query history.
3. **CLI**: `duckfleet sql`, `duckfleet summarize`, `duckfleet preview`.
4. **0.1.1 fixes** from `docs/REVIEW_v0.1.md` (Phase 0).
5. Everything works on Windows and Linux; CI runs the suite on both.

## 2. Repository layout after v0.2 (additions only)

```
packages/duckfleet-core/src/duckfleet_core/
  query.py            QueryConfig, open_readonly, run_query, export_query, preview_table, QueryError
  summary.py          TableSummary models, summarize_table
  store.py            + SummaryStore (JSON sidecars next to the profiles)
  cli.py              + sql, summarize, preview
packages/duckfleet-core/tests/
  test_query.py, test_summary.py, test_cli_v02.py   (unique basenames)
packages/duckfleet-ui/src/duckfleet_ui/
  nav.py              the st.Page objects, shared by app.py and the views
  query_access.py     cached wrappers for summaries and previews (not for ad-hoc SQL)
  views/database.py   render()
  views/sql.py        render()
packages/duckfleet-ui/tests/
  _database_app.py, _sql_app.py, test_ui_database.py, test_ui_sql.py
```

## 3. Data contracts

### 3.1 QueryConfig (`query.py`)

Pydantic model, defaults, meaning:

| Field | Default | Meaning |
|---|---|---|
| `threads` | `None` | `None` resolves to `min(4, cpu_count)` |
| `memory_limit_mb` | `None` | `None` resolves to `min(4096, total_ram_mb // 4)`, floor 512 (L-04 companion) |
| `max_rows` | `10_000` | rows fetched for display; L-09 |
| `max_download_rows` | `1_000_000` | rows exported to CSV or Parquet; L-09 |
| `timeout_s` | `60.0` | query time limit, enforced with `interrupt()`; L-14 |
| `preview_rows` | `1_000` | preview page size; L-08 |
| `preview_max_columns` | `50` | columns shown by default in the preview; L-15 |
| `stats_row_threshold` | `20_000_000` | `SUMMARIZE` runs on a sample above this; L-05 |
| `stats_sample_rows` | `1_000_000` | sample size; L-06 |

Provide `resolved_threads()` and `resolved_memory_limit_mb()`.

### 3.2 The query connection (`query.py`, decision D-021)

```python
def connection_config(config: QueryConfig) -> dict:
    return {
        "threads": config.resolved_threads(),
        "memory_limit": f"{config.resolved_memory_limit_mb()}MB",
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "enable_external_access": False,
        "lock_configuration": True,
        "TimeZone": "UTC",            # (TBC) see Section 10 item 1
    }

@contextlib.contextmanager
def open_readonly(db_path: str | Path, config: QueryConfig) -> Iterator[duckdb.DuckDBPyConnection]: ...
```

Verified behaviour of such a connection (all raise, the connection stays
usable):

1. `CREATE TABLE`, `INSERT`, `UPDATE`, `DELETE` on the database:
   `InvalidInputException: Cannot execute statement of type "CREATE" on
   database "x" which is attached in read-only mode!`
2. `COPY ... TO 'file'`, `ATTACH 'file'`, `read_csv('file')`, `INSTALL`:
   `PermissionException: ... file system operations are disabled by
   configuration`; `LOAD`: `Loading external extensions is disabled through
   configuration`.
3. `SET threads = 8` and any other `SET`: `InvalidInputException: Cannot
   change configuration option "threads" - the configuration has been
   locked`.
4. `CREATE TEMP TABLE` is allowed (in memory, per connection, gone on
   close); `SHOW TABLES`, `PRAGMA database_size`, `SUMMARIZE`, `DESCRIBE`,
   `EXPLAIN` work.
5. **Same file, same config, one process** (verified): a second
   `duckdb.connect` to the same file in the same process must use an
   identical config dict, or it raises `ConnectionException: Can't open a
   connection to same database file with a different configuration than
   existing connections`. Therefore every open of a scanned file inside the
   UI or CLI process goes through `open_readonly` with `connection_config`,
   and the config is resolved once per process (`QueryConfig` fields
   `threads` and `memory_limit_mb` are never exposed as page inputs).
6. `con.interrupt()` called from another thread raises
   `duckdb.InterruptException: INTERRUPT Error: Interrupted!` in the
   thread running the query; the connection is usable afterwards
   (verified).

### 3.3 QueryResult and QueryError (`query.py`)

```python
@dataclass
class QueryResult:
    columns: list[str]
    types: list[str]            # str(description[i][1]), e.g. "DECIMAL(18,3)"
    rows: list[tuple]           # at most max_rows (or the preview page size)
    truncated: bool             # more rows existed
    elapsed_s: float
    statements: int             # from con.extract_statements(sql)

class QueryError(Exception):
    kind: Literal["sql", "read_only", "blocked", "timeout", "other"]
    message: str                # first 1,000 chars of DuckDB's text
```

Classification (case-insensitive substrings, in this order): `timeout`
when the exception is `duckdb.InterruptException`; `read_only` for
`read-only mode`; `blocked` for `disabled by configuration`,
`configuration has been locked`, `extensions is disabled`; `sql` for
`duckdb.ParserException`, `duckdb.BinderException`,
`duckdb.CatalogException`, `duckdb.ConversionException`,
`duckdb.InvalidInputException`; everything else `other`.

### 3.4 TableSummary (`summary.py`)

```python
SUMMARY_SCHEMA_VERSION = 1

class ColumnSummary(BaseModel):
    column_name: str
    column_type: str
    min: str | None
    max: str | None
    approx_unique: int | None
    avg: str | None
    std: str | None
    q25: str | None
    q50: str | None
    q75: str | None
    count: int | None
    null_percentage: float | None

class TableSummary(BaseModel):
    summary_schema_version: int = SUMMARY_SCHEMA_VERSION
    rel_path: str
    schema_name: str
    table_name: str
    size_bytes: int             # of the database file when computed
    mtime_ns: int
    duckdb_version: str
    computed_at: str            # ISO 8601 UTC
    elapsed_s: float
    row_count: int | None
    sampled: bool
    sample_rows: int | None
    columns: list[ColumnSummary]
    error: str | None = None
```

`SUMMARIZE` output columns (verified): `column_name VARCHAR, column_type
VARCHAR, min VARCHAR, max VARCHAR, approx_unique BIGINT, avg VARCHAR, std
VARCHAR, q25 VARCHAR, q50 VARCHAR, q75 VARCHAR, count BIGINT,
null_percentage DECIMAL(9,2)`. `SUMMARIZE SELECT * FROM "db"."s"."t" USING
SAMPLE 1000000 ROWS`, `SUMMARIZE` of a view and of an empty table all work
(verified).

### 3.5 Summary store (`store.py`, decision D-020)

```
<cache_dir>/roots/<root_key>/summaries/<file_key>/<table_key>.json
```

`table_key = sha1(f"{schema}.{table}")[:16]`. A cached summary is valid
when `size_bytes`, `mtime_ns`, `summary_schema_version` and
`duckdb_version` match the current file and library; otherwise it is
recomputed on request. `ProfileStore.prune` also deletes the summaries
folder of every pruned profile. Summaries are not part of the catalog in
v0.2 (the catalog is single-writer and rebuilt by scans only; folding
summaries into it is an open option).

### 3.6 Preview (`query.py`)

```python
def preview_table(db_path, schema_name, table_name, config, *,
                  columns: list[str] | None = None,   # None = all
                  order_by: str | None = None, descending: bool = False,
                  offset: int = 0, limit: int | None = None) -> QueryResult: ...
```

Builds `SELECT <quoted columns or *> FROM "db"."schema"."table" [ORDER BY
"col" ASC|DESC] LIMIT n OFFSET k` with `quote_ident` (import it from
`profiler.py`, or move it to a small `sqlutil.py` and import it from both).
`columns` and `order_by` must be names of the table (validate against
`duckdb_columns()`; raise `QueryError(kind="sql")` otherwise).

## 4. Verified facts the code relies on

1. Section 3.2 items 1 to 6.
2. `cursor.description` is a list of `(name, DuckDBPyType, ...)`; `str()`
   of the type gives `TIMESTAMP`, `DECIMAL(18,3)`, `INTEGER[]`, `TIMESTAMP
   WITH TIME ZONE` (verified). `cursor.description` is `None` for a
   statement without a result set.
3. `cursor.fetchmany(n)` then `cursor.fetchmany(1)` detects "more rows"
   (verified).
4. `con.extract_statements(sql)` returns one `Statement` per statement,
   with `.type` (verified). `con.execute("a; b")` runs both and returns the
   last result (verified).
5. `con.sql(sql)` returns a `DuckDBPyRelation` (also for `SHOW TABLES`);
   `relation.limit(n).to_arrow_table()` returns a `pyarrow.Table`
   (`fetch_arrow_table()` is deprecated in 1.5.6; `.arrow()` returns a
   `RecordBatchReader`, not a table). `pyarrow.parquet.write_table` and
   `pyarrow.csv.write_csv` into a `BytesIO` produce the download bytes
   (verified).
6. Fetching a `TIMESTAMP WITH TIME ZONE` value with `fetchone`/`fetchmany`
   needs `pytz` (`InvalidInputException: Required module 'pytz' failed to
   import`, verified); Arrow and pandas fetches do not. Decision D-022: add
   `pytz>=2024.1` to `duckfleet-core` dependencies so rows fetch in the CLI
   and the grid. The profiler's SQL-side UTC conversion stays (LIMITS 13
   becomes historical).
7. Streamlit 1.66: `st.switch_page(page: str | Path | Page)` and
   `st.page_link(page)` accept the `st.Page` objects given to
   `st.navigation`; `st.dataframe(..., on_select=...)` exists;
   `st.download_button(label, data, file_name, mime, ...)` (verified
   signatures).
8. `isfinite(x)` works on TIMESTAMP and DATE and is `false` for the
   `infinity` sentinels (verified).
9. An encrypted database fixture: `ATTACH 'enc.duckdb' AS e (ENCRYPTION_KEY
   'secret'); CREATE TABLE e.t AS SELECT 1` from an in-memory connection
   writes a file whose header keeps the `DUCK` magic (storage version 68);
   `duckdb.connect(path, read_only=True)` raises `CatalogException: Cannot
   open encrypted database "..." without a key`, classified `encrypted`
   (verified).

## 5. Phases

### Phase 0: 0.1.1, the review fixes

Implements every item of `docs/REVIEW_v0.1.md` Sections 2 to 4. In
`packages/duckfleet-core`:

1. R-01: catalog column `storage_version UBIGINT`; `read_header` returns
   `None` for a value above `2**63 - 1`; demo fleet gains
   `broken/highbit.duckdb` (`bytes(8) + b"DUCK" + b"\xff" * 8 +
   bytes(4096)`, profiled as `corrupt`); update `docs/ARCHITECTURE.md`
   Section 5.
2. R-02: temporal `min`/`max` become `min(expr) FILTER (WHERE
   isfinite(expr))` and `max(...)` in `_run_aggregates` for both the exact
   and the sampled query; `_as_datetime` returns `None` for
   `datetime.max`, `datetime.min`, `date.max`, `date.min`; test with
   `'infinity'`, `'-infinity'`, `DATE 'infinity'` and
   `'infinity'::TIMESTAMPTZ` (for `TIMESTAMP WITH TIME ZONE` the filter
   applies to the `AT TIME ZONE 'UTC'` expression; `isfinite` was verified
   on TIMESTAMP and DATE only, so this case is part of the test); TUNING
   row `time-window-scope` mentions it.
3. R-03: `ScanConfig` validator for `tag_patterns` (compile; require named
   groups `key` and `value`; `ValueError` with the pattern in the message);
   the CLI reports it as a usage error (exit 1); `_file_facts` runs inside
   the `try` of `profile_database` with `tags={}` as fallback, and
   `error_profile` never raises. Tests: `profile_database` with `["("]`
   returns an error profile; `run_scan` with workers 1 and 2 does not raise
   when a pattern is valid but matches nothing.
4. R-04: `resolved_workers()` returns `min(max(1, ...), MAX_POOL_WORKERS)`;
   UI test with `os.cpu_count` patched to 72 renders the page.
5. R-05: when a temporal min or max is not a `date`/`datetime`, set
   `TableProfile.error = "temporal value outside the supported range in
   column <name>"`, clear that table's window, add `table_error:` health;
   LIMITS 14 corrected.
6. R-06: `discover` prunes the resolved cache directory (compare
   `os.path.normcase(os.path.realpath(...))`); test with `--cache-dir`
   inside the root.
7. R-07: `build_catalog` connects with `threads=2, memory_limit="1GB",
   autoinstall/autoload False`.
8. R-10: delete the vacuous assertion.
9. R-11: demo fleet gains `secure/encrypted.duckdb` (Section 4 item 9);
   test asserts `error.type == "encrypted"`; correct `CHANGELOG.md` open
   item 1, D-018 and F-SCAN-09. Verify that no `.wal` sibling remains once
   the attaching connection is closed (checkpoint or delete it in
   `make_demo_fleet` if one does), otherwise the walked-files count in
   item 12 is off by one.
10. P-01: `_can_reuse` also requires `existing.duckdb_version ==
    duckdb.__version__`; test.
11. P-02: `_profiles_in_pool` shuts the pool down with
    `cancel_futures=True` in a `try/finally`. Test without patching (a
    monkeypatched `profile_database` never reaches spawned workers):
    generate 20 small files, run with 2 workers through a consumer loop
    that raises after the first saved profile, then assert that fewer
    profile JSON files than candidates exist in the store.
12. Demo fleet after Phase 0: 16 files walked, 13 candidates, 2 rejected,
    10 ok, 3 failed (`corrupt` x2, `encrypted`); family numbers unchanged.
    Update every test and document that states the old numbers.

In `packages/duckfleet-ui`:

13. R-04 sidebar value capped (follows from item 4).
14. R-08: in `_timeline`, a bar with `time_max == time_min` is drawn with
    `time_max + max(1 hour, 0.5 % of the fleet span)` and the hover text
    says "single instant"; the caption lists such databases.
15. R-09: `_scan` says "The scan ended without a finished event" when the
    exit code is 0 and no summary arrived, and appends `stray_stdout` to the
    log expander.

Documentation (R-12 to R-16, P-03, P-04): LIMITS L-02 gains the 61 cap;
LIMITS 5 documents the click-during-scan behaviour; LIMITS 15 documents
that profiles with table errors are reused until the file changes;
ARCHITECTURE Sections 3, 4 and 7 corrected; FEATURES F-SCAN-10 wording;
the UI package description becomes "Streamlit UI for duckfleet";
CHANGELOG gets a 0.1.1 entry listing the fixes by review ID. Versions:
0.1.1 in the three `pyproject.toml` files and both `__init__.py`.

### Phase 1: query layer (`query.py`)

```python
def run_query(db_path, sql, config: QueryConfig, *, max_rows: int | None = None) -> QueryResult: ...
def export_query(db_path, sql, fmt: Literal["csv", "parquet"], config, *,
                 max_rows: int | None = None) -> tuple[bytes, int, bool]: ...   # bytes, rows, truncated
def preview_table(...)   # Section 3.6
def table_columns(db_path, schema_name, table_name, config) -> list[tuple[str, str]]: ...  # (name, type)
```

Rules:

1. Every function opens with `open_readonly`, runs inside
   `_with_timeout(con, fn, config.timeout_s)`, and closes the connection
   in `finally`. `_with_timeout` runs `fn` in a one-thread
   `ThreadPoolExecutor`; on `concurrent.futures.TimeoutError` it calls
   `con.interrupt()`, waits for the future, and raises
   `QueryError(kind="timeout")`.
2. `run_query`: `statements = len(con.extract_statements(sql))` (a parser
   error here is `QueryError(kind="sql")`); `cursor = con.execute(sql)`; if
   `cursor.description is None` return an empty result; else
   `fetchmany(max_rows)` and the extra `fetchmany(1)` for `truncated`.
   The `execute` and both `fetchmany` calls run inside the timed function
   (fetching is the slow half of a streaming query), never after it.
3. `export_query`: `relation = con.sql(sql)` (a non-query statement gives
   `None`: raise `QueryError(kind="sql", message="the statement returns no
   rows")`); `table = relation.limit(max_download_rows + 1).to_arrow_table()`;
   `truncated = table.num_rows > max_download_rows`; slice to the cap;
   Parquet via `pyarrow.parquet.write_table(table, buffer)`, CSV via
   `pyarrow.csv.write_csv(table, buffer)`. pyarrow is imported inside the
   function (it is a UI dependency; the CLI `--format csv` path needs it
   too, so `duckfleet-core` declares `pyarrow` as an optional extra
   `export` and the CLI prints a clear message when it is missing).
4. Row values are returned as DuckDB gives them (datetime, Decimal, lists);
   the UI converts with pandas; the CLI table printer uses `str()`.
5. The SQL text is never modified or wrapped, except for the `LIMIT`
   applied through the relation API in `export_query`.

Tests (`test_query.py`, on a generated file with TIMESTAMP, TIMESTAMPTZ,
DOUBLE, VARCHAR, DECIMAL, INTEGER[] and 10,000 rows): result columns and
types; `max_rows=5` gives `truncated=True`; a write statement is
`read_only`; `COPY ... TO`, `ATTACH`, `read_csv`, `LOAD` are `blocked`; `SET`
is `blocked`; a syntax error is `sql`; `timeout_s=0.5` with `select
sum(a.i * b.i) from range(200000) a(i), range(200000) b(i)` is `timeout`
within a few seconds and the next query on the same path works; multi
statement returns the last result with `statements == 2`; export Parquet
and CSV round-trip through pyarrow; preview paging, ordering and a column
subset; unknown column names rejected; a schema and table with quotes and
unicode in the names.

### Phase 2: summaries (`summary.py`, `store.py`)

```python
def summarize_table(db_path, schema_name, table_name, config: QueryConfig, *,
                    row_count: int | None, rel_path: str, size_bytes: int, mtime_ns: int) -> TableSummary: ...

class SummaryStore:
    def __init__(self, cache_dir: Path, root: Path): ...
    def load(self, rel_path, schema_name, table_name, *, size_bytes, mtime_ns) -> TableSummary | None: ...
    def save(self, summary: TableSummary) -> None: ...
    def get_or_compute(self, db_path, rel_path, schema_name, table_name, config, *, row_count,
                       refresh: bool = False) -> TableSummary: ...
```

Rules: `sampled = row_count is not None and row_count >
config.stats_row_threshold`; SQL `SUMMARIZE SELECT * FROM <qualified>` or
`... USING SAMPLE <n> ROWS`; the timeout of Section 5 Phase 1 applies; a
failure is stored as a `TableSummary` with `error` set (and is not
reused: `load` ignores summaries with an error). `ProfileStore.prune`
removes `summaries/<file_key>` of pruned profiles.

Tests: summary columns and values on a known table; cache hit (second call
does not open the database: patch `open_readonly` to fail); invalidation
on mtime change; sampled flag with a low threshold; empty table; a view;
an error summary is recomputed next time.

### Phase 3: CLI

```
duckfleet sql ROOT DATABASE SQL [--max-rows N] [--timeout S] [--format table|csv|json] [--cache-dir DIR]
duckfleet summarize ROOT DATABASE SCHEMA.TABLE [--refresh] [--format table|json] [--cache-dir DIR]
duckfleet preview ROOT DATABASE SCHEMA.TABLE [--rows N] [--offset N] [--order-by COL] [--desc]
                  [--columns a,b,c] [--format table|csv|json] [--cache-dir DIR]
```

1. `DATABASE` is the `rel_path` as listed by `report`; the absolute path
   comes from the catalog (`databases.abs_path`). If no catalog exists:
   fatal (exit 2) with the hint to run `scan`.
2. Exit codes: 0 ok, 1 usage, 2 fatal, 3 query error (`QueryError`; the
   kind and message go to stderr).
3. `--format table` prints an aligned text table (first `max_rows` rows,
   then `... (truncated)`), `csv` writes CSV to stdout, `json` one object
   per row.
4. `summarize --format table` prints the 12 columns; the second call says
   `(cached <computed_at>)` on stderr.

Tests (`test_cli_v02.py`): the three commands on the demo fleet, the exit
codes 2 and 3, `--format json`, the cached message.

### Phase 4: Database page (`views/database.py`, `nav.py`, `query_access.py`)

1. `nav.py`: `PAGES = {"overview": st.Page(overview.render, title="Overview",
   default=True), "database": st.Page(database.render, title="Database"),
   "sql": st.Page(sql.render, title="SQL")}`; `app.py` runs
   `st.navigation(list(PAGES.values()))`. Views import `nav` inside
   functions (never at module level) to avoid the import cycle.
2. `query_access.py`: `@st.cache_data` wrappers keyed by `(abs_path,
   size_bytes, mtime_ns, ...)` for `table_summary` and `preview_page`;
   `run_query` is never cached. A `QueryConfig` is built once per process
   from the environment (`DUCKFLEET_QUERY_THREADS`, `DUCKFLEET_QUERY_MEMORY_MB`
   optional overrides) and shared by both pages (Section 3.2 item 5).
3. `database.render()`:
   - resolve the root and catalog exactly as the Overview does (same
     helpers, move them to `duckfleet_ui/common.py`); if no catalog: info
     and a `st.page_link` to the Overview;
   - database selectbox over `databases` (status `ok` first, then errors),
     key `selected_rel_path` (shared with the Overview);
   - header: `rel_path`, size, family labels, tags, time window, health;
     an error database shows the error and stops; a file whose `abs_path`
     no longer exists shows "file missing since the last scan, rescan"
     instead of a raw `QueryError`; a file whose size or mtime differ from
     the catalog row gets the caption "changed since the last scan" (row
     counts shown come from the scan);
   - tables table (`CatalogReader.tables(rel_path)`): schema, name, kind,
     rows, columns, time min, time max, error; a selectbox picks one,
     key `selected_table` (value `"schema.table"`), kept valid with
     `_keep_valid`;
   - tabs **Columns**, **Summary**, **Preview**:
     - Columns: `CatalogReader.columns(rel_path, schema, table)` as a
       DataFrame with null share (`1 - non_null_count / row_count`), min,
       max, approx distinct, sampled;
     - Summary: if a valid cached summary exists show it at once, else a
       **Compute summary** button; **Recompute** afterwards; caption with
       `computed_at`, `elapsed_s`, sampled and sample size; `error` shown
       with `st.error`;
     - Preview: multiselect of columns (default the first
       `preview_max_columns`), sort column selectbox (none, or a column) and
       a descending checkbox, page number input; shows `rows x of N` from
       the catalog row count; `st.dataframe` of the page; the query uses
       `preview_table`;
   - button **Open in SQL**: sets `st.session_state["sql_text"] = 'SELECT *
     FROM "schema"."table" LIMIT 100'` and calls
     `st.switch_page(nav.PAGES["sql"])`.
4. Values for the grid: build the DataFrame with
   `pd.DataFrame(rows, columns=columns)`; convert temporal columns with
   `pd.to_datetime` (pandas 2.2 and 3.x), leave lists and structs as
   objects.

### Phase 5: SQL page (`views/sql.py`)

1. Database selectbox (shared `selected_rel_path`); an expander **Schema**
   listing tables with their columns and types from the catalog.
2. `st.text_area("SQL", key="sql_text", height=220)`; buttons **Run**
   (primary) and **Clear**; an expander **Options** with max rows shown
   (`number_input`, 100 to 100,000, default `max_rows`) and timeout seconds
   (1 to 3,600, default `timeout_s`).
3. Run: `run_query` with the page options (a copy of the shared
   `QueryConfig` with `max_rows` and `timeout_s` replaced; `threads` and
   `memory_limit_mb` untouched); on success a caption (`elapsed`, rows
   shown, `truncated` warning, statements count when above 1) and
   `st.dataframe`; on `QueryError` an `st.error` with a kind-specific
   hint: `read_only` "duckfleet opens databases read-only", `blocked`
   "file access, extensions and settings are disabled on this page",
   `timeout` "raise the timeout in Options or narrow the query", `sql` the
   DuckDB message.
4. Downloads: buttons **Prepare CSV** and **Prepare Parquet** run
   `export_query` with the current SQL and store the bytes in
   `st.session_state["download"]` (`{"name", "mime", "data", "rows",
   "truncated"}`); then an `st.download_button` appears with the file name
   `<database stem>_<yyyymmdd-hhmmss>.csv|parquet`; a truncated export
   shows a warning with the cap.
5. History: `st.session_state["sql_history"]` keeps the last 20 distinct
   queries (newest first); a selectbox **History** loads one into the
   editor.
6. The last result is kept in `st.session_state["sql_result"]` so the grid
   survives reruns caused by other widgets; it is cleared when the
   database changes.

### Phase 6: tests, docs, acceptance

1. UI tests with `AppTest` on `_database_app.py` and `_sql_app.py`
   (wrapper scripts calling `render()` with `DUCKFLEET_ROOT` and
   `DUCKFLEET_CACHE_DIR` pointing at a scanned demo fleet): Database page
   renders the tables table, Columns tab, Summary compute, Preview page 2
   sorted descending; SQL page runs a query, shows a `read_only` error for
   an `INSERT`, a `blocked` error for `COPY`, and a truncated result with
   max rows 5; `Open in SQL` sets `sql_text` (page switching itself is
   (TBC) under AppTest; assert the state, not the navigation).
2. Docs: `docs/FEATURES.md` (F-DB-01..05, F-UI-02, F-UI-04 to `done
   v0.2`), `docs/LIMITS.md` (L-08, L-09, L-14, L-15, L-16 confirmed),
   `docs/TUNING.md` (any new marker), `docs/ARCHITECTURE.md` (query layer,
   summary store, UI structure with three pages), `docs/DECISIONS.md` (any
   TBC outcome), `CHANGELOG.md` 0.2.0 with the acceptance results,
   `README.md` quick start (the three new commands and the two pages).
3. Versions 0.2.0 everywhere.

## 6. Cross-cutting rules (in addition to v0.1 Section 6)

1. Every connection to a scanned file inside the UI or CLI process is
   `open_readonly` with `connection_config`: read-only, extension autoload
   off, external access off, configuration locked, one config per process.
2. User SQL is executed as given, never interpolated into other SQL; the
   only generated SQL (preview, summary) uses `quote_ident` and validated
   column names.
3. Every query runs under the timeout; no page can hang on a runaway
   query (the timeout interrupts it).
4. Nothing is written inside the scanned folder; downloads are bytes
   handed to the browser, never files on disk.
5. New dependency in core: `pytz` (D-022); optional extra `export` for
   `pyarrow`. Nothing else. After any dependency edit run `uv lock` and
   commit `uv.lock` in the same commit: CI runs plain `uv sync`, so a stale
   lock does not fail CI, it silently diverges.

## 7. Windows and Linux

Unchanged from v0.1 Section 7. The timeout uses a thread, not signals.
The UI tests build the demo fleet in `tmp_path` and scan with `workers=1`.

## 8. Acceptance checklist (v0.2 done when all are ticked)

1. CI green on Windows and Linux for the 0.1.1 commit and for every v0.2
   phase commit.
2. Phase 0: demo scan reports 16 walked, 13 candidates, 2 rejected, 10 ok,
   3 failed (`corrupt`, `corrupt`, `encrypted`); families unchanged; the
   `infinity` test passes; `duckfleet scan` with an invalid tag pattern
   exits 1 with a message naming the pattern; the AppTest with 72 patched
   CPUs renders.
3. `uv run duckfleet sql ./demo-fleet exp_01/run_01/metrics.duckdb "select
   count(*) from events"` prints 1000; an `INSERT` exits 3 with a
   `read_only` message; a `COPY ... TO` exits 3 with `blocked`.
4. `uv run duckfleet summarize ./demo-fleet exp_01/run_01/metrics.duckdb
   main.events` prints 3 column rows with min, max, avg and quantiles; the
   second call says cached; `--refresh` recomputes.
5. `uv run duckfleet preview ... main.events --rows 5 --offset 5 --order-by
   ts --desc` prints 5 rows in descending time order.
6. UI on Windows and (through AppTest in CI) Linux: Database page for
   `exp_01/run_01/metrics.duckdb` shows 2 tables, the Columns tab, a
   computed Summary that is cached on the next visit, a Preview page with
   sorting and a column subset; **Open in SQL** lands on the SQL page with
   the template query; the SQL page runs it, shows a truncated result with
   max rows 5, downloads CSV and Parquet with non-zero sizes, shows the
   `read_only` message for an `INSERT`, and a `timeout` message for the
   cross-join query with timeout 1 s, after which another query runs.
7. A database with `TIMESTAMP WITH TIME ZONE` columns previews and
   queries without error (pytz).
8. Docs updated and versions bumped to 0.2.0.

## 9. Not in v0.2 (do not build)

Plot page (v0.3), cross-database attach and overlays (v0.4), query cancel
button (the timeout is the only cancel), saved queries, a code editor
widget, summaries inside the catalog, SQL autocomplete, non-blocking scan.

## 10. Open items for the implementer (TBC)

1. `"TimeZone": "UTC"` inside the connect `config` dict: verify it is
   accepted together with `lock_configuration`. If not, drop
   `lock_configuration` from the dict, run `SET TimeZone = 'UTC'` and then
   `SET lock_configuration = true` on the fresh connection, and verify
   `SET` is refused afterwards.
2. `st.switch_page` with the `st.Page` objects from `nav.py`: if it fails
   under `st.navigation`, fall back to `st.page_link(nav.PAGES["sql"])`
   next to the button and keep the session-state hand-off.
3. `AppTest` and page switching: assert the state, not the navigation.
4. Preview of very wide rows (lists, structs) in `st.dataframe`: if a
   column type is not displayable, cast it to VARCHAR in the preview SQL
   and say so in the caption.
5. If `to_arrow_table()` is missing on an older duckdb within the pin, use
   `fetch_arrow_table()`; keep the pin `duckdb>=1.1,<2`.
