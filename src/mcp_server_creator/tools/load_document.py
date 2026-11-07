"""Load one skill, knowledge document, or skill reference file.

Progressive disclosure: the listing is cheap, a skill body is a few thousand
tokens, and a reference file is loaded only when a phase needs it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from mcp_server_creator import library


def register(mcp: FastMCP) -> None:
    """Register the document loading tool with the MCP server."""

    @mcp.tool(
        name="creator_load_document",
        annotations={
            "title": "Load a skill or reference document",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def creator_load_document(
        name: Annotated[
            str,
            Field(
                description=(
                    "A skill name such as 'mcp-tool-design', or a knowledge "
                    "slug such as 'databricks-mcp'. Call "
                    "creator_list_library for the exact names."
                )
            ),
        ],
        reference: Annotated[
            str,
            Field(
                description=(
                    "A reference file belonging to that skill, as the "
                    "listing spells it, e.g. 'references/examples.md'. "
                    "Leave empty for the skill body itself."
                )
            ),
        ] = "",
    ) -> dict[str, Any]:
        """Return the full text of one skill or reference document.

        Follow a skill's instructions as a procedure. Treat a knowledge
        document as reference material to consult.

        Returns: `kind` ('skill' or 'knowledge'), `name`, `description`,
        `content`, and for a skill its `references` index. An `error` with a
        `hint` and the available names when nothing matches.
        """
        try:
            skill = library.load_skill(name)
        except library.LibraryError:
            skill = None

        if skill is not None:
            if reference.strip():
                try:
                    content = library.load_skill_reference(name, reference.strip())
                except library.LibraryError as error:
                    return {
                        "error": str(error),
                        "hint": "Use a path from the skill's references list.",
                    }
                return {
                    "kind": "skill_reference",
                    "name": skill.name,
                    "reference": reference.strip(),
                    "content": content,
                }
            return {
                "kind": "skill",
                "name": skill.name,
                "description": skill.description,
                "references": skill.references,
                "content": skill.body,
            }

        try:
            content = library.load_knowledge(name)
        except library.LibraryError as error:
            return {
                "error": str(error),
                "hint": (
                    "Call creator_list_library for the exact skill names and "
                    "knowledge slugs."
                ),
            }
        return {"kind": "knowledge", "name": name, "content": content}
