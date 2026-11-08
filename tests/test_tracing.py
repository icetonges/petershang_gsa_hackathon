"""Every tool call leaves a trace, and no trace carries arguments or results."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastmcp import Client

from mcp_server_creator import volume_files
from mcp_server_creator.app import mcp
from mcp_server_creator.config import settings

SECRET_PURPOSE = "zebra-canary-purpose-text"


def _traces(output: str) -> list[dict]:
    return [
        json.loads(line[len("TRACE ") :])
        for line in output.splitlines()
        if line.startswith("TRACE ")
    ]


async def test_a_tool_call_emits_one_trace(capsys):
    async with Client(mcp) as client:
        await client.call_tool("creator_list_library", {})
    traces = _traces(capsys.readouterr().out)
    assert len(traces) == 1
    trace = traces[0]
    assert trace["tool"] == "creator_list_library"
    assert trace["outcome"] == "ok"
    assert isinstance(trace["duration_ms"], int)
    assert set(trace) >= {"ts", "id", "caller", "identity"}


async def test_a_trace_never_contains_arguments(capsys):
    async with Client(mcp) as client:
        await client.call_tool(
            "creator_start_design",
            {
                "purpose": SECRET_PURPOSE,
                "primary_user": "analyst",
                "jobs_to_be_done": ["find expiring awards"],
            },
            raise_on_error=False,
        )
    output = capsys.readouterr().out
    assert _traces(output)
    assert SECRET_PURPOSE not in output


async def test_a_returned_error_is_traced_as_a_tool_error(capsys):
    async with Client(mcp) as client:
        await client.call_tool(
            "creator_generate_project", {"design_id": "dsn_0"}, raise_on_error=False
        )
    (trace,) = _traces(capsys.readouterr().out)
    assert trace["outcome"] == "tool_error"
    assert trace["error"]


async def test_traces_go_to_a_volume_when_configured(monkeypatch, capsys):
    written: dict[str, str] = {}

    class Files:
        def upload(self, path, contents, overwrite=False):
            written[path] = contents.read().decode()

    volume_files.set_client(SimpleNamespace(files=Files()))
    root = "/Volumes/users/someone/mcp_a_creator/traces"
    monkeypatch.setattr(settings, "creator_trace_dir", root)
    try:
        async with Client(mcp) as client:
            await client.call_tool("creator_list_library", {})
    finally:
        volume_files.set_client(None)
    (path,) = written
    assert path.startswith(root + "/") and path.endswith(".json")
    assert json.loads(written[path])["tool"] == "creator_list_library"


@pytest.mark.parametrize("enabled", [False])
async def test_tracing_can_be_turned_off(monkeypatch, capsys, enabled):
    monkeypatch.setattr(settings, "creator_trace_enabled", enabled)
    async with Client(mcp) as client:
        await client.call_tool("creator_list_library", {})
    assert not _traces(capsys.readouterr().out)
