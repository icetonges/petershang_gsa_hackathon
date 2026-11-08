"""List the bundled skill and knowledge library.

The entry point for a client that has just connected: it shows the whole
methodology surface in one cheap call, so the model can decide what to load
rather than guessing.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from mcp_server_creator import library


def register(mcp: FastMCP) -> None:
    """Register the library listing tool with the MCP server."""

    @mcp.tool(
        name="creator_list_library",
        annotations={
            "title": "List the skill and knowledge library",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def creator_list_library() -> dict[str, Any]:
        """Show every skill and reference document this server can hand you.

        Start here. Skills are procedures to follow; knowledge documents are
        reference material to consult. Each entry reports its approximate
        token cost so you can decide what to load.

        Returns: `skills` and `knowledge`, each a list of entries with a name
        or slug, a description, and `approx_tokens`; plus `start_with`, the
        skill to read first, and `phases`, the build sequence in order.
        """
        skills = library.list_skills()
        return {
            "skills": skills,
            "knowledge": library.list_knowledge(),
            "start_with": "mcp-a-creator",
            "phases": [
                "1. discovery: creator_start_design",
                "2. data profiling: creator_profile_data_source",
                "3. tool design: creator_design_tools",
                "4. generation: creator_generate_project",
                "5. evaluation: included in generation; see the eval skill",
                "6. deployment and review: creator_review_server",
            ],
            "note": (
                "Load one skill at a time with creator_load_document. The "
                "entry skill routes to the others as each phase needs them."
            ),
        }
