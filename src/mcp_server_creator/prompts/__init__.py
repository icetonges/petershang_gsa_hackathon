"""Prompts submodule: the guided workflows a client shows as commands."""

from __future__ import annotations

from mcp_server_creator.prompts import workflows


def register_prompts(mcp) -> None:
    """Register all prompts with the MCP server."""
    workflows.register(mcp)


__all__ = ["register_prompts"]
