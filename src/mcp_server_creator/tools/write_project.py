"""Write a generated project to the local filesystem.

The only tool here that is not read-only, so it is the only one with a
guard. Writing is off unless an operator both sets `CREATOR_ALLOW_WRITE` and
names a `CREATOR_OUTPUT_ROOT`, every path is resolved inside that root, and
the default is a dry run that reports what would be written.

On Databricks Apps there is no durable local filesystem worth writing to, so
leave writing off there and let the client write the files instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from mcp_server_creator import designs
from mcp_server_creator.config import settings
from mcp_server_creator.generators import generate_full_project
from mcp_server_creator.utils import UnsafePathError, safe_output_path, slugify


def register(mcp: FastMCP) -> None:
    """Register the project writer with the MCP server."""

    @mcp.tool(
        name="creator_write_project",
        annotations={
            "title": "Write the generated project to disk",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def creator_write_project(
        design_id: Annotated[
            str, Field(description=designs.DESIGN_ID_DESCRIPTION)
        ] = "",
        spec_json: Annotated[
            str, Field(description=designs.SPEC_JSON_DESCRIPTION)
        ] = "",
        dry_run: Annotated[
            bool,
            Field(
                description=(
                    "True reports what would be written and changes nothing. "
                    "Set it to false only after reviewing that list."
                )
            ),
        ] = True,
        subdirectory: Annotated[
            str,
            Field(
                description=(
                    "Folder under the configured output root. Defaults to "
                    "the server name."
                )
            ),
        ] = "",
        overwrite: Annotated[
            bool,
            Field(
                description=(
                    "Allow replacing files that already exist. False stops "
                    "at the first collision and writes nothing."
                )
            ),
        ] = False,
        include_evaluation: Annotated[
            bool, Field(description="Include the evaluation suite.")
        ] = True,
        include_deployment: Annotated[
            bool, Field(description="Include the Databricks deployment kit.")
        ] = True,
    ) -> dict[str, Any]:
        """Write every generated file under the configured output root.

        Returns: `written` or `would_write` with each path, `root`,
        `collisions` when any file exists and overwrite is false, and
        `warnings` from generation. An `error` with a `hint` when writing is
        not enabled on this server.
        """
        spec, _, failure = designs.resolve_result(design_id, spec_json)
        if failure:
            return failure
        assert spec is not None

        if not settings.write_enabled:
            return {
                "error": "Writing to disk is not enabled on this server.",
                "hint": (
                    "An operator must set CREATOR_ALLOW_WRITE=true and "
                    "CREATOR_OUTPUT_ROOT to a directory. Until then, call "
                    "creator_generate_project and write the files with your "
                    "own file tools."
                ),
                "write_enabled": False,
            }

        folder = slugify(subdirectory or spec.server_name)
        manifest = generate_full_project(
            spec,
            include_evaluation=include_evaluation,
            include_deployment=include_deployment,
        )

        try:
            root = safe_output_path(settings.creator_output_root, folder)
        except UnsafePathError as error:
            return {
                "error": str(error),
                "hint": "Use a simple folder name with no slashes or dots.",
            }

        planned: list[tuple[Path, str, bool]] = []
        collisions: list[str] = []
        for generated in sorted(manifest.files, key=lambda f: f.path):
            try:
                destination = safe_output_path(root, generated.path)
            except UnsafePathError as error:
                return {
                    "error": f"Refusing to write {generated.path!r}: {error}",
                    "hint": "This is a generator bug; report the path above.",
                }
            if destination.exists() and not overwrite:
                collisions.append(generated.path)
            planned.append((destination, generated.content, generated.executable))

        if collisions and not overwrite:
            return {
                "error": f"{len(collisions)} file(s) already exist.",
                "collisions": collisions[:20],
                "root": str(root),
                "hint": (
                    "Pass overwrite=true to replace them, or choose another "
                    "subdirectory."
                ),
                "wrote_nothing": True,
            }

        relative = [str(path.relative_to(root)) for path, _, _ in planned]
        if dry_run:
            return {
                "dry_run": True,
                "root": str(root),
                "would_write": relative,
                "file_count": len(relative),
                "total_bytes": manifest.total_bytes,
                "warnings": manifest.warnings,
                "next_step": ("Review that list, then call again with dry_run=false."),
            }

        written: list[str] = []
        for destination, content, executable in planned:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            if executable:
                destination.chmod(0o755)
            written.append(str(destination.relative_to(root)))

        return {
            "dry_run": False,
            "root": str(root),
            "written": written,
            "file_count": len(written),
            "total_bytes": manifest.total_bytes,
            "warnings": manifest.warnings,
            "next_steps": manifest.next_steps,
        }
