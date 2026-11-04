"""Console script ``duckfleet-ui``: start the Streamlit app.

Extra command line arguments are handed to ``streamlit run``, for example
``duckfleet-ui --server.port 8600 --server.headless true``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def app_path() -> Path:
    """Path of ``app.py``, the script Streamlit runs."""
    return Path(__file__).with_name("app.py")


def main() -> None:
    from streamlit.web import cli as streamlit_cli

    sys.argv = ["streamlit", "run", str(app_path()), *sys.argv[1:]]
    sys.exit(streamlit_cli.main())


if __name__ == "__main__":
    main()
