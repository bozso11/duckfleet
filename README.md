# duckfleet

Explore a folder tree of DuckDB files as one fleet: how many files there
are, which table structures exist and how many of each, which time windows
and runs they hold, then deep dive into one file, query it and plot its
columns.

Status: v0.1 (scan + fleet overview) is specified in `docs/PLAN_v0.1.md`
and in implementation. `docs/FEATURES.md` says what works today;
`docs/ROADMAP.md` says what comes next.

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

The commands exist once v0.1 is implemented; the current tree has the
workspace skeleton, the docs and the plan.

## Packages

1. `packages/duckfleet-core`: discovery, read-only profiling in a parallel
   worker pool, schema fingerprints and families, profile cache, catalog
   (a DuckDB file), CLI. No UI dependency.
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
