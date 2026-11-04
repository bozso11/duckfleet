# Changelog

## Unreleased

### Phase 0 (2026-11-04)

1. uv workspace with two packages: `duckfleet-core` (duckdb, pydantic,
   platformdirs, psutil) and `duckfleet-ui` (streamlit, plotly, pandas,
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
2. `demo.py` (`make_demo_fleet`) built in this phase, ahead of the plan's
   Phase 5, because the Phase 1 and Phase 4 tests need real DuckDB files.
   `tags.py` came forward from Phase 3 because `ScanConfig` defaults to its
   patterns.
3. Discovery detail the plan left open: when the limit is hit, the first
   further DuckDB file is listed as rejected `over_max_files` and the walk
   stops (LIMITS L-01).
4. Cache directory uses `appauthor=False` (decision D-015).
