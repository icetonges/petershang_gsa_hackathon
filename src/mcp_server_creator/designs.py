"""Design handles: the state a multi-step build needs, without the payload.

A specification is about 17 KB of nested JSON. Asking a model to re-emit it
on every call costs thousands of tokens per request and, through an AI
gateway that forwards schemas verbatim to a provider with a narrower schema
subset, can come back empty. So the model gets an eight-character handle
instead, and the server keeps the document.

Two backends, because the right one depends on the environment:

  memory  Fast, zero setup, and correct only when one replica serves every
          request. Databricks Apps supports horizontal scaling, so this is a
          development default, not a deployment one.
  file    A directory every replica can read, such as a mounted volume.

`spec_json` is always accepted as an alternative to a handle, so a stateless
deployment and a client that would rather hold the document itself both work
with no server state at all.
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mcp_server_creator.config import settings
from mcp_server_creator.models import ServerSpec
from mcp_server_creator.utils import UnsafePathError, safe_output_path

HANDLE_PREFIX = "dsn_"
HANDLE_BYTES = 4
MAX_MEMORY_DESIGNS = 256

_MEMORY: dict[str, tuple[float, dict[str, Any]]] = {}


class DesignError(LookupError):
    """A handle does not resolve, or a stored document cannot be read."""


def new_handle() -> str:
    """A short, unambiguous handle a model can repeat without mangling it."""
    return HANDLE_PREFIX + secrets.token_hex(HANDLE_BYTES)


def store_mode() -> str:
    """Which backend is active: 'memory' or 'file'."""
    configured = (settings.creator_design_store or "memory").strip().lower()
    if configured == "file" and settings.creator_design_dir:
        return "file"
    return "memory"


def is_durable() -> bool:
    """True when every replica of this server can read the same designs."""
    return store_mode() == "file"


def _file_path(handle: str) -> Path:
    """Resolve a handle to a file inside the configured directory.

    Raises:
        DesignError: on a malformed handle or an unusable directory.
    """
    if (
        not handle.startswith(HANDLE_PREFIX)
        or not handle[len(HANDLE_PREFIX) :].isalnum()
    ):
        raise DesignError(f"'{handle}' is not a design handle")
    try:
        return safe_output_path(settings.creator_design_dir, f"{handle}.json")
    except UnsafePathError as error:
        raise DesignError(str(error)) from None


def save(spec: ServerSpec, handle: str | None = None) -> str:
    """Store a specification and return its handle."""
    handle = handle or new_handle()
    document = spec.model_dump(mode="json")
    if store_mode() == "file":
        path = _file_path(handle)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return handle
    if len(_MEMORY) >= MAX_MEMORY_DESIGNS:
        oldest = min(_MEMORY, key=lambda key: _MEMORY[key][0])
        _MEMORY.pop(oldest, None)
    _MEMORY[handle] = (time.time(), document)
    return handle


def load(handle: str) -> ServerSpec:
    """Load a specification by handle.

    Raises:
        DesignError: if the handle is unknown or the document is invalid.
    """
    document: dict[str, Any] | None = None
    if store_mode() == "file":
        path = _file_path(handle)
        if path.is_file():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                raise DesignError(
                    f"the stored design {handle} is not readable JSON"
                ) from None
    else:
        entry = _MEMORY.get(handle)
        document = entry[1] if entry else None

    if document is None:
        raise DesignError(
            f"no design named {handle!r}. Handles do not survive a server "
            "restart, and with the in-memory store they do not cross "
            "replicas. Call creator_start_design again, or pass spec_json."
        )
    try:
        return ServerSpec.model_validate(document)
    except ValidationError as error:
        raise DesignError(
            f"the stored design {handle} no longer validates: {error.error_count()} "
            "problem(s). Start a new design."
        ) from None


def resolve(design_id: str | None, spec_json: str | None) -> tuple[ServerSpec, str]:
    """Resolve a design from a handle or from inline JSON.

    Returns the specification and which input supplied it.

    Raises:
        DesignError: when neither input resolves, with a message that says
            what to pass instead.
    """
    if design_id and spec_json:
        raise DesignError(
            "pass either design_id or spec_json, not both, so it is clear "
            "which one is authoritative"
        )
    if design_id:
        return load(design_id.strip()), "design_id"
    if spec_json:
        text = spec_json.strip()
        if not text.startswith("{"):
            raise DesignError(
                "spec_json must be a JSON object. Pass the `spec` value a "
                "previous call returned, serialized as a string."
            )
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise DesignError(
                f"spec_json is not valid JSON: {error.msg} at position {error.pos}"
            ) from None
        try:
            return ServerSpec.model_validate(document), "spec_json"
        except ValidationError as error:
            raise DesignError(
                "spec_json does not match the specification shape: "
                + "; ".join(
                    f"{'.'.join(str(p) for p in item['loc'])}: {item['msg']}"
                    for item in error.errors(include_url=False)[:4]
                )
            ) from None
    raise DesignError(
        "pass design_id, the handle creator_start_design returned, or "
        "spec_json if you are holding the document yourself"
    )


def persist(spec: ServerSpec, design_id: str | None) -> str | None:
    """Write an updated specification back under its handle, if it has one."""
    if not design_id:
        return None
    return save(spec, design_id.strip())


def handle_advice(multi_replica: bool) -> str:
    """Tell the caller which input to use, given the deployment reality."""
    if store_mode() == "file":
        return (
            "Pass design_id on every later call. Designs are stored in a "
            "directory every replica can read."
        )
    if multi_replica:
        return (
            "This server stores designs in process while the platform may "
            "run several replicas, so a handle can miss. Keep the returned "
            "spec and pass it as spec_json, or configure "
            "CREATOR_DESIGN_STORE=file with CREATOR_DESIGN_DIR."
        )
    return (
        "Pass design_id on every later call. Designs are held in memory, so "
        "they do not survive a restart; the returned spec is the durable "
        "copy if you want one."
    )


def forget(handle: str) -> bool:
    """Drop one stored design. Returns whether anything was removed."""
    if store_mode() == "file":
        path = _file_path(handle)
        if path.is_file():
            path.unlink()
            return True
        return False
    return _MEMORY.pop(handle, None) is not None


def count() -> int:
    """How many designs are currently stored."""
    if store_mode() == "file":
        directory = Path(settings.creator_design_dir)
        return len(list(directory.glob("dsn_*.json"))) if directory.is_dir() else 0
    return len(_MEMORY)


DESIGN_ID_DESCRIPTION = (
    "The handle creator_start_design returned, e.g. 'dsn_1a2b3c4d'. Leave "
    "empty and pass spec_json instead if you are holding the document."
)

SPEC_JSON_DESCRIPTION = (
    "The specification as a JSON object string, as an alternative to "
    "design_id. Use this when the handle has expired, when the server runs "
    "several replicas with the in-memory store, or when you would rather "
    "keep the document yourself."
)


def resolve_result(
    design_id: str, spec_json: str
) -> tuple[ServerSpec | None, str, dict[str, Any] | None]:
    """Resolve a design, returning a client-ready error instead of raising.

    Returns `(spec, source, None)` on success and `(None, "", payload)` on
    failure, where the payload carries an actionable hint.
    """
    try:
        spec, source = resolve(design_id.strip(), spec_json.strip())
    except DesignError as error:
        return (
            None,
            "",
            {
                "error": str(error),
                "hint": (
                    "Call creator_start_design to get a handle, or pass the "
                    "spec you already have as spec_json."
                ),
                "store_mode": store_mode(),
                "designs_held": count(),
            },
        )
    return spec, source, None
