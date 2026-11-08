# duckfleet

Explore a folder tree of DuckDB files as one fleet: how many files there
are, which table structures exist and how many of each, which time windows
and runs they hold, then deep dive into one file, query it and plot its
columns.

Status: v0.3 (scan, fleet overview, database deep dive, read-only SQL, plots) is
implemented; what it does is in `CHANGELOG.md` and `docs/FEATURES.md`, what
comes next in `docs/ROADMAP.md`. The plans are `docs/PLAN_v0.1.md`,
`docs/PLAN_v0.2.md` and `docs/PLAN_v0.3.md`.

## Quick start

Requires Python 3.11 and [uv](https://docs.astral.sh/uv/). Works on Windows
and Linux.

```bash
uv sync --all-packages
uv run duckfleet make-demo ./demo-fleet    # small example fleet to try things on
uv run duckfleet scan ./demo-fleet         # profiles every file, builds the catalog
uv run duckfleet report ./demo-fleet       # text summary of the fleet
uv run duckfleet sql ./demo-fleet exp_01/run_01/metrics.duckdb "select count(*) from events"
uv run duckfleet summarize ./demo-fleet exp_01/run_01/metrics.duckdb main.events
uv run duckfleet preview ./demo-fleet exp_01/run_01/metrics.duckdb main.events --rows 5 --order-by ts --desc
uv run duckfleet plot ./demo-fleet exp_01/run_01/metrics.duckdb main.events --kind line --x ts --y value --max-points 100
uv run duckfleet-ui                        # Streamlit app
```

1. `scan` writes one JSON profile per database and a catalog into the user
   cache (never into the scanned folder); `duckfleet paths ./demo-fleet`
   prints the location. A second scan reuses the profiles of unchanged files.
2. `sql`, `summarize` and `preview` take the root, the database as listed by
   `report` (its `rel_path`) and, for the last two, `SCHEMA.TABLE`. They open
   the database read-only: no writes, no file access, no extensions. Output is
   a text table, CSV or JSON (`--format`); exit code 3 means a query error.
   CSV and JSON are always UTF-8; the text table uses the console encoding,
   so set `PYTHONUTF8=1` on Windows to see every character in it.
   Summaries are cached next to the scan profiles until the file changes.
   `plot` prints the data a chart would draw, reduced inside DuckDB to the
   point budget (`--max-points`, default 50,000): time buckets with mean, min
   and max for a temporal x, numeric buckets, histogram bins, a reservoir
   sample for scatter, top-N folding for series and bar categories; a summary
   line (strategy, bucket width, points) goes to stderr, `--show-sql` adds the
   query. `make-demo --big` adds a table of 200,000 rows to try it on.
3. `duckfleet-ui` opens the Overview page: enter a root folder in the
   sidebar, press **Scan** (the page waits behind a progress bar) or **Load
   existing catalog**. The **Database** page shows the tables of one database
   with their columns, a cached per-column summary and a paged preview, and
   **Open in SQL** hands a query to the **SQL** page: a read-only editor with
   a row limit, a timeout, CSV and Parquet downloads and a query history.
   **Plot this table** hands the table to the **Plot** page: line, scatter,
   histogram or bar of chosen columns, a colour column for series, an SQL
   filter and a point budget; DuckDB reduces the data (time buckets with a
   min to max band, a sample for scatter) so the browser never gets more than
   the budget; the plotted data is shown as a table and downloadable as CSV.
   Extra arguments go to Streamlit, for example
   `uv run duckfleet-ui --server.port 8600`. The environment variables
   `DUCKFLEET_ROOT` and `DUCKFLEET_CACHE_DIR` set the initial root and the
   cache directory; `DUCKFLEET_QUERY_THREADS` and `DUCKFLEET_QUERY_MEMORY_MB`
   override the threads and memory of the query connection.
4. `uv run pytest` and `uv run ruff check .` run the checks CI runs on
   Windows and Linux.

## Packages

1. `packages/duckfleet-core`: discovery, read-only profiling in a parallel
   worker pool, schema fingerprints and families, profile cache, catalog
   (a DuckDB file), read-only query layer and summaries, CLI (`scan`,
   `report`, `make-demo`, `paths`, `sql`, `summarize`, `preview`, `plot`). No UI
   dependency.
2. `packages/duckfleet-ui`: Streamlit app over the core: fleet overview
   (v0.1), database deep dive and SQL (v0.2), plots (v0.3), cross-database
   views (v0.4).

## Documents

| File | What it answers |
|---|---|
| `docs/SCOPE.md` | What the tool is for, what it is not for |
| `docs/FEATURES.md` | Feature matrix with status per version and limits |
| `docs/LIMITS.md` | Every limit with its number and what happens beyond it |
| `docs/TUNING.md` | Heuristics that need tuning once real data is known |
| `docs/ARCHITECTURE.md` | Components, data flow, storage, boundary rules |
| `docs/DECISIONS.md` | Decision log |
| `docs/ROADMAP.md` | Version ladder, open options, out of scope |
| `docs/PLAN_v0.1.md`, `docs/PLAN_v0.2.md`, `docs/PLAN_v0.3.md` | Detailed implementation plans for v0.1, v0.2 and v0.3 |
| `docs/REVIEW_v0.1.md`, `docs/REVIEW_v0.2.md` | Reviews of v0.1.0 (fixed in 0.1.1) and v0.2.0 (fixed in 0.2.1) |
| `docs/PROPOSAL.md` | The original proposal and the answers that shaped it |
| `CHANGELOG.md` | What each version shipped |
| `CLAUDE.md` | Working rules for LLM sessions in this repository |

## Ground rules

1. The tool never writes to a scanned database.
2. The core library never imports the UI.
3. Windows and Linux are equally supported; CI runs both.
4. Heuristics are marked `# TUNING(<id>)` in code and listed in
   `docs/TUNING.md`.
