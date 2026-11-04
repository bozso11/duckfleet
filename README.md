# duckfleet

Explore a folder tree of DuckDB files as one fleet: how many files there
are, which table structures exist and how many of each, which time windows
and runs they hold, then deep dive into one file, query it and plot its
columns.

Status: v0.1 (scan + fleet overview) is implemented; what it does is in
`CHANGELOG.md` (0.1.0) and `docs/FEATURES.md`, what comes next in
`docs/ROADMAP.md`. The plan it was built from is `docs/PLAN_v0.1.md`.

## Quick start

Requires Python 3.11 and [uv](https://docs.astral.sh/uv/). Works on Windows
and Linux.

```bash
uv sync --all-packages
uv run duckfleet make-demo ./demo-fleet    # small example fleet to try things on
uv run duckfleet scan ./demo-fleet         # profiles every file, builds the catalog
uv run duckfleet report ./demo-fleet       # text summary of the fleet
uv run duckfleet-ui                        # Streamlit app
```

1. `scan` writes one JSON profile per database and a catalog into the user
   cache (never into the scanned folder); `duckfleet paths ./demo-fleet`
   prints the location. A second scan reuses the profiles of unchanged files.
2. `duckfleet-ui` opens the Overview page: enter a root folder in the
   sidebar, press **Scan** (the page waits behind a progress bar) or **Load
   existing catalog**. Extra arguments go to Streamlit, for example
   `uv run duckfleet-ui --server.port 8600`. The environment variables
   `DUCKFLEET_ROOT` and `DUCKFLEET_CACHE_DIR` set the initial root and the
   cache directory.
3. `uv run pytest` and `uv run ruff check .` run the checks CI runs on
   Windows and Linux.

## Packages

1. `packages/duckfleet-core`: discovery, read-only profiling in a parallel
   worker pool, schema fingerprints and families, profile cache, catalog
   (a DuckDB file), CLI (`scan`, `report`, `make-demo`, `paths`). No UI
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
| `docs/PLAN_v0.1.md` | Detailed implementation plan for v0.1 |
| `docs/PROPOSAL.md` | The original proposal and the answers that shaped it |
| `CHANGELOG.md` | What each version shipped |
| `CLAUDE.md` | Working rules for LLM sessions in this repository |

## Ground rules

1. The tool never writes to a scanned database.
2. The core library never imports the UI.
3. Windows and Linux are equally supported; CI runs both.
4. Heuristics are marked `# TUNING(<id>)` in code and listed in
   `docs/TUNING.md`.
