"""Resources submodule: the library, readable without a tool call."""

from __future__ import annotations

from mcp_server_creator.resources import library_resources


def register_resources(mcp) -> None:
    """Register all resources with the MCP server."""
    library_resources.register(mcp)


__all__ = ["register_resources"]
