"""Expose the skill and knowledge library as readable resources.

Resources cost no tool call, so a client that surfaces them well can read a
skill directly. Many clients do not, which is why the same content is also
reachable through `creator_load_document`.
"""

from __future__ import annotations

import json

from fastmcp import FastMCP

from mcp_server_creator import __version__, library


def register(mcp: FastMCP) -> None:
    """Register the library resources with the MCP server."""

    @mcp.resource("creator://library/index")
    def library_index() -> str:
        """Every skill and knowledge document, with token costs."""
        return json.dumps(
            {
                "skills": library.list_skills(),
                "knowledge": library.list_knowledge(),
                "start_with": "mcp-server-creator",
            },
            indent=2,
        )

    @mcp.resource("creator://skills/{name}")
    def skill(name: str) -> str:
        """One skill's full instructions."""
        try:
            return library.load_skill(name).body
        except library.LibraryError as error:
            return str(error)

    @mcp.resource("creator://knowledge/{slug}")
    def knowledge(slug: str) -> str:
        """One reference document."""
        try:
            return library.load_knowledge(slug)
        except library.LibraryError as error:
            return str(error)

    @mcp.resource("creator://info/server")
    def server_info() -> str:
        """What this server is for and how to drive it."""
        return json.dumps(
            {
                "name": "MCP Server Creator",
                "version": __version__,
                "purpose": (
                    "Turns a person's intent and their own structured or "
                    "unstructured data into a deployable, evaluated MCP "
                    "server on Databricks."
                ),
                "sequence": [
                    "creator_list_library",
                    "creator_load_document('mcp-server-creator')",
                    "creator_start_design",
                    "creator_profile_data_source (once per source)",
                    "creator_design_tools",
                    "creator_review_server",
                    "creator_generate_project",
                    "creator_write_project (optional, if enabled)",
                ],
                "state": (
                    "The ServerSpec is the state. MCP 2026-07-28 removes "
                    "sessions, so pass it back on every call."
                ),
            },
            indent=2,
        )
