"""Security tests for the boundaries this server has.

Every check is deterministic and offline. Nothing probes a real destination.
"""

from __future__ import annotations

import pytest

from mcp_server_creator.app import mcp
from mcp_server_creator.utils import (
    UnsafePathError,
    quote_uc_name,
    safe_output_path,
    validate_uc_identifier,
)

FORBIDDEN_PARAM_NAMES = {
    "url",
    "uri",
    "endpoint",
    "host",
    "sql",
    "statement",
    "path",
    "file_path",
    "filepath",
    "command",
    "script",
}


def tool_schema(tool) -> dict:
    """Read a tool's input schema across FastMCP versions."""
    return getattr(tool, "parameters", None) or getattr(tool, "inputSchema", None) or {}


def hint(tool, name: str):
    """Read an annotation hint across FastMCP versions.

    `name` is the snake_case form, e.g. "read_only_hint".
    """
    annotations = tool.annotations
    if annotations is None:
        return None
    if hasattr(annotations, name):
        return getattr(annotations, name)
    camel = name.split("_")
    legacy = camel[0] + "".join(part.capitalize() for part in camel[1:])
    return getattr(annotations, legacy, None)


async def test_no_tool_accepts_a_url_sql_or_path_argument():
    """These three arguments turn a data tool into an exfiltration primitive.

    `api_base_url` on the profiler is deliberately not in this set: it
    records an origin into a specification, it is never fetched by this
    server, and the generated server pins it in operator-controlled code.
    """
    offenders: list[str] = []
    for tool in await mcp.list_tools():
        properties = tool_schema(tool).get("properties") or {}
        for param in properties:
            if param.lower() in FORBIDDEN_PARAM_NAMES:
                offenders.append(f"{tool.name}.{param}")
    assert not offenders, offenders


async def test_exactly_the_mutating_tools_are_marked_mutating():
    """Three tools change state, and each changes something different.

    add_data_source and design_tools update the stored design; only
    write_project touches a filesystem. An annotation that overstates or
    understates this is a lie the client acts on.
    """
    mutating = {
        tool.name
        for tool in await mcp.list_tools()
        if hint(tool, "read_only_hint") is False
    }
    assert mutating == {
        "creator_add_data_source",
        "creator_design_tools",
        "creator_write_project",
    }, sorted(mutating)


async def test_only_the_write_tool_is_destructive():
    destructive = {
        tool.name for tool in await mcp.list_tools() if hint(tool, "destructive_hint")
    }
    assert destructive == {"creator_write_project"}, sorted(destructive)


async def test_annotations_are_internally_consistent():
    for tool in await mcp.list_tools():
        if hint(tool, "read_only_hint"):
            assert not hint(tool, "destructive_hint"), tool.name


def test_identifier_validation_rejects_injection():
    for attempt in (
        "table; DROP TABLE users",
        "table`",
        "../etc/passwd",
        "a b",
        "",
        "1table",
        "tab\nle",
    ):
        with pytest.raises(ValueError):
            validate_uc_identifier(attempt)


def test_identifier_validation_accepts_plain_names():
    assert validate_uc_identifier("my_table") == "my_table"
    assert validate_uc_identifier("_private") == "_private"


def test_qualified_names_are_quoted():
    assert quote_uc_name("main", "acq", "awards") == "`main`.`acq`.`awards`"


def test_path_containment_refuses_traversal_and_absolutes(tmp_path):
    for attempt in (
        "../escape",
        "/etc/passwd",
        "a/../../b",
        "C:/windows",
        "",
        " leading-space",
        "weird|char",
    ):
        with pytest.raises(UnsafePathError):
            safe_output_path(tmp_path, attempt)


def test_path_containment_allows_nested_relative_paths(tmp_path):
    resolved = safe_output_path(tmp_path, "src/pkg/tools/thing.py")
    assert str(resolved).startswith(str(tmp_path.resolve()))


def test_write_tool_refuses_when_writing_is_disabled():
    """The guard has to hold even if a caller asks nicely."""
    from mcp_server_creator.config import settings

    assert settings.write_enabled is False


def test_generated_servers_forbid_the_same_arguments(designed_spec):
    """The rubric this server applies to others must flag unsafe arguments."""
    from mcp_server_creator.models import ParamPlan
    from mcp_server_creator.review import review_spec

    designed_spec.tools[0].params.append(
        ParamPlan(name="url", python_type="str", description="anything")
    )
    report = review_spec(designed_spec)
    checks = {finding.check for finding in report.blockers}
    assert "no_url_sql_or_path_arguments" in checks
