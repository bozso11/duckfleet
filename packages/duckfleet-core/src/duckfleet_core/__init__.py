"""duckfleet-core: discover, profile and fingerprint a folder tree of DuckDB files.

Rules for this package (see CLAUDE.md and docs/ARCHITECTURE.md):
- never imports streamlit or any UI library;
- never writes to a scanned database file; every connection is read_only=True;
- heuristics are marked with ``# TUNING(<id>)`` comments indexed in docs/TUNING.md.
"""

__version__ = "0.2.0"
