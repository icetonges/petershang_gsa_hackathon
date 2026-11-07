"""The bundled skill and knowledge library loads and is well formed."""

from __future__ import annotations

import pytest

from mcp_server_creator import library

EXPECTED_SKILLS = {
    "mcp-server-creator",
    "mcp-tool-design",
    "structured-data-mcp",
    "unstructured-data-mcp",
    "databricks-mcp-deployment",
    "mcp-evaluation-harness",
    "mcp-security-review",
    "mcp-portability",
}

EXPECTED_KNOWLEDGE = {
    "databricks-mcp",
    "mcp-design-rules",
    "data-patterns",
    "judging-rubric",
    "environment-portability",
}


def test_every_skill_is_present():
    assert EXPECTED_SKILLS <= set(library.skill_names())


def test_every_knowledge_document_is_present():
    slugs = {entry["slug"] for entry in library.list_knowledge()}
    assert EXPECTED_KNOWLEDGE <= slugs


def test_every_skill_has_a_description():
    for entry in library.list_skills():
        assert entry["description"], entry["name"]


def test_skill_bodies_stay_within_the_recommended_budget():
    """The spec recommends keeping an activated skill body under ~5k tokens."""
    oversized = [
        (skill["name"], skill["approx_tokens"])
        for skill in library.list_skills()
        if skill["approx_tokens"] > 5000
    ]
    assert not oversized, oversized


def test_loading_a_skill_returns_its_body():
    skill = library.load_skill("mcp-tool-design")
    assert "## Parameters" in skill.body


def test_loading_a_skill_is_case_insensitive():
    assert library.load_skill("MCP-Tool-Design").name == "mcp-tool-design"


def test_unknown_skill_raises_with_the_available_names():
    with pytest.raises(library.LibraryError) as error:
        library.load_skill("does-not-exist")
    assert "Available" in str(error.value)


def test_unknown_knowledge_document_raises():
    with pytest.raises(library.LibraryError):
        library.load_knowledge("not-a-document")


def test_knowledge_documents_load():
    assert "mcp-" in library.load_knowledge("databricks-mcp")


def test_frontmatter_parsing_handles_wrapped_values():
    meta, body = library.parse_frontmatter(
        "---\nname: x\ndescription: one\n  two\n---\nbody\n"
    )
    assert meta["name"] == "x"
    assert meta["description"] == "one two"
    assert body.strip() == "body"
