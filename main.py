"""Root entry point for local development.

Run locally:
    uv run python main.py

Or via the installed script:
    uv run mcp-server-creator
"""

from mcp_server_creator.app import main

if __name__ == "__main__":
    main()
