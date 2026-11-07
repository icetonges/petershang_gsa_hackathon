"""Propose the tool surface for a design.

Phase 3. The designer proposes only tools the profiled fields can support,
and reports what it left out and why. Edit the result with the user before
generating: the tool names are the part they will read.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from mcp_server_creator import designs
from mcp_server_creator.designer import MAX_TOOLS, design_tools


def register(mcp: FastMCP) -> None:
    """Register the tool designer with the MCP server."""

    @mcp.tool(
        name="creator_design_tools",
        annotations={
            "title": "Design the tool surface",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def creator_design_tools(
        design_id: Annotated[
            str, Field(description=designs.DESIGN_ID_DESCRIPTION)
        ] = "",
        spec_json: Annotated[
            str, Field(description=designs.SPEC_JSON_DESCRIPTION)
        ] = "",
        drop: Annotated[
            list[str],
            Field(
                description=(
                    "Tool names to remove from the proposal, after the user "
                    "has looked at it."
                )
            ),
        ] = [],
        keep_only: Annotated[
            list[str],
            Field(
                description=(
                    "Keep only these tool names. Use this when the user has "
                    "picked a short list; it beats dropping one at a time."
                )
            ),
        ] = [],
    ) -> dict[str, Any]:
        """Propose tools the data can support, and name what it cannot.

        One tool per question shape per source, named after the user's own
        questions where they were recorded. Three to six tools beats twelve:
        a model choosing between many similar tools chooses worse.

        Returns: `tools` with each name, pattern and source; `design_notes`
        saying what was left out and why; `review_first` reminding you to
        confirm the names with the user; and the updated `design_id`.
        """
        spec, _, failure = designs.resolve_result(design_id, spec_json)
        if failure:
            return failure
        assert spec is not None

        if not spec.data_sources:
            return {
                "error": "The design has no data sources yet.",
                "hint": (
                    "Call creator_add_data_source at least once. Without a "
                    "profiled source there is nothing to design against."
                ),
            }

        plans, notes = design_tools(spec)

        wanted = {name.strip() for name in keep_only if name.strip()}
        unwanted = {name.strip() for name in drop if name.strip()}
        if wanted:
            plans = [plan for plan in plans if plan.name in wanted]
            missing = wanted - {plan.name for plan in plans}
            if missing:
                notes.append(
                    "keep_only named tools the designer did not propose: "
                    + ", ".join(sorted(missing))
                )
        if unwanted:
            plans = [plan for plan in plans if plan.name not in unwanted]

        spec.tools = plans
        designs.persist(spec, design_id)

        by_source: dict[str, list[str]] = {}
        for plan in plans:
            by_source.setdefault(plan.source, []).append(plan.name)

        return {
            "design_id": design_id or "",
            "tools": [
                {
                    "name": plan.name,
                    "title": plan.title,
                    "goal": plan.goal,
                    "pattern": plan.pattern.value,
                    "source": plan.source,
                    "parameters": [param.name for param in plan.params],
                    "paginated": plan.paginated,
                }
                for plan in plans
            ],
            "tool_count": len(plans),
            "by_source": by_source,
            "design_notes": notes,
            "review_first": (
                "Read each tool name aloud to the person whose job it is. If "
                "they would not recognize it as something they do, rename "
                f"it. Drop anything they do not need: {MAX_TOOLS} tools is "
                "the ceiling for a focused server."
            ),
            "spec": spec.model_dump(mode="json") if not design_id else None,
            "next_step": (
                "Confirm the tool list with the user, then call "
                "creator_review_server, then creator_generate_project."
            ),
        }
