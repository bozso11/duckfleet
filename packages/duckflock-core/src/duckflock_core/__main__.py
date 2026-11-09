"""``python -m duckflock_core`` runs the CLI (the Streamlit UI starts scans this way)."""

from duckflock_core.cli import main

# The guard matters: multiprocessing's spawn context re-imports the main module in
# every worker, and the smoke test imports every module of the package.
if __name__ == "__main__":
    raise SystemExit(main())
