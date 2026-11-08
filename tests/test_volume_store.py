"""The volume design store: shared across replicas, through the Files API.

A fake Files client stands in for Unity Catalog, so these run offline. Two
separate in-memory caches simulate two replicas reading the same volume.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from mcp_server_creator import designs, volume_files
from mcp_server_creator.config import settings
from mcp_server_creator.models import ServerSpec

ROOT = "/Volumes/users/someone/mcp_a_creator/designs"


class NotFound(Exception):
    """Named like the SDK's error, which is how the wrapper recognizes it."""


class FakeFiles:
    def __init__(self, fail: bool = False) -> None:
        self.blobs: dict[str, bytes] = {}
        self.fail = fail

    def upload(self, path, contents, overwrite=False):
        if self.fail:
            raise PermissionError("PERMISSION_DENIED secret-detail")
        self.blobs[path] = contents.read()

    def download(self, path):
        if path not in self.blobs:
            raise NotFound(path)
        return SimpleNamespace(contents=io.BytesIO(self.blobs[path]))

    def delete(self, path):
        if path not in self.blobs:
            raise NotFound(path)
        del self.blobs[path]

    def list_directory_contents(self, directory):
        prefix = directory.rstrip("/") + "/"
        return [
            SimpleNamespace(name=p[len(prefix) :], path=p, is_directory=False)
            for p in self.blobs
            if p.startswith(prefix) and "/" not in p[len(prefix) :]
        ]


@pytest.fixture
def volume(monkeypatch):
    fake = FakeFiles()
    volume_files.set_client(SimpleNamespace(files=fake))
    monkeypatch.setattr(settings, "creator_design_store", "volume")
    monkeypatch.setattr(settings, "creator_design_dir", ROOT)
    monkeypatch.setattr(designs, "last_volume_error", "")
    yield fake
    volume_files.set_client(None)
    designs._MEMORY.clear()


def _spec(name: str = "volume-mcp") -> ServerSpec:
    return ServerSpec(server_name=name, purpose="x")


def test_a_volume_directory_selects_the_volume_store(volume):
    assert designs.store_mode() == "volume"
    assert designs.is_durable()


def test_file_mode_stays_file_mode_even_for_a_volume_path(volume, monkeypatch):
    """The backend is explicit, so it never flips once a directory appears."""
    monkeypatch.setattr(settings, "creator_design_store", "file")
    assert designs.store_mode() == "file"


def test_a_non_volume_directory_does_not_select_the_volume_store(volume, monkeypatch):
    monkeypatch.setattr(settings, "creator_design_dir", "/tmp/not-a-volume")
    assert designs.store_mode() == "memory"


def test_designs_cross_replicas(volume):
    handle = designs.save(_spec())
    assert f"{ROOT}/{handle}.json" in volume.blobs
    designs._MEMORY.clear()  # a different replica has an empty memory
    assert designs.load(handle).server_name == "volume-mcp"


def test_updates_overwrite_under_the_same_handle(volume):
    handle = designs.save(_spec("first-mcp"))
    designs.persist(_spec("second-mcp"), handle)
    designs._MEMORY.clear()
    assert designs.load(handle).server_name == "second-mcp"


def test_count_and_forget(volume):
    first = designs.save(_spec())
    designs.save(_spec())
    assert designs.count() == 2
    assert designs.forget(first)
    assert not designs.forget(first)
    assert designs.count() == 1


def test_an_unknown_handle_is_a_clear_error(volume):
    with pytest.raises(designs.DesignError):
        designs.load("dsn_00000000")


def test_a_failed_write_falls_back_without_failing_the_call(volume):
    volume.fail = True
    handle = designs.save(_spec("fallback-mcp"))
    assert designs.load(handle).server_name == "fallback-mcp"
    assert "WRITE VOLUME" in designs.last_volume_error
    assert "secret-detail" not in designs.last_volume_error


@pytest.mark.parametrize(
    "relative", ["../escape.json", "a/../../b.json", "", "bad name.json"]
)
def test_volume_paths_refuse_traversal(relative):
    with pytest.raises(volume_files.VolumeError):
        volume_files.join(ROOT, relative)


async def _write(spec_json: str, **arguments) -> dict:
    from fastmcp import Client

    from mcp_server_creator.app import mcp

    async with Client(mcp) as client:
        result = await client.call_tool(
            "creator_write_project", {"spec_json": spec_json, **arguments}
        )
    return result.structured_content or {}


@pytest.fixture
def writable_volume(volume, monkeypatch):
    monkeypatch.setattr(settings, "creator_allow_write", True)
    monkeypatch.setattr(
        settings, "creator_output_root", "/Volumes/users/someone/mcp_a_creator/projects"
    )
    return volume


async def test_write_project_to_a_volume(writable_volume, designed_spec):
    spec_json = designed_spec.model_dump_json()
    planned = await _write(spec_json)
    assert planned["dry_run"] is True and planned["file_count"] > 10
    assert not writable_volume.blobs, "a dry run must write nothing"

    done = await _write(spec_json, dry_run=False, subdirectory="awards")
    root = "/Volumes/users/someone/mcp_a_creator/projects/awards"
    assert done["root"] == root
    assert done["file_count"] == planned["file_count"]
    assert f"{root}/pyproject.toml" in writable_volume.blobs
    assert all(path.startswith(root + "/") for path in writable_volume.blobs)


async def test_write_project_refuses_a_non_empty_folder(writable_volume, designed_spec):
    spec_json = designed_spec.model_dump_json()
    await _write(spec_json, dry_run=False, subdirectory="awards")
    again = await _write(spec_json, dry_run=False, subdirectory="awards")
    assert again.get("wrote_nothing") is True
    replaced = await _write(
        spec_json, dry_run=False, subdirectory="awards", overwrite=True
    )
    assert replaced["file_count"] > 10


def test_volume_join_rejects_a_non_volume_root():
    with pytest.raises(volume_files.VolumeError):
        volume_files.join("/tmp/x", "a.json")
    assert volume_files.join(ROOT, "p/a.json") == f"{ROOT}/p/a.json"
