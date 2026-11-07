"""HTTP-only routes alongside the /mcp endpoint.

`/health` matters for deployment: Databricks Apps uses an HTTP health check
to decide whether the app started. Keep it fast and dependency-free so it
answers even when nothing else is reachable.
"""

from __future__ import annotations

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_server_creator import __version__, library


def register_routes(mcp: FastMCP) -> None:
    """Register custom HTTP routes with the server."""

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy", "service": "mcp-server-creator"})

    @mcp.custom_route("/version", methods=["GET"])
    async def version(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "version": __version__,
                "skills": len(library.skill_names()),
            }
        )
