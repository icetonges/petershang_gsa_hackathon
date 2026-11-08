"""Write a generated project to the local filesystem.

The only tool here that is not read-only, so it is the only one with a
guard. Writing is off unless an operator both sets `CREATOR_ALLOW_WRITE` and
names a `CREATOR_OUTPUT_ROOT`, every path is resolved inside that root, and
the default is a dry run that reports what would be written.

On Databricks Apps there is no durable local filesystem worth writing to.
Point CREATOR_OUTPUT_ROOT at a Unity Catalog volume directory instead
(/Volumes/<catalog>/<schema>/<volume>/projects) and the project is written
there through the Files API, where the user can open or download it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from mcp_server_creator import designs, live, user_workspace, volume_files
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
        destination: Annotated[
            Literal["auto", "workspace", "volume", "local"],
            Field(
                description=(
                    "Where to write. 'workspace' is your own home folder, "
                    "/Workspace/Users/<you>/mcp-servers/<server-name>, written "
                    "as you so you own and can redeploy it. 'auto' picks "
                    "'workspace' whenever you are signed in."
                )
            ),
        ] = "auto",
        folder: Annotated[
            str,
            Field(
                description=(
                    "For destination 'workspace': a folder inside your home "
                    "folder. Defaults to mcp-servers/<server-name>."
                )
            ),
        ] = "",
        create_folder: Annotated[
            bool,
            Field(
                description=(
                    "For destination 'workspace': create the folder when it "
                    "does not exist. Ask the user first; the default answer "
                    "is yes."
                )
            ),
        ] = True,
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

        if destination == "auto":
            destination = "workspace" if live.caller_token() else ""
        if destination == "workspace":
            manifest = generate_full_project(
                spec,
                include_evaluation=include_evaluation,
                include_deployment=include_deployment,
            )
            return _write_to_user_workspace(
                spec, manifest, folder, dry_run, overwrite, create_folder
            )
        if destination == "volume" and not volume_files.is_volume_path(
            settings.creator_output_root
        ):
            return {
                "error": "No volume output root is configured on this server.",
                "hint": "Use destination='workspace' to write to your home folder.",
            }

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

        if volume_files.is_volume_path(settings.creator_output_root):
            return _write_to_volume(manifest, folder, dry_run, overwrite)

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


def _write_to_user_workspace(
    spec: Any,
    manifest: Any,
    folder: str,
    dry_run: bool,
    overwrite: bool,
    create_folder: bool,
) -> dict[str, Any]:
    """Write into the caller's home folder, as the caller, after approval."""
    try:
        if folder.strip():
            relative = folder.strip().strip("/")
            if relative.startswith("Workspace/"):
                root = "/" + relative
            else:
                home = f"/Workspace/Users/{user_workspace.caller_email()}"
                root = f"{home}/{relative}"
        else:
            root = user_workspace.default_folder(spec.server_name)
        user_workspace.check_folder(root)
        existing = user_workspace.folder_entries(root)
    except user_workspace.UserWorkspaceError as error:
        return {"error": str(error), "wrote_nothing": True}

    files = sorted(manifest.files, key=lambda f: f.path)
    if any(".." in f.path.split("/") for f in files):
        return {"error": "Refusing a generated path with '..'.", "wrote_nothing": True}
    paths = [f.path for f in files]
    folder_exists = existing is not None
    base: dict[str, Any] = {
        "destination": "workspace",
        "root": root,
        "folder_exists": folder_exists,
        "file_count": len(paths),
        "total_bytes": manifest.total_bytes,
        "warnings": manifest.warnings,
    }

    if existing and not overwrite:
        return {
            **base,
            "error": f"{root} already has content.",
            "collisions": existing[:20],
            "hint": "Pass overwrite=true to replace it, or choose another folder.",
            "wrote_nothing": True,
        }

    if dry_run:
        result = {**base, "dry_run": True, "would_write": paths}
        if not folder_exists:
            result["approval"] = {
                "ask_user": (
                    f"Create the folder {root} to hold this MCP server, so you "
                    "can reuse and redeploy it? (default: yes)"
                ),
                "default": "yes",
                "if_yes": "Call again with dry_run=false.",
                "if_no": "Pass another folder, or keep the files from "
                "creator_generate_project instead.",
            }
        else:
            result["next_step"] = "Review the list, then call again with dry_run=false."
        return result

    if not folder_exists and not create_folder:
        return {
            **base,
            "error": f"{root} does not exist and create_folder is false.",
            "wrote_nothing": True,
        }

    try:
        written = user_workspace.write_files(root, [(f.path, f.content) for f in files])
    except user_workspace.UserWorkspaceError as error:
        return {
            **base,
            "error": str(error),
            "hint": "Call again with overwrite=true once the cause is fixed.",
        }
    return {
        **base,
        "dry_run": False,
        "folder_created": not folder_exists,
        "written": written,
        "file_count": len(written),
        "next_step": (
            "To make it a live MCP server, call creator_deploy_server with "
            f"the same design and source_path={root!r}. It runs a security "
            "review and asks before deploying."
        ),
    }


def _write_to_volume(
    manifest: Any, folder: str, dry_run: bool, overwrite: bool
) -> dict[str, Any]:
    """Write a manifest under a volume directory through the Files API.

    The collision check is per project folder rather than per file: one
    listing call instead of one per file, and it errs on the safe side.
    """
    try:
        root = volume_files.join(settings.creator_output_root, folder)
        planned = [
            (volume_files.join(root, generated.path), generated)
            for generated in sorted(manifest.files, key=lambda f: f.path)
        ]
    except volume_files.VolumeError as error:
        return {
            "error": f"Refusing to write: {error}",
            "hint": "Use a simple folder name with no slashes or dots.",
        }

    relative = [generated.path for _, generated in planned]
    try:
        existing = volume_files.list_names(root) or volume_files.list_dirs(root)
    except volume_files.VolumeError as error:
        return {"error": str(error), "root": root, "wrote_nothing": True}
    if existing and not overwrite:
        return {
            "error": f"The folder {root} already has content.",
            "collisions": existing[:20],
            "root": root,
            "hint": (
                "Pass overwrite=true to replace it, or choose another subdirectory."
            ),
            "wrote_nothing": True,
        }

    if dry_run:
        return {
            "dry_run": True,
            "root": root,
            "would_write": relative,
            "file_count": len(relative),
            "total_bytes": manifest.total_bytes,
            "warnings": manifest.warnings,
            "next_step": "Review that list, then call again with dry_run=false.",
        }

    written: list[str] = []
    for path, generated in planned:
        try:
            volume_files.write_text(path, generated.content)
        except volume_files.VolumeError as error:
            return {
                "error": str(error),
                "root": root,
                "written": written,
                "hint": "Fix the grant and call again with overwrite=true.",
            }
        written.append(generated.path)

    return {
        "dry_run": False,
        "root": root,
        "written": written,
        "file_count": len(written),
        "total_bytes": manifest.total_bytes,
        "warnings": manifest.warnings,
        "where_to_find_it": (
            f"Catalog Explorer, then the volume, then {folder}/. Download it, "
            "or copy it into a Git folder to deploy. Executable bits are not "
            "kept in a volume; run `chmod +x` on scripts after copying."
        ),
        "next_steps": manifest.next_steps,
    }
