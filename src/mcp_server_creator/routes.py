"""HTTP-only routes alongside the /mcp endpoint.

`/health` matters for deployment: Databricks Apps uses an HTTP health check
to decide whether the app started. Keep it fast and dependency-free so it
answers even when nothing else is reachable.
"""

from __future__ import annotations

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_server_creator import __version__, examples, library


def register_routes(mcp: FastMCP) -> None:
    """Register custom HTTP routes with the server."""

    @mcp.custom_route("/", methods=["GET"])
    async def root(request: Request) -> JSONResponse:
        # Landing page for people who open the app URL in a browser.
        # MCP clients use /mcp; this only reports status.
        return JSONResponse(
            {
                "service": "mcp-a-creator",
                "name": "MCP A Creator",
                "description": (
                    "Build your own MCP server from your data and a task you "
                    "do by hand. Tell it who the user is, 3-8 questions they "
                    "ask, the exact table or volume, and what to refuse."
                ),
                "how_to_use": [
                    "In Genie Code, open Settings > Connectors > Custom MCP Server.",
                    "Select mcp-a-creator and save; its creator_* tools "
                    "become available.",
                    "Ask Genie Code to 'Use MCP A Creator to build...', "
                    "naming the user, their questions, and a table that "
                    "exists. The examples use the samples catalog, which "
                    "every workspace has.",
                ],
                "try": examples.prompts()[0],
                "examples": [
                    {"for": e["persona"], "prompt": e["prompt"]}
                    for e in examples.EXAMPLES
                ],
                "status": "running",
                "version": __version__,
                "transport": "streamable-http (stateless)",
                "mcp_endpoint": "/mcp",
                "endpoints": {"health": "/health", "version": "/version"},
                "skills": len(library.skill_names()),
            }
        )

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy", "service": "mcp-a-creator"})

    @mcp.custom_route("/version", methods=["GET"])
    async def version(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "version": __version__,
                "skills": len(library.skill_names()),
            }
        )
