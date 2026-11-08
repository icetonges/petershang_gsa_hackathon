"""Root entry point for local development.

Run locally:
    uv run python main.py

Or via the installed script:
    uv run mcp-a-creator
"""

import sys
from pathlib import Path

# src/ layout: make the package importable when this file is run directly
# (e.g. `python main.py` from a Git-backed Databricks App deployment, where
# the package is not pip-installed and PYTHONPATH is not set).
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mcp_server_creator.app import main  # noqa: E402

if __name__ == "__main__":
    main()
