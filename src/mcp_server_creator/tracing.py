"""Tool-call traces: what ran, for whom, how long, and whether it worked.

Two channels, with different audiences:

* Audit lines. One JSON line per call to stdout (the app's Logs tab) and,
  when CREATOR_TRACE_DIR names a volume directory, one small file per call.
  These never contain arguments or results.
* MLflow traces, for evaluation. When MLFLOW_EXPERIMENT_ID is set (on
  Databricks Apps, from the `experiment` resource), every call becomes an
  MLflow trace with a TOOL span holding the clipped inputs and outputs, the
  caller as the trace user, and the design id as the session, so one whole
  build reads as one conversation and can be scored with mlflow.genai.
  Access is governed by the experiment's permissions. Set
  CREATOR_MLFLOW_CAPTURE=metadata to keep inputs and outputs out of it too.

Tracing is best effort: a failure to record a trace never fails the call.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

from mcp_server_creator import volume_files
from mcp_server_creator.config import settings


def _caller() -> dict[str, str]:
    """Who called, from headers Databricks Apps sets. Never the token."""
    try:
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers()
    except Exception:
        headers = {}
    return {
        "caller": headers.get("x-forwarded-email", ""),
        "identity": "user" if headers.get("x-forwarded-access-token") else "app",
        "request_id": headers.get("x-request-id", ""),
    }


def _outcome(result: Any) -> tuple[str, str]:
    """'ok' or 'tool_error', and the first line of any error the tool returned."""
    content = getattr(result, "structured_content", None)
    if isinstance(content, dict) and content.get("error"):
        return "tool_error", str(content["error"]).splitlines()[0][:200]
    return "ok", ""


def record(entry: dict[str, Any]) -> None:
    """Emit one trace: stdout always, the volume when configured."""
    line = json.dumps(entry, separators=(",", ":"))
    print(f"TRACE {line}", file=sys.stdout, flush=True)
    directory = settings.creator_trace_dir
    if not volume_files.is_volume_path(directory):
        return
    day = entry["ts"][:10]
    name = f"{entry['ts'][11:19].replace(':', '')}-{entry['tool']}-{entry['id']}.json"
    try:
        volume_files.write_text(volume_files.join(directory, f"{day}/{name}"), line)
    except volume_files.VolumeError as error:
        print(f"TRACE_WRITE_FAILED {error}", file=sys.stderr, flush=True)


# ── MLflow ────────────────────────────────────────────────────────────────────

CLIP_CHARS = 4_000
_mlflow_module: Any = None
_mlflow_state = "unset"  # unset | ready | off
mlflow_error = ""


def mlflow_status() -> dict[str, Any]:
    """For creator_check_environment: is evaluation tracing on, and where."""
    _mlflow()
    return {
        "enabled": _mlflow_state == "ready",
        "experiment_id": os.environ.get("MLFLOW_EXPERIMENT_ID", ""),
        "capture": settings.creator_mlflow_capture,
        "error": mlflow_error,
    }


_probe_result: dict[str, Any] | None = None
PROBE_TIMEOUT_S = 25
PROBE_READ_ATTEMPTS = 6
# A failing probe costs PROBE_TIMEOUT_S, so its answer is reused this long.
PROBE_FAILURE_TTL_S = 600
_probe_checked_at = 0.0


def upload_probe() -> dict[str, Any]:
    """Log one tiny trace end to end and read it back, to prove span data lands.

    The trace header and the span data travel separately: the header goes
    through the tracking API, the spans are a file uploaded to the
    experiment's artifact storage. Tool calls never wait for that upload, and
    MLflow only warns when it fails, so a broken upload is invisible. This
    runs the whole path synchronously, as its own trace (not a child of the
    current tool span), and returns what went wrong. A pass is cached for the
    life of the process; a failure is reused for PROBE_FAILURE_TTL_S.
    """
    global _probe_result, _probe_checked_at
    if _probe_result is not None and (
        _probe_result.get("ok")
        or time.monotonic() - _probe_checked_at < PROBE_FAILURE_TTL_S
    ):
        return {**_probe_result, "cached": True}
    mlflow = _mlflow()
    if mlflow is None:
        return {
            "ran": False,
            "reason": "evaluation tracing is off",
            "error": mlflow_error,
        }
    import logging
    from importlib import metadata

    result: dict[str, Any] = {"ran": True, "ok": False}
    with contextlib.suppress(Exception):
        result["mlflow_version"] = mlflow.__version__
        result["distributions"] = sorted(
            f"{dist.metadata['Name']}=={dist.version}"
            for dist in metadata.distributions()
            if (dist.metadata["Name"] or "").lower().startswith("mlflow")
        )
    with contextlib.suppress(Exception):
        from databricks.sdk import WorkspaceClient

        result["tracking_identity"] = WorkspaceClient().current_user.me().user_name

    warnings: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            text = record.getMessage()
            if record.exc_info:
                last = logging.Formatter().formatException(record.exc_info)
                text += " | " + last.strip().splitlines()[-1]
            warnings.append(text[:500])

    handler = _Collect(level=logging.WARNING)
    logger = logging.getLogger("mlflow")
    logger.addHandler(handler)

    def _run() -> None:
        # A new thread starts with an empty context, so the probe is its own
        # trace rather than a child of the tool span that called it (MLflow
        # refuses MlflowClient.start_trace under an active trace).
        try:
            result["stage"] = "start_trace"
            client = mlflow.MlflowClient()
            root = client.start_trace(
                name="tracing_upload_probe",
                span_type="TOOL",
                inputs={"probe": "upload"},
                tags={"server": "mcp-a-creator", "probe": "upload"},
            )
            trace_id = root.trace_id
            result["trace_id"] = trace_id
            result["stage"] = "end_trace (export queued)"
            client.end_trace(trace_id, outputs={"ok": True})
            flush = getattr(mlflow, "flush_trace_async_logging", None)
            if flush is not None:
                result["stage"] = "flush (header + span upload)"
                flush()
            result["stage"] = "read back (download span data)"
            # Unity Catalog storage lands spans a few seconds after export.
            spans = 0
            for _attempt in range(PROBE_READ_ATTEMPTS):
                with contextlib.suppress(Exception):
                    trace = client.get_trace(trace_id)
                    spans = len(trace.data.spans) if trace and trace.data else 0
                if spans:
                    break
                time.sleep(2)
            result["spans_read_back"] = spans
            result["ok"] = spans > 0
            result["stage"] = "done"
        except Exception as error:
            result["error"] = f"{type(error).__name__}: {str(error)[:300]}"

    import threading

    try:
        worker = threading.Thread(target=_run, name="mlflow-upload-probe")
        worker.start()
        worker.join(timeout=PROBE_TIMEOUT_S)
        if worker.is_alive():
            result["error"] = (
                f"probe still running after {PROBE_TIMEOUT_S}s; "
                "stuck at the stage shown"
            )
    finally:
        logger.removeHandler(handler)
    result = dict(result)
    if warnings:
        result["mlflow_warnings"] = warnings[-5:]
    if not result["ok"]:
        print(
            f"TRACE_UPLOAD_PROBE_FAILED {json.dumps(result)}",
            file=sys.stderr,
            flush=True,
        )
    _probe_result = result
    _probe_checked_at = time.monotonic()
    return result


def _mlflow() -> Any:
    """The mlflow module, configured once, or None when tracing is off."""
    global _mlflow_module, _mlflow_state, mlflow_error
    if _mlflow_state == "ready":
        return _mlflow_module
    if _mlflow_state == "off":
        return None
    experiment = os.environ.get("MLFLOW_EXPERIMENT_ID", "")
    if not settings.creator_trace_enabled or not experiment:
        _mlflow_state = "off"
        return None
    try:
        import mlflow

        mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "databricks"))
        mlflow.set_experiment(experiment_id=experiment)
    except Exception as error:
        mlflow_error = f"{type(error).__name__}: {str(error)[:200]}"
        print(f"TRACE_MLFLOW_OFF {mlflow_error}", file=sys.stderr, flush=True)
        _mlflow_state = "off"
        return None
    _mlflow_module = mlflow
    _mlflow_state = "ready"
    return mlflow


def warm_up() -> None:
    """Configure MLflow in the background so the first tool call stays fast."""
    if not os.environ.get("MLFLOW_EXPERIMENT_ID"):
        return
    import threading

    threading.Thread(target=_mlflow, name="mlflow-warm-up", daemon=True).start()


def _clip(value: Any) -> Any:
    """Keep a trace readable: long strings and big objects are shortened."""
    if isinstance(value, str):
        if len(value) <= CLIP_CHARS:
            return value
        return value[:CLIP_CHARS] + f"... [{len(value) - CLIP_CHARS} chars clipped]"
    if isinstance(value, dict):
        return {key: _clip(item) for key, item in value.items()}
    if isinstance(value, list):
        clipped = [_clip(item) for item in value[:50]]
        if len(value) > 50:
            clipped.append(f"... [{len(value) - 50} items clipped]")
        return clipped
    return value


def _design_id(arguments: dict[str, Any], content: Any) -> str:
    for source in (arguments, content if isinstance(content, dict) else {}):
        value = source.get("design_id") if isinstance(source, dict) else None
        if isinstance(value, str) and value:
            return value
    return ""


@contextlib.contextmanager
def _mlflow_span(tool: str):
    mlflow = _mlflow()
    if mlflow is None:
        yield None
        return
    try:
        manager = mlflow.start_span(name=tool, span_type="TOOL")
        span = manager.__enter__()
    except Exception:
        yield None
        return
    try:
        yield span
    except BaseException as error:
        with contextlib.suppress(Exception):
            manager.__exit__(type(error), error, error.__traceback__)
        raise
    else:
        with contextlib.suppress(Exception):
            manager.__exit__(None, None, None)


def _annotate(span: Any, entry: dict[str, Any], arguments: dict, result: Any) -> None:
    """Fill the span and trace. Never raises."""
    if span is None:
        return
    content = getattr(result, "structured_content", None)
    full = settings.creator_mlflow_capture != "metadata"
    with contextlib.suppress(Exception):
        names_only = {"argument_names": sorted(arguments)}
        span.set_inputs(_clip(arguments) if full else names_only)
        if full:
            span.set_outputs(_clip(content) if content is not None else None)
        span.set_attributes(
            {k: v for k, v in entry.items() if k in ("outcome", "error", "identity")}
        )
    mlflow = _mlflow_module
    tags = {
        "tool": entry["tool"],
        "outcome": entry.get("outcome", ""),
        "identity": entry.get("identity", ""),
        "server": "mcp-a-creator",
    }
    session = _design_id(arguments, content)
    with contextlib.suppress(Exception):
        mlflow.update_current_trace(
            tags=tags,
            session_id=session or None,
            user=entry.get("caller") or None,
        )


class TraceMiddleware(Middleware):
    """Record every tool call: an audit line, plus an MLflow trace when on."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        started = time.perf_counter()
        tool = getattr(context.message, "name", "unknown")
        arguments = dict(getattr(context.message, "arguments", None) or {})
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "id": secrets.token_hex(4),
            "tool": tool,
            **_caller(),
        }
        with _mlflow_span(tool) as span:
            try:
                result = await call_next(context)
            except Exception as error:
                entry.update(
                    outcome="exception",
                    error_type=type(error).__name__,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                )
                _annotate(span, entry, arguments, None)
                await _record_quietly(entry)
                raise
            outcome, message = _outcome(result)
            entry.update(
                outcome=outcome,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            if message:
                entry["error"] = message
            _annotate(span, entry, arguments, result)
        await _record_quietly(entry)
        return result


async def _record_quietly(entry: dict[str, Any]) -> None:
    if not settings.creator_trace_enabled:
        return
    try:
        await asyncio.to_thread(record, entry)
    except Exception:
        pass
