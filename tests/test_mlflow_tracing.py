"""Evaluation traces: each tool call becomes an MLflow trace, grouped by design."""

from __future__ import annotations

import contextlib

import pytest
from fastmcp import Client

from mcp_server_creator import tracing
from mcp_server_creator.app import mcp
from mcp_server_creator.config import settings


class FakeSpan:
    def __init__(self, name: str, span_type: str) -> None:
        self.name, self.span_type = name, span_type
        self.inputs = self.outputs = None
        self.attributes: dict = {}
        self.closed_with: object = "open"

    def set_inputs(self, value):
        self.inputs = value

    def set_outputs(self, value):
        self.outputs = value

    def set_attributes(self, value):
        self.attributes.update(value)


class FakeMlflow:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []
        self.traces: list[dict] = []

    @contextlib.contextmanager
    def start_span(self, name, span_type="UNKNOWN"):
        span = FakeSpan(name, span_type)
        self.spans.append(span)
        try:
            yield span
        except BaseException as error:
            span.closed_with = type(error).__name__
            raise
        span.closed_with = None

    def update_current_trace(self, **kwargs):
        self.traces.append(kwargs)


@pytest.fixture
def fake_mlflow(monkeypatch):
    fake = FakeMlflow()
    monkeypatch.setattr(tracing, "_mlflow_module", fake)
    monkeypatch.setattr(tracing, "_mlflow_state", "ready")
    return fake


async def test_a_tool_call_becomes_one_tool_span(fake_mlflow):
    async with Client(mcp) as client:
        await client.call_tool("creator_list_library", {})
    (span,) = fake_mlflow.spans
    assert span.name == "creator_list_library" and span.span_type == "TOOL"
    assert span.closed_with is None
    assert isinstance(span.outputs, dict) and span.outputs.get("skills")
    assert fake_mlflow.traces[0]["tags"]["outcome"] == "ok"


async def test_the_design_id_becomes_the_session(fake_mlflow):
    async with Client(mcp) as client:
        started = await client.call_tool(
            "creator_start_design",
            {"purpose": "Weather forecasts for city planners."},
        )
        design_id = started.structured_content["design_id"]
        await client.call_tool("creator_review_server", {"design_id": design_id})
    sessions = [trace["session_id"] for trace in fake_mlflow.traces]
    assert sessions == [design_id, design_id]
    assert fake_mlflow.spans[1].inputs == {"design_id": design_id}


async def test_long_inputs_are_clipped(fake_mlflow):
    long_purpose = "x" * (tracing.CLIP_CHARS + 500)
    async with Client(mcp) as client:
        await client.call_tool("creator_start_design", {"purpose": long_purpose})
    recorded = fake_mlflow.spans[0].inputs["purpose"]
    assert len(recorded) < len(long_purpose) and "clipped" in recorded


async def test_metadata_capture_keeps_values_out(fake_mlflow, monkeypatch):
    monkeypatch.setattr(settings, "creator_mlflow_capture", "metadata")
    async with Client(mcp) as client:
        await client.call_tool("creator_start_design", {"purpose": "secret-canary"})
    span = fake_mlflow.spans[0]
    assert span.inputs == {"argument_names": ["purpose"]}
    assert span.outputs is None


async def test_a_broken_tracer_never_fails_the_call(monkeypatch):
    class Broken:
        def start_span(self, **_):
            raise RuntimeError("tracking server down")

    monkeypatch.setattr(tracing, "_mlflow_module", Broken())
    monkeypatch.setattr(tracing, "_mlflow_state", "ready")
    async with Client(mcp) as client:
        result = await client.call_tool("creator_list_library", {})
    assert result.structured_content.get("skills")


def test_tracing_is_off_without_an_experiment(monkeypatch):
    monkeypatch.delenv("MLFLOW_EXPERIMENT_ID", raising=False)
    monkeypatch.setattr(tracing, "_mlflow_state", "unset")
    assert tracing.mlflow_status()["enabled"] is False


def test_upload_probe_reports_off_without_an_experiment(monkeypatch):
    monkeypatch.delenv("MLFLOW_EXPERIMENT_ID", raising=False)
    monkeypatch.setattr(tracing, "_mlflow_state", "unset")
    monkeypatch.setattr(tracing, "_probe_result", None)
    assert tracing.upload_probe()["ran"] is False


def test_upload_probe_reports_spans_that_did_not_land(monkeypatch):
    class Info:
        trace_id = "tr-probe"

    class Trace:
        class data:
            spans: list = []

    class Client:
        def start_trace(self, **_):
            return Info()

        def end_trace(self, *_, **__):
            pass

        def get_trace(self, _):
            return Trace()

    class Mlflow:
        __version__ = "test"
        MlflowClient = Client

    monkeypatch.setattr(tracing, "_mlflow_module", Mlflow())
    monkeypatch.setattr(tracing, "_mlflow_state", "ready")
    monkeypatch.setattr(tracing, "_probe_result", None)
    monkeypatch.setattr(tracing, "PROBE_READ_ATTEMPTS", 1)
    monkeypatch.setattr(tracing.time, "sleep", lambda _: None)
    result = tracing.upload_probe()
    assert result["ran"] is True and result["ok"] is False
    assert result["spans_read_back"] == 0 and result["trace_id"] == "tr-probe"
    assert tracing.upload_probe()["cached"] is True
