# Plan: rename duckfleet to duckflock-explorer

Status: ready for implementation, 2026-11-09. Decided by the owner: the
product name changes everywhere (code, documentation, repository), and the
domain word "fleet" becomes "flock" throughout, including CLI commands,
classes and module names. Decision D-051. Written for Sonnet 5.5 or any
Claude Code session; read `CLAUDE.md` first.

## Key immediate next steps

1. One commit on `main`, `v0.5.0: rename to duckflock-explorer`, containing
   every change of Sections 2 to 6; CI green on both OSes.
2. The GitHub repository rename (Section 7) right after the push.
3. The local folder rename (Section 8) is done afterwards by the planning
   session, not by the build session, because the build session's working
   directory is that folder.

## 1. The new names

| Old | New |
|---|---|
| repository `bozso11/duckfleet` | `bozso11/duckflock-explorer` |
| local folder `C:\Users\bozso\repos\duckfleet` | `C:\Users\bozso\repos\duckflock-explorer` (Section 8) |
| workspace project `duckfleet-workspace` | `duckflock-explorer-workspace` |
| distributions `duckfleet-core`, `duckfleet-ui` | `duckflock-core`, `duckflock-ui` |
| import packages `duckfleet_core`, `duckfleet_ui` | `duckflock_core`, `duckflock_ui` |
| console scripts `duckfleet`, `duckfleet-ui` | `duckflock`, `duckflock-ui` |
| `python -m duckfleet_core` | `python -m duckflock_core` |
| environment variables `DUCKFLEET_*` | `DUCKFLOCK_*` |
| cache folder `platformdirs.user_cache_dir("duckfleet")` | `user_cache_dir("duckflock")` |
| excluded directory default `.duckfleet` | `.duckflock` |
| union-view fallback label column `duckfleet_database` | `duckflock_database` |
| logger name `duckfleet` | `duckflock` |
| Streamlit page title and sidebar header | `duckflock explorer` |
| modules `fleet.py`, `cli_fleet.py` | `flock.py`, `cli_flock.py` |
| tests `test_fleet.py`, `test_fleet_plot.py`, `test_cli_fleet.py` | `test_flock.py`, `test_flock_plot.py`, `test_cli_flock.py` |
| CLI commands `fleet-tables`, `fleet-sql` | `flock-tables`, `flock-sql` |
| classes `FleetSelection`, `FleetSummary`, `FleetHandle`, `FleetQueryError` | `FlockSelection`, `FlockSummary`, `FlockHandle`, `FlockQueryError` |
| functions `open_fleet`, `fleet_tables`, `run_fleet_query`, `export_fleet_query`, `describe_fleet`, `fetch_fleet_plot_data`, `make_demo_fleet`, `ensure_fleet_temp_dir`, ... | the same with `flock` |
| feature IDs `F-FLEET-nn` | `F-FLOCK-nn` |
| demo folder in docs and `.gitignore` `demo-fleet` | `demo-flock` |
| the word "fleet" in prose, UI text and docs | "flock" ("a flock of DuckDB files", "flock overview", "Flock time span") |

The word "explorer" appears in the repository name, the workspace project
name, the README title and the page title only; it is not part of any
import, command or variable name (short names in every import line and on
the command line; open to challenge, but decide before Section 3).

## 2. What the inventory says (2026-11-09)

96 tracked files hold the old product name, about 740 times; the domain
word adds about 900 occurrences. Every spelling found is either the bare
word, a known compound (`fleet-sql`, `FleetSelection`, `fleet_plot`,
`fleets`, `open_fleet`, test names) or the product name itself; no English
word containing "fleet" with another meaning exists in the tree, so plain
case-aware substring replacement is safe once the product name has been
handled first.

## 3. The rename script (run once, not committed)

A Python script in a temporary folder, run from the repository root. Order
matters: paths first, then the longest spellings, then the bare words.

1. `git mv packages/duckfleet-core packages/duckflock-core`,
   `git mv packages/duckfleet-ui packages/duckflock-ui`, then inside them
   `git mv .../src/duckfleet_core .../src/duckflock_core` and
   `git mv .../src/duckfleet_ui .../src/duckflock_ui`; then `git mv` of
   `fleet.py` to `flock.py`, `cli_fleet.py` to `cli_flock.py`,
   `test_fleet.py`, `test_fleet_plot.py`, `test_cli_fleet.py` to their
   `flock` names.
2. For every tracked text file (`git ls-files`, skip `uv.lock`), apply in
   this order:
   `duckfleet-workspace` -> `duckflock-explorer-workspace`;
   `duckfleet_core` -> `duckflock_core`; `duckfleet_ui` -> `duckflock_ui`;
   `duckfleet-core` -> `duckflock-core`; `duckfleet-ui` -> `duckflock-ui`;
   `DUCKFLEET_` -> `DUCKFLOCK_`; `duckfleet` -> `duckflock`;
   `DuckFleet` -> `DuckFlock` (none exist today, included for safety);
   then `demo-fleet` -> `demo-flock`; then the bare words
   `fleet` -> `flock`, `Fleet` -> `Flock`, `FLEET` -> `FLOCK` (substring
   replacement, which also turns `fleets` into `flocks`).
3. The README title becomes `# duckflock-explorer` and the one-line
   description under it says "duckflock explorer"; the Streamlit page title
   (`st.set_page_config`) and the sidebar header become `duckflock explorer`.
4. Regenerate the lock: `uv lock` (never edit `uv.lock` by hand), then
   `uv sync --all-packages`.
5. Final check that must return nothing:
   `git ls-files | xargs grep -il "fleet"` and `git ls-files | grep -i fleet`.

## 4. What the script cannot do (check by hand)

1. `pyproject.toml` of both packages: `[project] name`, `[project.scripts]`
   (`duckflock = "duckflock_core.cli:main"`, `duckflock-ui =
   "duckflock_ui.launch:main"`), `[tool.hatch.build.targets.wheel]
   packages`; the root `[tool.uv.sources]` and `[dependency-groups]`;
   `[tool.ruff.lint.isort] known-first-party`.
2. `config.py`: `APP_NAME = "duckflock"`, the `exclude_dirs` default
   `.duckflock`; the logger name in every `logging.getLogger("duckflock")`.
3. The CLI program name (`argparse` `prog="duckflock"`) and every message
   prefix (`duckflock: error:`, `duckflock: flock: 3 of 3 databases
   attached`, `duckflock: plot: ...`); tests assert these strings.
4. `scan_runner.py`: `[sys.executable, "-m", "duckflock_core", "scan", ...]`.
5. The union-view label fallback and its test (`duckflock_database`).
6. `.github/workflows/ci.yml` needs no change (it runs `uv sync
   --all-packages`), but confirm.
7. `.gitignore`: `demo-flock/`.
8. `docs/FEATURES.md` IDs `F-FLOCK-nn` and every cross-reference to them in
   the other docs (the substring pass covers them; confirm with grep).

## 5. Verification

1. `uv run ruff check .` clean; `uv run pytest` green (841 tests; every
   broken import, command name, env var, label column and message is
   caught here).
2. `uv run duckflock make-demo ./demo-flock`, `scan`, `report`,
   `flock-tables --family F1`, `flock-sql ... --all`, `plot --family F1`
   produce the outputs of `CHANGELOG.md` 0.4.0 item 3 to 5 with the new
   names.
3. `uv run duckflock-ui` starts; the page title reads "duckflock explorer";
   the four pages render on the demo (AppTest in CI; a manual check is
   recorded in the changelog if one is made, never as the last action of a
   turn).
4. CI green on `ubuntu-latest` and `windows-latest`.

## 6. Records

1. Versions `0.5.0` in the three `pyproject.toml` files and both
   `__init__.py` (a breaking rename of imports, commands and env vars).
2. `CHANGELOG.md` 0.5.0: "renamed to duckflock-explorer; no functional
   change", with the table of Section 1 condensed, and the note that an
   existing cache under the old folder is not reused (the first use after
   the upgrade rescans; the old folder can be deleted by hand).
3. `docs/DECISIONS.md` D-051 is already written; add the outcome of any
   deviation as D-052.
4. `docs/ARCHITECTURE.md`, `docs/LIMITS.md`, `README.md`, `CLAUDE.md`:
   the substring pass renames them; read them once for sentences that no
   longer read well ("a flock of DuckDB files" is the intended phrase).
5. Historical documents (plans, reviews, older changelog entries) are
   rewritten by the same pass, as the owner asked for every instance. The
   git history keeps the old names; it is not rewritten (D-051).

## 7. The GitHub repository

After the push: `gh repo rename duckflock-explorer --yes` from the
repository root (it updates the local `origin` URL). GitHub redirects the
old URL. Update the URL in `README.md` and `CLAUDE.md` in the same commit
before pushing (the new URL is known in advance), and confirm with `gh repo
view bozso11/duckflock-explorer --json url`.

## 8. The local folder (planning session, afterwards)

Stop every process that uses the folder (Streamlit previews, test runs),
rename `C:\Users\bozso\repos\duckfleet` to
`C:\Users\bozso\repos\duckflock-explorer`, run `uv sync --all-packages`
there (the virtual environment is recreated if its paths went stale), move
the Claude session to the new folder, and update the local
`.claude/launch.json` (ignored by git) to `duckflock-ui`.

## 9. Cost

One session, one to two hours of wall clock, one commit plus the repository
rename; the effort of one of the earlier fix releases plus about half for
the domain word. Risk sits in the entry points and lock file, the env var
names in tests, the label column test, the CLI message prefixes and the
cache folder, all caught by the test suite or visible at once.
