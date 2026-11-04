# Implementation plan v0.1: scan + fleet overview

Status: implemented as 0.1.0 on 2026-11-04 (deviations and results: `CHANGELOG.md`
0.1.0, `docs/DECISIONS.md` D-015 to D-018). Written for Sonnet 5.5 or any
Claude Code session. Read `CLAUDE.md` first, then this file top to bottom.
Everything here is decided unless marked (TBC) or listed in Section 10.
Facts marked (verified) were checked on 2026-11-04 against duckdb 1.5.6,
Python 3.11.9, Windows 11; Linux-specific facts are marked (verify on Linux).

## Key immediate next steps

1. Phase 0 is done (uv workspace, docs, CI). Start at Phase 1 (Section 5).
2. One phase at a time. A phase is done when `uv run pytest` and
   `uv run ruff check .` are green locally; then commit with the phase name.
3. After Phase 7, tick the acceptance checklist (Section 8) and stop. Do not
   start v0.2 features.

## 1. What v0.1 delivers

1. `duckfleet scan <root>`: finds every DuckDB file under `<root>`, profiles
   each one read-only in a parallel worker pool, stores one JSON profile per
   file in the user cache, and builds a DuckDB catalog from those profiles.
   Rescans reuse profiles of unchanged files.
2. `duckfleet report <root>`: text summary of the fleet from the catalog
   (counts, schema families, table variants, time windows, health, errors).
3. `duckfleet make-demo <dir>`: generates a small demo fleet with two schema
   families, different time windows, a legacy file, an empty database, a
   corrupt file, an SQLite look-alike and a stale WAL sibling. Tests and the
   acceptance checklist use it.
4. `duckfleet-ui`: a Streamlit app with one page, **Overview**: choose a root
   folder, run a scan (page blocks behind a progress bar), then see the fleet
   table, schema families, family diff, table variants, time-window timeline
   and health list.
5. Everything works on Windows and Linux; CI runs the test suite on both.

## 2. Repository layout after v0.1

```
duckfleet/
  pyproject.toml                    uv workspace root (virtual), dev deps, ruff, pytest
  packages/duckfleet-core/
    pyproject.toml                  duckdb, pydantic, platformdirs, psutil
    src/duckfleet_core/
      __init__.py                   __version__
      __main__.py                   python -m duckfleet_core -> cli.main()
      cli.py                        argparse: scan, report, make-demo, paths
      config.py                     ScanConfig + resolved defaults
      models.py                     pydantic models: DatabaseProfile and parts, ScanSummary
      discovery.py                  walk + header sniff -> Candidate list
      profiler.py                   worker entry profile_database(...) -> dict, never raises
      fingerprint.py                canonical schemas, fingerprints, families, variants, diff
      tags.py                       path-token tags (TUNING)
      store.py                      ProfileStore: per-file JSON, root key, recent roots
      coordinator.py                run_scan(): discovery -> pool -> store -> catalog
      catalog.py                    build_catalog() and CatalogReader
      report.py                     text/markdown report from CatalogReader
      demo.py                       make_demo_fleet()
    tests/
      conftest.py                   demo fleet fixture (uses demo.py), tmp cache dir
      test_discovery.py
      test_profiler.py
      test_fingerprint.py
      test_tags.py
      test_store.py
      test_coordinator.py
      test_catalog.py
      test_cli.py
      test_core_smoke.py            keep: asserts core never imports streamlit
  packages/duckfleet-ui/
    pyproject.toml                  duckfleet-core, streamlit, plotly, pandas, pyarrow
    src/duckfleet_ui/
      __init__.py
      launch.py                     console script duckfleet-ui -> streamlit run app.py
      app.py                        st.set_page_config + st.navigation([Overview])
      scan_runner.py                runs the scan as a subprocess, parses JSON-lines progress
      catalog_access.py             st.cache_data wrappers around CatalogReader (DataFrames)
      views/__init__.py
      views/overview.py             render()
    tests/
      _overview_app.py              tiny script that calls views.overview.render()
      test_ui_smoke.py              AppTest on _overview_app.py against a demo catalog
```

Test file basenames must be unique across both packages (pytest imports
them as top-level modules; duplicates fail collection, verified).

## 3. Data contracts

### 3.1 ScanConfig (`config.py`)

Pydantic model. Field, default, meaning:

| Field | Default | Meaning |
|---|---|---|
| `extensions` | `[".duckdb", ".db", ".ddb"]` | lower-case, with dot; candidates by extension |
| `sniff_all_files` | `False` | also header-sniff files with other extensions |
| `exclude_dirs` | `[".git", ".venv", "__pycache__", "node_modules", ".duckfleet"]` | directory names skipped at any depth |
| `follow_symlinks` | `False` | never follow symlinked directories by default |
| `max_files` | `1000` | stop walking after this many candidates; `truncated=True` |
| `workers` | `None` | `None` resolves to `max(1, os.cpu_count() - 2)` |
| `worker_threads` | `1` | DuckDB `threads` setting per worker |
| `worker_memory_limit_mb` | `None` | `None` resolves to `min(2048, total_ram_mb // 2 // workers)`, floor 256 |
| `stats_row_threshold` | `20_000_000` | above this, non-temporal stats run on a sample |
| `stats_sample_rows` | `1_000_000` | `USING SAMPLE n ROWS` size |
| `include_views_in_fingerprint` | `False` | views are listed but not fingerprinted |
| `count_view_rows` | `False` | views get `row_count=None` unless true |
| `tag_patterns` | `tags.DEFAULT_TAG_PATTERNS` | regexes with named groups, see 5.3 |
| `full` | `False` | ignore cached profiles, re-profile everything |
| `cache_dir` | `None` | `None` resolves to `platformdirs.user_cache_dir("duckfleet")` |

Provide `resolved_workers()`, `resolved_memory_limit_mb()`,
`resolved_cache_dir()`. Workers receive `config.model_dump(mode="json")`
(a plain dict, always picklable). `PROFILE_SCHEMA_VERSION = 1` is a module
constant in `models.py`; bump it whenever `DatabaseProfile` changes shape,
which invalidates cached profiles.

### 3.2 DatabaseProfile (`models.py`)

Pydantic models, serialised with `model_dump_json()` and loaded with
`model_validate_json()`. All timestamps are ISO 8601 strings; `profiled_at`
is UTC.

```python
class ColumnStats(BaseModel):
    non_null_count: int | None = None
    min: str | None = None          # numeric and temporal columns only
    max: str | None = None
    approx_distinct: int | None = None
    sampled: bool = False

class ColumnInfo(BaseModel):
    name: str
    ordinal: int                    # duckdb_columns().column_index, 1-based
    data_type: str                  # duckdb_columns().data_type verbatim, e.g. "DECIMAL(18,3)"
    nullable: bool
    is_temporal: bool               # see TUNING(temporal-types)
    is_numeric: bool
    stats: ColumnStats | None = None

class TableProfile(BaseModel):
    schema_name: str
    name: str
    kind: Literal["table", "view"]
    row_count: int | None           # exact for tables; None for views unless count_view_rows
    estimated_size: int | None      # duckdb_tables().estimated_size, tables only
    columns: list[ColumnInfo]
    fp_strict: str                  # 16 hex chars, see 5.3
    fp_loose: str
    time_min: str | None            # min over temporal columns' mins
    time_max: str | None
    error: str | None = None        # per-table failure, table still listed

class ProfileError(BaseModel):
    type: Literal["locked", "not_duckdb", "corrupt", "incompatible_version",
                  "encrypted", "missing", "worker_crashed", "other"]
    message: str                    # first 500 chars of the exception text
    exception: str                  # exception class name

class DatabaseProfile(BaseModel):
    profile_schema_version: int = PROFILE_SCHEMA_VERSION
    rel_path: str                   # posix separators, relative to root
    abs_path: str
    file_name: str
    size_bytes: int
    mtime_ns: int
    has_wal: bool                   # "<path>.wal" exists next to the file
    storage_version: int | None     # uint64 little-endian at byte offset 12 (verified)
    duckdb_version: str             # library used for profiling
    profiled_at: str
    profile_seconds: float
    status: Literal["ok", "error"]
    error: ProfileError | None = None
    tags: dict[str, str] = {}
    schemas: list[str] = []
    tables: list[TableProfile] = []
    fp_strict: str | None = None    # None when status=error or no tables
    fp_loose: str | None = None
    time_min: str | None = None
    time_max: str | None = None
    total_rows: int | None = None   # sum of table row counts (tables only)
    health: list[str] = []          # "wal_present", "empty_database", "no_temporal_columns",
                                    # "table_error:<schema>.<table>"
```

`ScanSummary` (also in `models.py`): `run_id`, `root`, `started_at`,
`finished_at`, `duckfleet_version`, `duckdb_version`, `workers`,
`files_walked`, `files_candidates`, `files_rejected`, `files_profiled`,
`files_reused`, `files_failed`, `truncated`, `catalog_path`,
`rejected: list[RejectedFile]` (`rel_path`, `reason`), `config: ScanConfig`.

### 3.3 Profile store on disk (`store.py`)

```
<cache_dir>/roots/<root_key>/
    root.txt              absolute root path, for humans
    profiles/<file_key>.json
    catalog.duckdb        derived index, rebuilt at the end of every scan
    catalog.duckdb.tmp    build target, replaced atomically
    last_scan.json        ScanSummary of the latest run
<cache_dir>/recent_roots.json     list of {root, last_scanned_at}, newest first, max 20
```

1. `root_key = sha1(os.path.normcase(str(root.resolve())).replace("\\", "/"))[:16]`.
2. `file_key = sha1(rel_path)[:24]`.
3. Reuse rule (in the coordinator): an existing profile is reused when
   `size_bytes`, `mtime_ns` and `profile_schema_version` match and
   `status == "ok"`. Errors are always retried. `full=True` reuses nothing.
4. `prune(keep_rel_paths)` deletes profiles of files no longer discovered.
5. The JSON profiles are the source of truth; the catalog is derived and
   always rebuilt from all profiles. Never update the catalog incrementally.

### 3.4 Catalog (`catalog.py`)

A DuckDB file. Built by `build_catalog(profiles, summary, rejected,
catalog_path, tmp_path)` (signature in Phase 4):
write to `catalog.duckdb.tmp` (delete a stale tmp first), close the
connection, then `os.replace(tmp, catalog_path)` (verified on Windows when
no process holds the target open). If `os.replace` raises `PermissionError`,
retry 5 times with 0.5 s sleeps, then raise with a message naming the file.

Tables (column types in DuckDB terms):

```
scan_runs(run_id VARCHAR, root VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP,
          duckfleet_version VARCHAR, duckdb_version VARCHAR, workers INTEGER,
          files_walked INTEGER, files_candidates INTEGER, files_rejected INTEGER,
          files_profiled INTEGER, files_reused INTEGER, files_failed INTEGER,
          truncated BOOLEAN, config_json VARCHAR)
databases(rel_path VARCHAR PRIMARY KEY, file_name VARCHAR, abs_path VARCHAR,
          size_bytes BIGINT, mtime_ns BIGINT, has_wal BOOLEAN, storage_version BIGINT,
          status VARCHAR, error_type VARCHAR, error_message VARCHAR,
          profiled_at TIMESTAMP, profile_seconds DOUBLE,
          n_schemas INTEGER, n_tables INTEGER, n_views INTEGER, total_rows BIGINT,
          fp_strict VARCHAR, fp_loose VARCHAR, family_strict VARCHAR, family_loose VARCHAR,
          time_min TIMESTAMP, time_max TIMESTAMP, tags_json VARCHAR, health_json VARCHAR)
tables(rel_path VARCHAR, schema_name VARCHAR, table_name VARCHAR, kind VARCHAR,
       n_columns INTEGER, row_count BIGINT, estimated_size BIGINT,
       fp_strict VARCHAR, fp_loose VARCHAR, time_min TIMESTAMP, time_max TIMESTAMP,
       error VARCHAR)
columns(rel_path VARCHAR, schema_name VARCHAR, table_name VARCHAR, ordinal INTEGER,
        column_name VARCHAR, data_type VARCHAR, nullable BOOLEAN,
        is_temporal BOOLEAN, is_numeric BOOLEAN, non_null_count BIGINT,
        min_value VARCHAR, max_value VARCHAR, approx_distinct BIGINT, sampled BOOLEAN)
families(level VARCHAR, family_id VARCHAR, fingerprint VARCHAR, n_members INTEGER,
         n_tables INTEGER, canonical_json VARCHAR)
table_variants(schema_name VARCHAR, table_name VARCHAR, fp_strict VARCHAR,
               n_members INTEGER, n_columns INTEGER, canonical_json VARCHAR)
rejected(rel_path VARCHAR, reason VARCHAR)
```

Rules:

1. `families.level` is `strict` or `loose`. `family_id` is `F1, F2, ...`
   for strict and `L1, L2, ...` for loose, numbered by `n_members`
   descending, then fingerprint ascending (deterministic).
2. Databases with `status = 'error'` or no tables have NULL fingerprints and
   NULL family ids; they appear in `databases` and in the health list, not
   in `families`.
3. `tags_json` and `health_json` are JSON text; readers parse them.
4. Insert with `executemany` over Python lists. Core has no pandas
   dependency; `CatalogReader` returns `list[dict]` and offers `df(sql)`
   that imports pandas lazily (UI only).
5. `CatalogReader(catalog_path)` opens a read-only connection inside each
   method and closes it in `finally`. Never cache a connection: an open
   handle blocks the next scan's `os.replace` on Windows.

Reader methods (each a thin SQL query): `scan_run()`, `databases()`,
`tables(rel_path=None)`, `columns(rel_path, schema_name, table_name)`,
`families(level)`, `family_members(level, family_id)`,
`family_table_matrix(level)`, `table_variants()`, `timeline()` (rel_path,
family_strict, time_min, time_max where both non-null), `health()`,
`rejected()`, `diff(rel_path_a, rel_path_b)` (delegates to
`fingerprint.diff_schemas` on the two profiles' table/column rows).

### 3.5 Progress events (JSON lines on stdout)

`duckfleet scan --progress json` prints one JSON object per line, nothing
else on stdout; logs go to stderr. The UI parses these.

```
{"event":"discovered","walked":N,"candidates":M,"rejected":R,"truncated":false}
{"event":"planned","to_profile":K,"reused":J}
{"event":"profiled","done":i,"total":K,"rel_path":"...","status":"ok|error","error_type":null,"seconds":0.42}
{"event":"catalog","path":"..."}
{"event":"finished","summary":{...ScanSummary as JSON...}}
{"event":"fatal","message":"..."}        exit code 2 follows
```

## 4. Verified DuckDB facts the code relies on

1. **File header**: bytes 8..11 are `b"DUCK"`; bytes 12..19 are the storage
   version as little-endian uint64 (64 for files written by duckdb 1.5.6).
   Discovery reads the first 20 bytes; a file without the magic is rejected
   with reason `not_duckdb_header` and never handed to a worker.
2. **DuckDB opens SQLite files**: `duckdb.connect("x.db", read_only=True)`
   on a file with an SQLite header succeeds (verified, even with extension
   autoload off). The header sniff in discovery is therefore mandatory, not
   a nicety.
3. **Errors on open** (all `duckdb.IOException`; classify by substring, see
   5.2):
   - empty or garbage file: `exists, but it is not a valid DuckDB database
     file` -> `not_duckdb`;
   - valid magic, bad body: `Corrupt database file: computed checksum ...
     does not match` -> `corrupt`;
   - truncated: `Could not read file ... Reached the end of the file` ->
     `corrupt`;
   - missing: `Cannot open database ... in read-only mode: database does not
     exist` -> `missing`;
   - writer in another process, Windows: `The process cannot access the file
     because it is being used by another process` -> `locked`; Linux
     (verify on Linux): `Could not set lock on file` / `Conflicting lock is
     held` -> `locked`;
   - storage version mismatch (TBC, not reproduced): message contains
     `version number` -> `incompatible_version`;
   - encrypted file (TBC, needs duckdb >= 1.4 encrypted fixture): message
     mentions `encrypt` -> `encrypted`.
4. **Several processes may open the same file read-only at once** (verified).
   One read-write process excludes everyone else.
5. **A stale `.wal` sibling does not block a read-only open** (verified);
   record `wal_present` in health and carry on.
6. **Connection config**: `duckdb.connect(path, read_only=True,
   config={"threads": 1, "memory_limit": "512MB",
   "autoinstall_known_extensions": False, "autoload_known_extensions": False})`
   works and the settings take effect (verified). Always pass all four.
7. **Introspection** (verified column names):
   - `duckdb_tables()`: `database_name, schema_name, table_name, internal,
     temporary, estimated_size, column_count`;
   - `duckdb_views()`: `database_name, schema_name, view_name, internal,
     temporary, column_count`;
   - `duckdb_columns()`: `database_name, schema_name, table_name,
     column_name, column_index, data_type, is_nullable, internal`;
   - `duckdb_databases()`: `database_name, internal, readonly, encrypted`;
     the user database is the one row with `internal = false`.
   Filter every query on `database_name = <user db>` and `NOT internal`.
8. `SUMMARIZE` exists (columns `column_name, column_type, min, max,
   approx_unique, avg, std, q25, q50, q75, count, null_percentage`) but is
   for v0.2; v0.1 computes its cheaper stats with explicit aggregates.
9. `os.replace` over a closed catalog file works on Windows (verified).
10. Installed versions on 2026-11-04: duckdb 1.5.6, streamlit 1.66.1,
    plotly 7.1.0, pandas 3.1.1, pyarrow 26.0.1, pydantic 2.14.2. pandas 3
    defaults to the string dtype and copy-on-write; write code that works
    on pandas 2.2 and 3.x.

## 5. Phases

Each phase: build, test, lint, commit. Signatures below are the contract;
private helpers are yours.

### Phase 1: discovery (`discovery.py`)

```python
@dataclass(frozen=True)
class Candidate:
    abs_path: Path
    rel_path: str            # posix separators
    size_bytes: int
    mtime_ns: int
    has_wal: bool
    storage_version: int | None

@dataclass(frozen=True)
class RejectedFile:
    rel_path: str
    reason: str              # "not_duckdb_header", "unreadable", "over_max_files"

@dataclass(frozen=True)
class DiscoveryResult:
    root: Path
    candidates: list[Candidate]
    rejected: list[RejectedFile]
    walked_files: int
    truncated: bool

def read_header(path: Path) -> tuple[bool, int | None]: ...   # (is_duckdb, storage_version)
def discover(root: Path, config: ScanConfig) -> DiscoveryResult: ...
```

Rules:

1. `os.walk(root, followlinks=config.follow_symlinks)`; prune `dirnames` in
   place for `exclude_dirs`; sort `dirnames` and `filenames` for
   deterministic order.
2. A file is examined when its lower-cased suffix is in `extensions`, or
   `sniff_all_files` is true. Skip names ending in `.wal`, `.tmp`.
3. Examined files are header-sniffed. Magic present -> candidate; absent ->
   rejected `not_duckdb_header`; OSError -> rejected `unreadable`.
4. After `max_files` candidates, stop walking, set `truncated=True`, and do
   not list further files.
5. `has_wal = (abs_path.parent / (abs_path.name + ".wal")).exists()`.
6. Candidates sorted by `size_bytes` descending (largest first balances the
   pool).

Tests: tree with nested dirs, excluded dir, SQLite look-alike `.db`, empty
`.duckdb`, `.wal` sibling, `max_files=2` truncation, a path with a space and
a non-ASCII character, `sniff_all_files` picking up a `.bin` DuckDB file.

### Phase 2: models and profiler (`models.py`, `profiler.py`)

```python
def profile_database(abs_path: str, rel_path: str, config_dict: dict) -> dict: ...
def classify_error(exc: BaseException) -> ProfileError: ...
```

`profile_database` is the process-pool entry point: plain argument types,
returns `DatabaseProfile.model_dump(mode="json")`, never raises. Steps:

1. `stat` the file, read the header (reuse `discovery.read_header`), set
   `has_wal`, `storage_version`, `tags` (Phase 3; call `tags.extract_tags`).
2. Connect with the four settings from Section 4 item 6; `memory_limit` from
   `config_dict["worker_memory_limit_mb"]` resolved by the coordinator
   (pass the resolved number in the dict under the same key).
3. User database name: `select database_name from duckdb_databases() where
   not internal`.
4. Tables, views, columns from the introspection functions (Section 4 item
   7), excluding `internal` and `temporary`, ordered by schema, table,
   `column_index`.
5. Per table (not view unless `count_view_rows`): `select count(*) from
   "schema"."table"`; quote identifiers by doubling embedded `"`.
6. Per table, one aggregate query over all columns:
   `count("c")` for every column; `min("c")`, `max("c")` for numeric and
   temporal columns; `approx_count_distinct("c")` for every column whose
   type is not BLOB, STRUCT, LIST, MAP, UNION, ARRAY or a nested type
   (TUNING(stats-column-types)). If `row_count > stats_row_threshold`:
   run temporal `min`/`max` exactly in one query, and everything else with
   `FROM "schema"."table" USING SAMPLE {stats_sample_rows} ROWS`, marking
   `sampled=True`.
7. Serialise: `datetime`/`date` -> `isoformat()`; `Decimal`, ints, floats
   -> `str()`; `None` stays `None`.
8. Table `time_min`/`time_max`: compare as `datetime` (dates become
   midnight; timezone-aware values are converted to UTC and made naive,
   TUNING(timezones)); serialise after comparing.
9. Per-table exception: set `TableProfile.error`, add
   `table_error:<schema>.<table>` to health, continue.
10. Database-level: `fp_strict`, `fp_loose` (Phase 3), `time_min`,
    `time_max` over tables, `total_rows`, health flags `empty_database`
    (no tables), `no_temporal_columns`, `wal_present`.
11. Any exception around connect or introspection -> `status="error"`,
    `error=classify_error(exc)`, tables empty. `finally: con.close()`.

`classify_error` applies the substrings from Section 4 item 3 in this order:
locked, missing, not_duckdb, corrupt, incompatible_version, encrypted,
other. Matching is case-insensitive.

Tests: profile a generated file (row counts, column types, temporal
min/max, tags); wide table with 200 columns; table above a tiny
`stats_row_threshold` (set it to 100 in the test) marks `sampled`; empty
database -> `empty_database`; corrupt file -> `corrupt`; lock test: start a
subprocess that opens the file read-write, writes a marker file, sleeps;
wait for the marker; assert `error.type == "locked"`; kill the child in
`finally`. Mark the lock test `@pytest.mark.timeout(60)`.

### Phase 3: fingerprints and tags (`fingerprint.py`, `tags.py`)

```python
def normalize_type(data_type: str) -> str: ...           # identity in v0.1, TUNING(type-normalization)
def canonical_table(columns: list[ColumnInfo], strict: bool) -> list: ...
def table_fingerprint(columns: list[ColumnInfo], strict: bool) -> str: ...
def database_fingerprint(tables: list[TableProfile], strict: bool, include_views: bool) -> str | None: ...
def assign_families(profiles: list[DatabaseProfile], strict: bool) -> dict[str, str]: ...   # rel_path -> family_id
def table_variants(profiles: list[DatabaseProfile]) -> list[TableVariant]: ...
SchemaMap = dict[tuple[str, str], list[tuple[str, str]]]   # (schema, table) -> [(column, type)]
def schema_map_from_profile(tables: list[TableProfile]) -> SchemaMap: ...
def schema_map_from_rows(table_rows: list[dict], column_rows: list[dict]) -> SchemaMap: ...
def diff_schemas(a: SchemaMap, b: SchemaMap) -> SchemaDiff: ...

DEFAULT_TAG_PATTERNS = [
    r"(?P<key>[A-Za-z][A-Za-z0-9]*?)[_=-](?P<value>\d+)",       # exp_12, run-03, seed=7
    r"(?P<key>[A-Za-z][A-Za-z0-9]*)=(?P<value>[^/=\\]+)",       # model=resnet
]
def extract_tags(rel_path: str, patterns: list[str]) -> dict[str, str]: ...
```

Rules:

1. Strict canonical table: `sorted([[name, normalize_type(type)] ...])` by
   name. Loose: `sorted([name ...])`. Column order never matters (decision
   D-007).
2. `table_fingerprint = sha256(json.dumps(canonical, separators=(",", ":"),
   ensure_ascii=False)).hexdigest()[:16]`.
3. `database_fingerprint` hashes `sorted([[schema, name, table_fp] ...])`
   over tables (and views when `include_views`). Returns `None` when there
   are no tables.
4. `assign_families`: group by fingerprint, order groups by size descending
   then fingerprint, label `F1...` (strict) or `L1...` (loose). Profiles
   with `None` fingerprint get no label.
5. `table_variants`: for each `(schema, table)` across all `ok` profiles,
   the distinct `fp_strict` values with member counts and the canonical
   column list.
6. `SchemaDiff`: `only_in_a`, `only_in_b` (table names), and per common
   table `columns_only_in_a`, `columns_only_in_b`, `type_changes`
   (`name, type_a, type_b`). Pure function over two `SchemaMap`s; no I/O.
   `CatalogReader.diff` builds the maps from `tables` and `columns` rows
   with `schema_map_from_rows`; the profiler and tests use
   `schema_map_from_profile`.
7. `extract_tags`: split `rel_path` on `/`, drop the file extension of the
   last component, apply every pattern with `finditer` to each component
   from root to leaf; later components override earlier ones; keys are
   lower-cased. Mark the function `# TUNING(tag-patterns)`.

Tests: known fingerprints are stable across runs; column order change
keeps the fingerprint; type change alters strict but not loose; diff of
two hand-built table lists; tags for `exp_12/run_03/data.duckdb` ->
`{"exp": "12", "run": "03"}`, `model=resnet/seed=7/x.duckdb` ->
`{"model": "resnet", "seed": "7"}`, no false positives on `data.duckdb`.

### Phase 4: store, coordinator, catalog (`store.py`, `coordinator.py`, `catalog.py`)

```python
class ProfileStore:
    def __init__(self, cache_dir: Path, root: Path): ...
    root_dir: Path; profiles_dir: Path; catalog_path: Path; tmp_catalog_path: Path
    def load(self, rel_path: str) -> DatabaseProfile | None: ...
    def save(self, profile: DatabaseProfile) -> None: ...
    def load_all(self) -> list[DatabaseProfile]: ...
    def prune(self, keep_rel_paths: set[str]) -> int: ...
    def save_summary(self, summary: ScanSummary) -> None: ...
    def load_summary(self) -> ScanSummary | None: ...

def remember_root(cache_dir: Path, root: Path) -> None: ...
def recent_roots(cache_dir: Path) -> list[dict]: ...

def run_scan(root: Path, config: ScanConfig,
             on_event: Callable[[dict], None] | None = None) -> ScanSummary: ...

def build_catalog(profiles: list[DatabaseProfile], summary: ScanSummary,
                  rejected: list[RejectedFile], catalog_path: Path, tmp_path: Path) -> None: ...
class CatalogReader: ...   # Section 3.4
```

`run_scan` steps, emitting the events of Section 3.5:

1. `discover`, emit `discovered`.
2. Decide reuse per candidate (Section 3.3 rule 3), emit `planned`.
3. Pool: `concurrent.futures.ProcessPoolExecutor(max_workers=workers,
   mp_context=multiprocessing.get_context("spawn"))` on both OSes. Submit
   `profile_database(abs, rel, config_dict)` largest file first; consume
   with `as_completed`; validate each dict into `DatabaseProfile`; `save`;
   emit `profiled`. When `workers == 1`, call `profile_database` in-process
   (no pool), which keeps debugging and most tests simple; at least one
   test runs with `workers=2` to exercise the pool.
4. `BrokenProcessPool`: mark every unfinished candidate as `status="error"`,
   `error.type="worker_crashed"`, save those profiles, continue to the
   catalog step. Document in `docs/LIMITS.md` (L-10).
5. `prune` profiles of files not discovered this run.
6. `build_catalog` from `load_all()`, emit `catalog`.
7. `save_summary`, `remember_root`, emit `finished`, return the summary.

Tests: full run on the demo fleet with `workers=1` and `workers=2`
(assert identical catalogs apart from timings); second run reuses every
profile (`files_reused == files_candidates`, and runs in a fraction of the
time); `full=True` reuses none; deleting a file prunes its profile; catalog
tables have the expected row counts; `CatalogReader.families("strict")`
returns the expected members; `os.replace` after a previous catalog exists.

### Phase 5: CLI and demo (`cli.py`, `__main__.py`, `report.py`, `demo.py`)

```
duckfleet scan ROOT [--workers N] [--max-files N] [--full] [--cache-dir DIR]
                    [--extensions .duckdb,.db,.ddb] [--sniff-all] [--progress text|json|none]
duckfleet report ROOT [--format text|md] [--cache-dir DIR]
duckfleet make-demo DIR [--seed N]
duckfleet paths ROOT [--cache-dir DIR]          prints root_dir and catalog path
```

1. argparse only. Exit codes: 0 success (even if some files failed), 1
   usage error, 2 fatal (root missing, catalog build failed).
2. `--progress text` prints one line per event to stderr; `json` prints
   Section 3.5 lines to stdout; `none` is silent. Logging goes to stderr.
3. `report` sections: summary (files walked, candidates, rejected, ok,
   failed, reused, workers, duration), strict families with members and
   table lists, loose families, table variants, time windows (one line per
   database, sorted by `time_min`), health flags, errors with type and
   message, rejected files with reason.
4. `make_demo_fleet(dir, seed=0)` creates exactly:
   - `exp_01/run_01..03/metrics.duckdb`: `events(ts TIMESTAMP, value DOUBLE,
     run_id INTEGER)` with 1,000 rows over a 7-day window per run, windows
     consecutive across runs; `config(key VARCHAR, value VARCHAR)` identical
     across the three runs;
   - `exp_02/run_01..03/metrics.duckdb`: same, but `value FLOAT` (differs in
     strict, same in loose) and windows following exp_01;
   - `exp_03/run_01..02/metrics.duckdb`: `events` gains `quality SMALLINT`;
     extra table `notes(id INTEGER, text VARCHAR)`;
   - `legacy/old.db`: a DuckDB file with `samples(t DATE, x DOUBLE)`;
   - `legacy/sqlite_lookalike.db`: `b"SQLite format 3\x00"` + zeros
     (rejected by the sniff);
   - `empty/empty.duckdb`: DuckDB file with no tables;
   - `broken/corrupt.duckdb`: first 12 bytes of a real file + zeros to
     4096 bytes + 8 KiB random bytes (`corrupt` error);
   - `broken/truncated.duckdb`: 100 random bytes (rejected by the sniff);
   - `exp_01/run_01/metrics.duckdb.wal`: empty file (health `wal_present`).
   Expected after a scan: 11 candidates, 2 rejected, 10 ok, 1 failed
   (`corrupt`); strict families: two of 3 members (exp_01 and exp_02; which
   of them gets the label F1 is a hash accident), one of 2 (exp_03), one of
   1 (legacy); loose families: one of 6, one of 2, one of 1; `events` has 3
   strict variants; the empty database has no family and appears in health.
   Tests assert family sizes and memberships, never a label-to-experiment
   mapping.

### Phase 6: UI overview (`duckfleet_ui`)

1. `launch.py`: `main()` sets `sys.argv = ["streamlit", "run", <app.py path>,
   *extra_args]` and calls `streamlit.web.cli.main()`.
2. `app.py`: `st.set_page_config(page_title="duckfleet", layout="wide")`,
   `st.navigation([st.Page(overview.render, title="Overview", default=True)])`
   then `.run()`. Views live in `views/`, never `pages/`.
3. `scan_runner.py`: `run_scan_subprocess(root, options, on_event)` runs
   `[sys.executable, "-m", "duckfleet_core", "scan", root, "--progress",
   "json", ...]` with `env={**os.environ, "PYTHONUTF8": "1",
   "PYTHONIOENCODING": "utf-8"}`, reads stdout line by line, parses each
   JSON object and calls `on_event`; collects stderr and shows it in an
   expander when the exit code is not 0. The scan is a subprocess so the
   process pool never runs inside the Streamlit server (decision D-004).
4. `catalog_access.py`: `@st.cache_data` functions taking
   `(catalog_path: str, catalog_mtime_ns: int)` so a rebuilt catalog
   invalidates the cache; each calls `CatalogReader` and returns a
   DataFrame.
5. `views/overview.py` `render()`:
   - sidebar: root text input with a selectbox of recent roots, workers
     (default resolved value), max files, checkboxes `full rescan`,
     `sniff all files`; buttons **Scan** and **Load existing catalog**;
     the initial root and the cache directory default from the
     environment variables `DUCKFLEET_ROOT` and `DUCKFLEET_CACHE_DIR` when
     set (the smoke test relies on this);
   - scan: `with st.status("Scanning...")` and `st.progress`, updated from
     events; the page blocks until `finished`, then `st.rerun()`;
   - metrics row: candidates, ok, failed, rejected, strict families, loose
     families, total rows, fleet time span;
   - fleet table (`st.dataframe`): rel_path, size, n_tables, total_rows,
     family_strict, family_loose, time_min, time_max, tags, status;
     a selectbox sets `st.session_state["selected_rel_path"]` (used from
     v0.2);
   - families: strict/loose toggle, table of families with members, and
     the family-by-table matrix (rows families, columns `schema.table`,
     cell = members having the table);
   - table variants table;
   - diff: two selectboxes (database or family), output as three small
     tables (tables only in A, only in B, column differences);
   - timeline: `plotly.express.timeline` with one bar per database,
     coloured by `family_strict`, databases without temporal columns listed
     below the chart;
   - health and errors table; rejected files table.
6. `tests/_overview_app.py` imports `duckfleet_ui.views.overview` and calls
   `render()`; `test_ui_smoke.py` builds a demo fleet in a temp dir, runs
   `run_scan` with `workers=1` into a temp cache, points the app at it via
   environment variables `DUCKFLEET_ROOT` and `DUCKFLEET_CACHE_DIR`, runs
   `AppTest.from_file(...)` with `default_timeout=60`, and asserts no
   exceptions and that the metrics row is present. If `st.navigation`
   misbehaves under AppTest, keep the test on the wrapper script (TBC).

### Phase 7: docs and acceptance

1. Update `docs/FEATURES.md` (v0.1 rows to `done v0.1`), `docs/LIMITS.md`
   (confirm numbers, add anything discovered), `docs/TUNING.md` (every
   `# TUNING(...)` marker in code has a row), `docs/ARCHITECTURE.md` (copy
   the final catalog schema there), `CHANGELOG.md` (0.1.0 entry),
   `README.md` quick start if any command changed.
2. Bump both package versions and the workspace version to `0.1.0`.
3. Run the acceptance checklist (Section 8) and record the result in
   `CHANGELOG.md` under 0.1.0.

## 6. Cross-cutting rules

1. Every DuckDB connection in core opens with `read_only=True` and the four
   settings from Section 4 item 6; the catalog build is the only writer and
   it writes only inside the cache directory.
2. `duckfleet_core` never imports streamlit, plotly or pandas at module
   level. `test_core_smoke.py` guards this by importing every
   `duckfleet_core` module in a subprocess and asserting `streamlit` is
   absent from `sys.modules` there; keep the pandas import lazy inside
   `CatalogReader.df`.
3. Quote every identifier in generated SQL with `"` and double embedded
   quotes. Never format user-provided strings into SQL; use parameters.
4. Logging through `logging.getLogger("duckfleet")`; never print from core
   except the CLI's progress output.
5. Type hints everywhere; pydantic for anything serialised; dataclasses for
   in-memory-only structures.
6. Every heuristic carries a `# TUNING(<id>)` comment and a row in
   `docs/TUNING.md`.
7. Tests use `tmp_path` and a temp cache dir; nothing touches the real user
   cache. Fixture fleets come from `demo.make_demo_fleet`.

## 7. Windows and Linux

1. Process pool always uses the `spawn` context; worker entry points live in
   importable modules with plain-type arguments.
2. `rel_path` uses `/` on both platforms; only `root_key` normalises case.
3. The UI subprocess sets `PYTHONUTF8=1` so non-ASCII paths survive on
   Windows consoles.
4. No `fcntl`, no `signal.SIGALRM`, no shell=True.
5. CI matrix: `ubuntu-latest` and `windows-latest`, Python 3.11, `uv sync
   --all-packages`, ruff, pytest. Both must be green before a phase is
   considered done.
6. Tests that need a second process use `subprocess.Popen([sys.executable,
   "-c", ...])` and clean up in `finally`.

## 8. Acceptance checklist (v0.1 done when all are ticked)

1. CI green on Windows and Linux.
2. `uv run duckfleet make-demo ./demo-fleet` then `uv run duckfleet scan
   ./demo-fleet` reports the exact numbers of Phase 5 item 4; a second scan
   reuses 10 profiles and retries the 1 failed one.
3. `uv run duckfleet report ./demo-fleet` shows four strict families (3, 3,
   2, 1 members) and three loose families (6, 2, 1), `events` with 3
   variants, the time windows in order, `wal_present` and
   `empty_database`, the `corrupt` error, and the 2 rejected files.
4. Lock test passes on both OSes with `error.type == "locked"`.
5. `uv run duckfleet-ui`, scan `./demo-fleet` from the sidebar on Windows
   and on Linux: progress visible, every section of Phase 6 item 5 renders
   without an exception; a full rescan and a cached load both work.
6. A scan of a folder with a non-ASCII name and a space works from the UI.
7. `workers` resolves to `max(1, cpu_count - 2)` and the subprocess uses it
   (visible in the `finished` summary).
8. `docs/FEATURES.md`, `docs/LIMITS.md`, `docs/TUNING.md`,
   `docs/ARCHITECTURE.md`, `CHANGELOG.md` updated; versions bumped to 0.1.0.

## 9. Not in v0.1 (do not build)

Database deep-dive page, SQL page, plots, cross-database attach,
non-blocking scan, scan cancel, content hashes of small tables, identity
query, SQLite or Parquet fleet members, encrypted files, query timeouts,
report export to files, watch folder. See `docs/ROADMAP.md`.

## 10. Open items for the implementer (TBC)

1. Verify the Linux lock error substring in CI and extend `classify_error`
   if the message differs.
2. The `incompatible_version` and `encrypted` substrings are unverified;
   leave the classification in place and add a test only if a fixture can
   be produced.
3. `st.navigation` under `AppTest`: if the wrapper-script approach is not
   enough, document the limitation in `docs/LIMITS.md` and keep the smoke
   test on `render()`.
4. If `os.replace` of the catalog fails on Windows during the UI test
   because Streamlit's cache holds a connection, the fix is in
   `catalog_access.py` (never keep a `CatalogReader` alive across calls),
   not in `catalog.py`.
5. pandas 3 behaviour in `st.dataframe` and `px.timeline` (string dtype,
   datetime columns): if something breaks, convert explicitly with
   `pd.to_datetime` and keep the pin `pandas>=2.2,<4`.
