# duckfleet: instructions for Claude Code sessions

duckfleet explores a folder tree of DuckDB files as one fleet: overview of
all files and their structures, then deep dive, SQL and plots on one file.
Python 3.11, uv workspace, core library + Streamlit UI.

## Read in this order

1. `docs/PLAN_v0.1.md` (or the plan for the version you are implementing):
   what to build, phase by phase, with contracts and acceptance checklist.
2. `docs/ARCHITECTURE.md`: components, data flow, boundary rules.
3. `docs/FEATURES.md`: what is done, planned, open or out of scope.
4. `docs/LIMITS.md` and `docs/TUNING.md`: numbers and heuristics.
5. `docs/DECISIONS.md`: why things are the way they are. Do not reopen a
   decision silently; add a new entry if you must change one.

## Rules

1. **Read-only.** Core never writes to a scanned database. Every connection
   to a scanned file is `read_only=True` with extension autoload off.
2. **Core/UI split.** `duckfleet_core` never imports streamlit, plotly or
   pandas at module level. The UI calls core; core never calls the UI.
3. **Windows and Linux are equally important.** Process pools use the
   `spawn` context, paths inside data use `/`, no POSIX-only modules. CI
   runs both; both must be green.
4. **Tests with every phase.** `uv run pytest` and `uv run ruff check .`
   green before a commit. Test fixtures come from `duckfleet_core.demo`,
   never from real user data. Test file basenames must be unique across
   packages.
5. **Docs are part of done.** Any change to behaviour, a limit or a
   heuristic updates `docs/FEATURES.md`, `docs/LIMITS.md` or
   `docs/TUNING.md` in the same commit, and `CHANGELOG.md` under Unreleased.
6. **Heuristics are marked.** Every rule that may need tuning once real
   data is known carries a `# TUNING(<id>)` comment and a row in
   `docs/TUNING.md` with the same id.
7. **Stay mainstream.** New dependencies only from widely used packages,
   with a `docs/DECISIONS.md` entry saying why.
8. **Scope.** Build what the current plan says and nothing from a later
   version. Open options go to `docs/ROADMAP.md`, not into the code.

## Commands

```bash
uv sync --all-packages          # install everything (Python 3.11 via .python-version)
uv lock                         # after editing any pyproject dependencies; commit uv.lock
uv run pytest                   # all tests
uv run ruff check .             # lint
uv run duckfleet make-demo ./demo-fleet
uv run duckfleet scan ./demo-fleet
uv run duckfleet report ./demo-fleet
uv run duckfleet-ui
```

## Layout

```
packages/duckfleet-core/src/duckfleet_core   library + CLI (no UI deps)
packages/duckfleet-ui/src/duckfleet_ui       Streamlit app (views/, never pages/)
docs/                                        plan, scope, features, limits, tuning, decisions, roadmap
```

## Commits

Small commits named after the plan phase, for example `phase 2: profiler`.
Do not bump versions except where the plan's final phase says so.
