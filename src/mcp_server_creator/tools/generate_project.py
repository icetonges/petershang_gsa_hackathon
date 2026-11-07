"""Generate the complete project for a design.

Phase 4. The manifest index comes back by default rather than every file
body, because a full project runs to hundreds of kilobytes and would swamp
the conversation. Ask for specific file contents by path, or write the whole
thing to disk with creator_write_project.

Generation is deterministic: the same specification produces the same files.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from mcp_server_creator import designs
from mcp_server_creator.config import settings
from mcp_server_creator.generators import generate_full_project
from mcp_server_creator.utils import truncate

MAX_CONTENT_FILES = 10


def register(mcp: FastMCP) -> None:
    """Register the project generator with the MCP server."""

    @mcp.tool(
        name="creator_generate_project",
        annotations={
            "title": "Generate the server project",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def creator_generate_project(
        design_id: Annotated[
            str, Field(description=designs.DESIGN_ID_DESCRIPTION)
        ] = "",
        spec_json: Annotated[
            str, Field(description=designs.SPEC_JSON_DESCRIPTION)
        ] = "",
        include_evaluation: Annotated[
            bool,
            Field(
                description=(
                    "Include the evaluation suite: harness, golden set and "
                    "independent answer key."
                )
            ),
        ] = True,
        include_deployment: Annotated[
            bool,
            Field(
                description=(
                    "Include app.yaml, databricks.yml, the deploy and smoke "
                    "scripts, and the grants file."
                )
            ),
        ] = True,
        include_contents: Annotated[
            list[str],
            Field(
                description=(
                    "Paths from the index whose full text you want back, at "
                    "most ten. Leave empty to get the index only."
                )
            ),
        ] = [],
    ) -> dict[str, Any]:
        """Generate the whole project and return its file index.

        Review the index first, then ask for the files that matter: usually
        a tool module and the deployment config. Warnings name every place
        the generator had to guess or emit a stub, so read them before you
        read the code.

        Returns: `index` with path, purpose, lines and bytes per file;
        `warnings`; `next_steps`; `totals`; and `contents` for any paths you
        asked for.
        """
        spec, _, failure = designs.resolve_result(design_id, spec_json)
        if failure:
            return failure
        assert spec is not None

        if not spec.tools:
            return {
                "error": "The design has no tools yet.",
                "hint": (
                    "Call creator_design_tools first, and confirm the names "
                    "with the user. Generating a server with no tools "
                    "produces nothing anyone can use."
                ),
            }

        manifest = generate_full_project(
            spec,
            include_evaluation=include_evaluation,
            include_deployment=include_deployment,
        )

        if manifest.total_bytes > settings.creator_max_manifest_bytes:
            return {
                "error": (
                    f"The generated project is {manifest.total_bytes} bytes, "
                    f"over the {settings.creator_max_manifest_bytes} byte "
                    "limit."
                ),
                "hint": (
                    "Reduce the tool count, or generate the server and the "
                    "evaluation in separate calls."
                ),
                "index": manifest.index(),
            }

        requested = [path for path in include_contents if path.strip()]
        contents: dict[str, str] = {}
        unknown: list[str] = []
        by_path = {generated.path: generated for generated in manifest.files}
        for path in requested[:MAX_CONTENT_FILES]:
            generated = by_path.get(path.strip())
            if generated is None:
                unknown.append(path)
                continue
            contents[path.strip()] = truncate(generated.content, 60_000)

        result: dict[str, Any] = {
            "design_id": design_id or "",
            "server_name": manifest.server_name,
            "package_name": manifest.package_name,
            "index": manifest.index(),
            "totals": {
                "file_count": len(manifest.files),
                "total_bytes": manifest.total_bytes,
            },
            "warnings": manifest.warnings,
            "next_steps": manifest.next_steps,
        }
        if contents:
            result["contents"] = contents
        if unknown:
            result["unknown_paths"] = unknown
            result["hint"] = (
                "Those paths are not in the index. Copy a path exactly as "
                "the index spells it."
            )
        if len(requested) > MAX_CONTENT_FILES:
            result["note"] = (
                f"Only the first {MAX_CONTENT_FILES} requested files were "
                "returned. Ask for the rest in another call, or write the "
                "project to disk."
            )
        return result
