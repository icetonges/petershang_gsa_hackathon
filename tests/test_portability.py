"""Portability: can any model, through any gateway, drive this server?

MCP is model-agnostic by protocol, but a server can still be unusable by a
whole class of models. The first version of this server was: its tool
listing was 48 KB of schema, about 12,900 tokens on every request, with four
tools nested 13 levels deep, and after the design phase the model had to
re-emit a 17 KB specification on every call.

Both failures are documented, not theoretical. MLflow's AI Gateway forwards
function schemas to Gemini verbatim, Gemini's validator does not act on
`$defs`, `$ref` or nullable `anyOf` unions, and nested object parameters
come back empty with no error anywhere. Databricks' AI Gateway is
MLflow-based.

These tests keep the fix from regressing.
"""

from __future__ import annotations

import json

from mcp_server_creator.app import mcp

MAX_TOTAL_SCHEMA_BYTES = 14_000
MAX_TOOL_SCHEMA_BYTES = 4_000
MAX_SCHEMA_DEPTH = 6
RISKY_KEYWORDS = ("$ref", "$defs", "anyOf", "allOf", "oneOf")


def schema_of(tool) -> dict:
    """Read a tool's input schema across FastMCP versions."""
    return getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None) or {}


def depth_of(node, level: int = 0) -> int:
    if isinstance(node, dict):
        return max([depth_of(v, level + 1) for v in node.values()] or [level])
    if isinstance(node, list):
        return max([depth_of(v, level + 1) for v in node] or [level])
    return level


def keywords_in(node, found=None) -> set:
    found = found if found is not None else set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in RISKY_KEYWORDS:
                found.add(key)
            keywords_in(value, found)
    elif isinstance(node, list):
        for value in node:
            keywords_in(value, found)
    return found


async def test_tool_listing_stays_within_its_token_budget():
    tools = await mcp.list_tools()
    total = sum(
        len(json.dumps(schema_of(tool), separators=(",", ":"))) for tool in tools
    )
    assert total <= MAX_TOTAL_SCHEMA_BYTES, (
        f"tool schemas total {total} bytes (~{total // 4} tokens), over the "
        f"{MAX_TOTAL_SCHEMA_BYTES} byte budget"
    )


async def test_no_single_tool_schema_is_oversized():
    tools = await mcp.list_tools()
    heavy = {
        tool.name: len(json.dumps(schema_of(tool), separators=(",", ":")))
        for tool in tools
        if len(json.dumps(schema_of(tool), separators=(",", ":")))
        > MAX_TOOL_SCHEMA_BYTES
    }
    assert not heavy, f"oversized tool schemas: {heavy}"


async def test_no_tool_takes_a_nested_object_parameter():
    """This is the one that made Gemini-through-a-gateway return nothing."""
    offenders: list[str] = []
    for tool in await mcp.list_tools():
        for name, prop in (schema_of(tool).get("properties") or {}).items():
            kind = prop.get("type")
            items = prop.get("items") or {}
            if kind == "object" or (kind == "array" and items.get("type") == "object"):
                offenders.append(f"{tool.name}.{name}")
    assert not offenders, f"nested object parameters: {offenders}"


async def test_schemas_avoid_keywords_narrow_providers_reject():
    offenders: dict[str, list[str]] = {}
    for tool in await mcp.list_tools():
        found = keywords_in(schema_of(tool))
        if found:
            offenders[tool.name] = sorted(found)
    assert not offenders, f"risky schema keywords: {offenders}"


async def test_schemas_are_shallow():
    deep = {
        tool.name: depth_of(schema_of(tool))
        for tool in await mcp.list_tools()
        if depth_of(schema_of(tool)) > MAX_SCHEMA_DEPTH
    }
    assert not deep, f"schemas nested deeper than {MAX_SCHEMA_DEPTH}: {deep}"


async def test_design_state_travels_as_a_short_handle():
    """The spec is ~17 KB. A handle is 12 characters."""
    from mcp_server_creator import designs
    from mcp_server_creator.models import ServerSpec

    handle = designs.save(ServerSpec(server_name="portable-mcp", purpose="x"))
    assert len(handle) <= 16
    assert designs.load(handle).server_name == "portable-mcp"


async def test_every_design_tool_accepts_both_a_handle_and_inline_json():
    """A stateless deployment has no store, so inline JSON must still work."""
    tools = {tool.name: schema_of(tool) for tool in await mcp.list_tools()}
    design_consuming = [
        "creator_add_data_source",
        "creator_design_tools",
        "creator_review_server",
        "creator_generate_project",
        "creator_write_project",
        "creator_deploy_server",
    ]
    for name in design_consuming:
        properties = (tools[name].get("properties") or {}).keys()
        assert "design_id" in properties, name
        assert "spec_json" in properties, name
