# DuckDB Fleet Explorer: proposal (v0.2, answers folded in)

Status: accepted on 2026-11-04 with the answers in Section 0. The detailed
plan for the first version is `docs/PLAN_v0.1.md`. Working name:
**duckfleet**.

## 0. Decisions taken on 2026-11-04

The owner accepted the defaults of the v0 proposal except where noted.

1. **File extensions**: `.duckdb`, `.db`, `.ddb` plus header sniffing
   (default kept).
2. **Sizes**: MB to low GB per file, tables up to tens of millions of rows
   (default kept; limits in `docs/LIMITS.md` are marked TBC until real data
   confirms them).
3. **DuckDB versions**: unknown; developed against the installed package
   (duckdb 1.5.6 on 2026-11-04). Incompatible files are reported per file.
4. **Writers during scan**: none expected; locked files reported and skipped
   (default kept).
5. **Same structure**: types count in the strict family, column order
   ignored, views listed but not fingerprinted (default kept).
6. **Time storage**: any temporal column qualifies, wide layout assumed for
   plotting, long layout via group-by (default kept).
7. **Run identity**: a convention exists but is unknown at this stage. Go
   with path-token tags and leave tuning points in the repository and the
   code (`docs/TUNING.md`, `# TUNING(...)` markers).
8. **Read-only**: yes, everywhere.
9. **Catalog location**: user cache directory, approved.
10. **Environment**: Windows and Linux equally important; no known package
    restrictions, exotic packages may be unavailable, so dependencies stay
    mainstream.
11. **Background scan**: a page that blocks behind a progress bar is
    acceptable for the first scan. Non-blocking becomes an open option.
12. **Implementer**: Sonnet 5.5 implements from the detailed plan; an
    Opus-class review at each version's acceptance checklist.
13. **Cross-database querying**: v0.4 (default kept).
14. **GUI verdict** accepted: Streamlit UI, separate core package, both set
    up with uv.
15. **Repository**: public GitHub repository, local folder under the
    owner's repos directory.

## 1. Problem statement

A folder tree holds up to a few hundred DuckDB files. Most share a
structure, some do not, and conventional database tools connect to one file
at a time. The tool must answer three things in order: first a fleet
overview (how many files, what they contain, which structures exist and how
many of each, whether same-structure files hold different data, time
windows or runs); then a deep dive into one database (tables, columns,
summaries, SQL); then visualisation of selected columns, with time series
as the main case.

## 2. GUI verdict: Streamlit, with a UI-free core

Streamlit for the UI, with everything that is not UI (discovery, profiling,
fingerprinting, catalog, query execution, plot data preparation) in a
separate core package that never imports Streamlit.

1. Streamlit is accepted in the owner's environment, and Streamlit +
   Plotly + pandas + duckdb are the libraries a weaker implementing LLM
   gets right most reliably. For a handover that is a real selection
   criterion.
2. `st.dataframe` already covers sortable, filterable tabular exploration,
   which is most of the overview and deep-dive screens.
3. The cost: Streamlit reruns the whole page script on every interaction,
   so state and long-running work need care (session state, caching, and a
   scan that runs outside the page script). The core/UI split keeps that
   cost contained in one layer and makes the UI swappable.
4. The one condition that would make me switch: interactive plotting of
   millions of raw points per chart. Then the UI moves to Panel + HoloViews
   with Datashader and the core stays unchanged. In this design DuckDB does
   the aggregation and downsampling, and Plotly renders at most tens of
   thousands of points, which covers time-series exploration.

Alternatives considered and not chosen: Dash (more code per screen,
callback plumbing), Panel (best for big plots, less LLM familiarity),
NiceGUI (small community), a PySide6 desktop app (best raw performance,
heaviest to build and maintain).

## 3. Components

### 3.1 Core package (no UI dependency)

1. **Discovery**: walks the folder recursively; a file is a candidate if its
   extension is in a configurable list or, optionally, for any extension;
   every candidate must carry the DuckDB magic bytes (`DUCK` at byte 8,
   verified) or it is rejected with a reason. Skips `.wal` and temp files,
   notes a WAL sibling as a health flag, records size and mtime, stops at
   the file cap with a warning.
2. **Profiler**: runs in a worker process, opens one file read-only, and
   returns a plain dict: schemas, tables, views, columns with types, exact
   row counts, cheap per-column stats (non-null count, min/max for numeric
   and temporal columns, approximate distinct count) below a row threshold
   and sampled above it, and min/max of every temporal column. Never writes
   to the file.
3. **Scan coordinator**: owns the process pool with `max(1, cpu_count - 2)`
   workers, caps each worker's DuckDB thread count (1) and memory limit so
   N workers cannot oversubscribe the machine, skips files whose size and
   mtime are unchanged since the last scan, collects results, and is the
   only writer of the catalog.
4. **Profile store and catalog**: one JSON profile per file in the user
   cache directory (source of truth); the catalog is a DuckDB file rebuilt
   from all profiles at the end of every scan, so it can be queried with
   SQL inside the tool. DuckDB allows one read-write process or several
   read-only processes on a file, never both, so the catalog is built in a
   temporary file and swapped in atomically.
5. **Fingerprinting and clustering**: see Section 4.
6. **Query layer**: read-only connections for the deep dive and SQL page
   (v0.2); `ATTACH ... (READ_ONLY)` for cross-database work (v0.4).
7. **CLI**: `duckfleet scan`, `report`, `make-demo`, `paths`.

### 3.2 Streamlit UI pages

1. **Overview** (v0.1): pick a folder, run a scan, see the fleet table,
   schema families, family diff, table variants, time-window timeline,
   health and error list.
2. **Database** (v0.2): deep dive into one selected file.
3. **SQL** (v0.2): read-only query editor with a result grid and download.
4. **Plot** (v0.3): column selection and charts for one table; across
   databases in v0.4.

### 3.3 Parallelism and "in the background, when it's safe"

1. One worker process per database file, pool size `max(1, cpu_count - 2)`,
   configurable, `spawn` context on both OSes.
2. "When it's safe" means: read-only opens only; a file locked by another
   writer is reported as unreadable and not retried; a file with a WAL
   sibling is flagged. Nothing in the tool ever writes to a scanned file.
3. "In the background" means: the workers run as separate processes while
   the UI shows progress. In v0.1 the page blocks behind the progress bar
   until the scan ends (accepted). The UI launches the scan as a subprocess
   and reads its progress events, which also keeps the process pool out of
   the Streamlit server process (the main Windows risk).

## 4. How the fleet questions get answered

### 4.1 Which structures exist and how many of each

1. For every database, a canonical schema description: every table with
   its sorted list of (column name, column type).
2. Hashed at two strictness levels: **strict** (names and types) and
   **loose** (names only). Column order is ignored. Views are listed but
   excluded from the fingerprint by default.
3. Databases with the same strict fingerprint form a **schema family**.
   The overview shows the number of families, member count per family, and
   a family-by-table matrix.
4. A **per-table fingerprint** answers finer questions: "table `events`
   has one variant across all 140 files, table `config` has three".
5. Selecting two families or files shows the diff: tables added or
   removed, columns added or removed, type changes.

### 4.2 Same structure, but different data, time windows or runs

1. **Time windows**: per table, min and max of every temporal column; per
   database, the union of those spans; a timeline with one bar per
   database shows at a glance whether files are consecutive windows,
   overlapping, or the same period. Integer epoch columns are a tuning
   point.
2. **Row counts** per table per database, side by side within a family.
3. **Content fingerprint of small tables** (v0.4): a hash of the sorted
   content of tables below a row cap, so the overview can say "identical
   config in 37 files, three other variants".
4. **Run or experiment identity**: folder and file name tokens become tags
   through configurable patterns (`exp_12/run_03/data.duckdb` gives
   `exp=12, run=03`). The real convention is unknown at this stage, so the
   patterns are a documented tuning point; a user-supplied identity query
   run against every file is an open option.

### 4.3 Health

Unreadable files with the reason (writer lock, storage version mismatch,
corrupt file, not a DuckDB file), WAL siblings, empty databases, per-table
errors.

## 5. Version ladder

See `docs/ROADMAP.md`: v0.1 scan + overview, v0.2 single-database deep
dive and SQL, v0.3 visualisation, v0.4 cross-database; then open options
and out of scope.

## 6. Documentation set for future LLMs

`README.md`, `CLAUDE.md`, `docs/SCOPE.md`, `docs/FEATURES.md` (feature
matrix with status per version), `docs/LIMITS.md`, `docs/TUNING.md`,
`docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/ROADMAP.md`,
`docs/PLAN_vX.md` per version, `CHANGELOG.md`. Every version updates them.

## 7. Default limits

See `docs/LIMITS.md`; all numbers are marked TBC until v0.1 acceptance on
real data.

## 8. Assumptions

1. Python 3.11, local single-user use, Windows and Linux.
2. The tool never writes to a scanned database.
3. Hundreds of files and GB-scale total data, not thousands of files or TB.
4. Files sit on local disk; network drives work but scan slowly.
5. Dependencies: duckdb, pydantic, platformdirs, psutil (core); streamlit,
   plotly, pandas, pyarrow (UI); standard library for CLI and concurrency.
6. The implementing LLM receives one detailed plan per version, with tests
   and an acceptance checklist.

## 9. Limitations already visible

See `docs/LIMITS.md`, "Known limitations without a number".
