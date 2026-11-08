"""Tools submodule: one tool per file.

Each module exposes `register(mcp)`. This file aggregates them into
`register_tools(mcp)`, which app.py calls once at startup.

Nine tools, following the build sequence: list and load the library, check
the environment, start a design, add each source, design the tools, review
the design, generate the project, and optionally write it to disk.

Nine is one more than the ceiling this server's own rubric sets for a
focused domain server, and deliberately so: a toolkit that spans six phases
needs one tool per phase. The rubric's advice still holds for the servers it
generates, which answer questions rather than run a workflow.
"""

from __future__ import annotations

from mcp_server_creator.tools import (
    add_data_source,
    check_environment,
    deploy_server,
    design_tools,
    generate_project,
    list_library,
    load_document,
    review_server,
    start_design,
    write_project,
)


def register_tools(mcp) -> None:
    """Register all tools with the MCP server."""
    list_library.register(mcp)
    load_document.register(mcp)
    check_environment.register(mcp)
    start_design.register(mcp)
    add_data_source.register(mcp)
    design_tools.register(mcp)
    review_server.register(mcp)
    generate_project.register(mcp)
    write_project.register(mcp)
    deploy_server.register(mcp)


__all__ = ["register_tools"]
