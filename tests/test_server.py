"""Smoke tests: the server imports and every submodule registers."""

from __future__ import annotations

from fastmcp import FastMCP

from mcp_server_creator.app import mcp
from mcp_server_creator.config import settings

EXPECTED_TOOLS = {
    "creator_list_library",
    "creator_load_document",
    "creator_check_environment",
    "creator_start_design",
    "creator_add_data_source",
    "creator_design_tools",
    "creator_review_server",
    "creator_generate_project",
    "creator_write_project",
}


def test_server_is_importable():
    assert mcp is not None


def test_server_has_name():
    assert mcp.name == "MCP Server Creator"


def test_server_instructions_warn_about_untrusted_input():
    """The injection rule has to be where the model actually reads it."""
    instructions = (mcp.instructions or "").lower()
    assert "never instructions" in instructions


def test_default_transport_is_stdio():
    assert settings.mcp_transport == "stdio"


def test_writing_is_disabled_by_default():
    """A side effect should need an operator to turn it on."""
    assert settings.write_enabled is False


def test_tools_register_without_error():
    from mcp_server_creator.tools import register_tools

    register_tools(FastMCP("test"))


def test_prompts_register_without_error():
    from mcp_server_creator.prompts import register_prompts

    register_prompts(FastMCP("test"))


def test_resources_register_without_error():
    from mcp_server_creator.resources import register_resources

    register_resources(FastMCP("test"))


def test_routes_register_without_error():
    from mcp_server_creator.routes import register_routes

    register_routes(FastMCP("test"))


async def test_every_tool_is_discoverable():
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert EXPECTED_TOOLS <= names, sorted(EXPECTED_TOOLS - names)


async def test_tool_count_stays_focused():
    """Nine: one per build phase, plus the two library tools.

    The rubric's ceiling of eight is for a focused domain server that
    answers questions. This is a toolkit that runs a seven-phase workflow,
    so it carries one more and says so in tools/__init__.py rather than
    pretending otherwise.
    """
    tools = await mcp.list_tools()
    assert len(tools) <= 9


async def test_every_tool_documents_itself():
    tools = await mcp.list_tools()
    undocumented = [tool.name for tool in tools if not (tool.description or "").strip()]
    assert not undocumented, undocumented


async def test_every_tool_has_a_consistent_prefix():
    tools = await mcp.list_tools()
    unprefixed = [t.name for t in tools if not t.name.startswith("creator_")]
    assert not unprefixed, unprefixed


async def test_prompts_are_discoverable():
    prompts = await mcp.list_prompts()
    names = {getattr(prompt, "name", prompt) for prompt in prompts}
    assert "create_mcp_server" in names
