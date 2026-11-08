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


def _match_path(requested: str, paths: list[str]) -> tuple[str | None, list[str]]:
    """Exact path, else a unique path ending in it, else one sharing its name.

    Clients often ask for 'app.yaml' or 'tools/search.py' without the
    package prefix; refusing those wastes a round trip.
    """
    wanted = requested.strip().lstrip("./")
    if wanted in paths:
        return wanted, []
    suffix = [p for p in paths if p.endswith("/" + wanted)]
    if len(suffix) == 1:
        return suffix[0], []
    if suffix:
        return None, sorted(suffix)
    name = wanted.rsplit("/", 1)[-1]
    same_name = [p for p in paths if p.rsplit("/", 1)[-1] == name]
    if len(same_name) == 1:
        return same_name[0], []
    stem = name.rsplit(".", 1)[0].lower()
    similar = [p for p in paths if stem and stem in p.lower()]
    return None, sorted(same_name or similar)


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
        resolved_as: dict[str, str] = {}
        did_you_mean: dict[str, list[str]] = {}
        for path in requested[:MAX_CONTENT_FILES]:
            match, candidates = _match_path(path, list(by_path))
            if match is None:
                unknown.append(path)
                if candidates:
                    did_you_mean[path] = candidates[:5]
                continue
            if match != path.strip():
                resolved_as[path] = match
            contents[match] = truncate(by_path[match].content, 60_000)

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
                "the index spells it"
                + (", or pick one from did_you_mean." if did_you_mean else ".")
            )
        if did_you_mean:
            result["did_you_mean"] = did_you_mean
        if resolved_as:
            result["resolved_paths"] = resolved_as
        if len(requested) > MAX_CONTENT_FILES:
            result["note"] = (
                f"Only the first {MAX_CONTENT_FILES} requested files were "
                "returned. Ask for the rest in another call, or write the "
                "project to disk."
            )
        return result
