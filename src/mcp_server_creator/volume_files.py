"""Read and write files in a Unity Catalog volume through the Files API.

Databricks Apps do not mount `/Volumes` as a local filesystem, so anything
the server keeps in a volume (shared designs, generated projects, traces)
goes through the Files API instead. This runs as the app's service
principal, which needs READ VOLUME and WRITE VOLUME on the volume; attach the
volume as an app resource and Databricks grants exactly that.

The client is created lazily and can be replaced, so tests run with no
network and no credentials.
"""

from __future__ import annotations

import io
import re
from typing import Any

VOLUME_ROOT = re.compile(r"^/Volumes/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+")
_SAFE_PART = re.compile(r"^[A-Za-z0-9_.\-]+$")

_client: Any = None


class VolumeError(RuntimeError):
    """A sanitized volume failure: bad path, missing grant, or no client."""


def is_volume_path(path: str) -> bool:
    """True for /Volumes/<catalog>/<schema>/<volume>[/...]."""
    return bool(VOLUME_ROOT.match((path or "").strip()))


def join(root: str, relative: str) -> str:
    """Join a relative path under a volume root, refusing any traversal.

    Raises:
        VolumeError: if the root is not a volume or the path escapes it.
    """
    root = (root or "").strip().rstrip("/")
    if not is_volume_path(root):
        raise VolumeError(
            f"'{root}' is not a /Volumes/<catalog>/<schema>/<volume> path"
        )
    parts = [part for part in relative.replace("\\", "/").split("/") if part]
    if not parts:
        raise VolumeError("an empty relative path is not a file")
    for part in parts:
        if part in {".", ".."} or not _SAFE_PART.match(part):
            raise VolumeError(f"'{relative}' is not a safe relative path")
    return root + "/" + "/".join(parts)


def set_client(client: Any) -> None:
    """Replace the Files API client. Pass None to go back to the default."""
    global _client
    _client = client


def _files() -> Any:
    """The `files` API of a WorkspaceClient, created on first use."""
    global _client
    if _client is None:
        try:
            from databricks.sdk import WorkspaceClient
        except ImportError as error:
            raise VolumeError(
                "databricks-sdk is not installed, so volume storage is unavailable."
            ) from error
        try:
            _client = WorkspaceClient()
        except Exception:
            raise VolumeError(
                "No Databricks credentials for volume storage. On Databricks "
                "Apps these are injected automatically."
            ) from None
    return _client.files


def _is_not_found(error: Exception) -> bool:
    name = type(error).__name__
    return name in {"NotFound", "ResourceDoesNotExist"} or "404" in str(error)


def write_text(path: str, text: str) -> None:
    """Create or overwrite one file."""
    try:
        _files().upload(path, io.BytesIO(text.encode("utf-8")), overwrite=True)
    except VolumeError:
        raise
    except Exception:
        raise VolumeError(
            f"could not write {path}. Check that the app's service principal "
            "has WRITE VOLUME on the volume (attach it as an app resource)."
        ) from None


def read_text(path: str) -> str | None:
    """Read one file, or None if it does not exist."""
    try:
        response = _files().download(path)
        return response.contents.read().decode("utf-8")
    except VolumeError:
        raise
    except Exception as error:
        if _is_not_found(error):
            return None
        raise VolumeError(
            f"could not read {path}. Check that the app's service principal "
            "has READ VOLUME on the volume."
        ) from None


def delete(path: str) -> bool:
    """Delete one file. Returns whether it existed."""
    try:
        _files().delete(path)
        return True
    except VolumeError:
        raise
    except Exception as error:
        if _is_not_found(error):
            return False
        raise VolumeError(f"could not delete {path}.") from None


def list_dirs(directory: str) -> list[str]:
    """Subdirectory names directly inside a directory; empty if none."""
    try:
        entries = _files().list_directory_contents(directory.rstrip("/"))
        return sorted(
            entry.name
            for entry in entries
            if entry.name and getattr(entry, "is_directory", False)
        )
    except VolumeError:
        raise
    except Exception as error:
        if _is_not_found(error):
            return []
        raise VolumeError(f"could not list {directory}.") from None


def list_names(directory: str) -> list[str]:
    """File names directly inside a directory; empty if it does not exist."""
    try:
        entries = _files().list_directory_contents(directory.rstrip("/"))
        return sorted(
            entry.name
            for entry in entries
            if entry.name and not getattr(entry, "is_directory", False)
        )
    except VolumeError:
        raise
    except Exception as error:
        if _is_not_found(error):
            return []
        raise VolumeError(f"could not list {directory}.") from None
