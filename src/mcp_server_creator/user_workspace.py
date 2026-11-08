"""Act in the calling user's own workspace, with the user's own token.

The generated server belongs to the person who asked for it, so it is
written to their home folder and deployed as an app they own, never as
this app's service principal. Every call here uses the token Databricks
Apps forwards for the caller (scopes `workspace.workspace` and `apps`); with
no caller token, nothing happens and the caller is told why.

The client is injectable so tests never touch a real workspace.
"""

from __future__ import annotations

import base64
from typing import Any

from mcp_server_creator import live
from mcp_server_creator.config import settings
from mcp_server_creator.utils import slugify

SERVERS_FOLDER = "mcp-servers"


class UserWorkspaceError(RuntimeError):
    """An operation in the user's workspace failed, with a reason to show."""


_client: Any = None


def set_client(client: Any) -> None:
    """Inject a client (tests), or pass None to go back to the real one."""
    global _client
    _client = client


def client() -> Any:
    """A WorkspaceClient acting as the caller."""
    if _client is not None:
        return _client
    token = live.caller_token()
    if not token:
        raise UserWorkspaceError(
            "no user token reached this server, so it cannot act in your "
            "workspace. Call it through Genie Code or another client that "
            "signs in as you, and make sure the app requests the "
            "'workspace.workspace' and 'apps' user scopes."
        )
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as error:
        raise UserWorkspaceError("databricks-sdk is not installed.") from error
    host = settings.databricks_host
    if host and not host.startswith("http"):
        host = f"https://{host}"
    return WorkspaceClient(host=host, token=token, auth_type="pat")


def caller_email() -> str:
    """The caller's user name, from the forwarded header or the API."""
    try:
        from fastmcp.server.dependencies import get_http_headers

        email = get_http_headers().get("x-forwarded-email", "")
    except Exception:
        email = ""
    if email:
        return email
    try:
        return client().current_user.me().user_name or ""
    except Exception as error:
        raise UserWorkspaceError(f"could not tell who is calling: {error}") from None


def default_folder(server_name: str) -> str:
    """/Workspace/Users/<caller>/mcp-servers/<server-name>."""
    email = caller_email()
    if not email or "/" in email:
        raise UserWorkspaceError("could not tell who is calling.")
    return f"/Workspace/Users/{email}/{SERVERS_FOLDER}/{slugify(server_name)}"


def check_folder(path: str) -> None:
    """Refuse anything outside the caller's own home folder."""
    home = f"/Workspace/Users/{caller_email()}/"
    normalized = path.rstrip("/") + "/"
    if not normalized.startswith(home) or "/../" in normalized or "/./" in normalized:
        raise UserWorkspaceError(
            f"{path} is outside your home folder ({home.rstrip('/')}); "
            "generated servers are only written there."
        )


def _is_not_found(error: Exception) -> bool:
    text = f"{type(error).__name__} {error}".lower()
    return "notfound" in text.replace(" ", "") or "does not exist" in text


def folder_entries(path: str) -> list[str] | None:
    """Names inside a folder, or None when the folder does not exist."""
    try:
        return sorted(
            (item.path or "").rsplit("/", 1)[-1]
            for item in client().workspace.list(path.removeprefix("/Workspace"))
        )
    except UserWorkspaceError:
        raise
    except Exception as error:
        if _is_not_found(error):
            return None
        raise UserWorkspaceError(f"could not read {path}: {error}") from None


def write_files(root: str, files: list[tuple[str, str]]) -> list[str]:
    """Create the folder tree and import each file, as the caller."""
    from databricks.sdk.service.workspace import ImportFormat

    check_folder(root)
    workspace = client().workspace
    api_root = root.removeprefix("/Workspace")
    made: set[str] = set()
    written: list[str] = []
    for relative, content in files:
        target = f"{api_root}/{relative}"
        parent = target.rsplit("/", 1)[0]
        try:
            if parent not in made:
                workspace.mkdirs(parent)
                made.add(parent)
            workspace.import_(
                target,
                content=base64.b64encode(content.encode("utf-8")).decode("ascii"),
                format=ImportFormat.AUTO,
                overwrite=True,
            )
        except Exception as error:
            raise UserWorkspaceError(
                f"stopped at {relative} after {len(written)} file(s): {error}"
            ) from None
        written.append(relative)
    return written
