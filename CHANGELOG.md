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
