"""Write to the caller's own folder after approval, then deploy behind a gate."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from fastmcp import Client

from mcp_server_creator import user_workspace
from mcp_server_creator.app import mcp
from mcp_server_creator.tools.deploy_server import app_name_for

ME = "analyst@agency.gov"
HOME = f"/Workspace/Users/{ME}"


class NotFound(Exception):
    pass


class FakeWorkspace:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()

    def list(self, path):
        path = path.rstrip("/")
        if path not in self.dirs:
            raise NotFound(f"Path ({path}) doesn't exist.")
        children = {
            name[len(path) + 1 :].split("/")[0]
            for name in list(self.files) + list(self.dirs)
            if name.startswith(path + "/")
        }
        return [SimpleNamespace(path=f"{path}/{child}") for child in children]

    def mkdirs(self, path):
        parts = path.strip("/").split("/")
        for index in range(1, len(parts) + 1):
            self.dirs.add("/" + "/".join(parts[:index]))

    def import_(self, path, content, format, overwrite):
        self.files[path] = base64.b64decode(content).decode()


class FakeApps:
    def __init__(self, owner: str = ME) -> None:
        self.apps: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.owner = owner

    def do(self, method, path, body=None):
        self.calls.append((method, path))
        parts = path.split("/")
        if method == "POST" and path == "/api/2.0/apps":
            self.apps[body["name"]] = {
                "name": body["name"],
                "creator": self.owner,
                "description": body.get("description", ""),
                "resources": body.get("resources", []),
                "compute_status": {"state": "STARTING"},
            }
            return self.apps[body["name"]]
        name = parts[4]
        if name not in self.apps:
            raise NotFound("App does not exist.")
        app = self.apps[name]
        if method == "GET":
            return app
        if method == "PATCH":
            # Like the real API: PATCH replaces the whole object, so a
            # field left out of the body is wiped.
            app["description"] = body.get("description", "")
            app["resources"] = body.get("resources", [])
            return app
        if path.endswith("/deployments"):
            app["pending_deployment"] = {
                "source_code_path": body["source_code_path"],
                "status": {"state": "IN_PROGRESS"},
            }
            return {"deployment_id": "dep_1"}
        if path.endswith("/start"):
            app["compute_status"] = {"state": "STARTING"}
            return app
        raise AssertionError(path)

    def finish_deployment(self, name: str) -> None:
        app = self.apps[name]
        pending = app.pop("pending_deployment")
        app["active_deployment"] = {
            "source_code_path": pending["source_code_path"],
            "status": {"state": "SUCCEEDED"},
        }
        app["app_status"] = {"state": "RUNNING"}
        app["url"] = f"https://{name}-1.aws.databricksapps.com"


@pytest.fixture
def fake():
    workspace = FakeWorkspace()
    apps = FakeApps()
    client = SimpleNamespace(
        workspace=workspace,
        api_client=apps,
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=ME)),
    )
    user_workspace.set_client(client)
    yield client
    user_workspace.set_client(None)


async def _call(name: str, **arguments) -> dict:
    async with Client(mcp) as client:
        result = await client.call_tool(name, arguments, raise_on_error=False)
    return result.structured_content or {}


async def _write(spec_json: str, **arguments) -> dict:
    return await _call(
        "creator_write_project",
        spec_json=spec_json,
        destination="workspace",
        **arguments,
    )


async def test_a_missing_folder_asks_first_with_yes_as_default(fake, designed_spec):
    plan = await _write(designed_spec.model_dump_json())
    assert plan["root"] == f"{HOME}/mcp-servers/{designed_spec.server_name}"
    assert plan["folder_exists"] is False
    assert plan["approval"]["default"] == "yes"
    assert "Create the folder" in plan["approval"]["ask_user"]
    assert not fake.workspace.files, "asking must not write"


async def test_approved_write_creates_the_folder_as_the_user(fake, designed_spec):
    spec_json = designed_spec.model_dump_json()
    done = await _write(spec_json, dry_run=False)
    root = done["root"].removeprefix("/Workspace")
    assert done["folder_created"] is True
    assert f"{root}/app.yaml" in fake.workspace.files
    assert all(path.startswith(root + "/") for path in fake.workspace.files)
    assert "creator_deploy_server" in done["next_step"]

    again = await _write(spec_json, dry_run=False)
    assert again["wrote_nothing"] is True and again["collisions"]


async def test_a_folder_outside_home_is_refused(fake, designed_spec):
    out = await _write(
        designed_spec.model_dump_json(),
        folder="/Workspace/Users/someone.else@agency.gov/x",
        dry_run=False,
    )
    assert "outside your home folder" in out["error"]
    assert not fake.workspace.files


async def test_create_folder_false_writes_nothing(fake, designed_spec):
    out = await _write(
        designed_spec.model_dump_json(), dry_run=False, create_folder=False
    )
    assert out["wrote_nothing"] is True and not fake.workspace.files


def test_app_names_follow_the_apps_rules(designed_spec):
    name = app_name_for(designed_spec)
    assert name.startswith("mcp-") and len(name) <= 30
    assert not name.endswith("-") and "mcp-mcp" not in name


async def test_deploy_walks_from_approval_to_live(fake, designed_spec):
    spec_json = designed_spec.model_dump_json()
    await _write(spec_json, dry_run=False)

    plan = await _call("creator_deploy_server", spec_json=spec_json)
    assert plan["status"] == "needs_approval"
    assert plan["approval"]["default"] == "yes"
    assert plan["security"]["blockers"] == []
    assert not fake.api_client.calls, "planning must not touch apps"

    name = plan["app_name"]
    step = await _call("creator_deploy_server", spec_json=spec_json, approve=True)
    assert step["status"] == "creating" and name in fake.api_client.apps

    step = await _call("creator_deploy_server", spec_json=spec_json, approve=True)
    assert step["status"] == "creating"

    fake.api_client.apps[name]["compute_status"] = {"state": "ACTIVE"}
    step = await _call("creator_deploy_server", spec_json=spec_json, approve=True)
    assert step["status"] == "deploying" and step["deployment_id"] == "dep_1"

    step = await _call("creator_deploy_server", spec_json=spec_json, approve=True)
    assert step["status"] == "deploying"

    fake.api_client.finish_deployment(name)
    live = await _call("creator_deploy_server", spec_json=spec_json, approve=True)
    assert live["status"] == "live"
    assert live["mcp_url"].endswith("/mcp") and name in live["connect"]
    deployments = [c for c in fake.api_client.calls if c[1].endswith("/deployments")]
    assert len(deployments) == 1, "a live app is not redeployed unless asked"


async def test_redeploy_refreshes_the_description_and_keeps_resources(
    fake, designed_spec
):
    spec_json = designed_spec.model_dump_json()
    await _write(spec_json, dry_run=False)
    plan = await _call("creator_deploy_server", spec_json=spec_json)
    name = plan["app_name"]
    assert plan["plan"]["description"].endswith("Built with MCP A Creator.")
    assert len(plan["plan"]["description"]) <= 500

    await _call("creator_deploy_server", spec_json=spec_json, approve=True)
    app = fake.api_client.apps[name]
    resources = list(app["resources"])
    app["description"] = "stale description"
    app["compute_status"] = {"state": "ACTIVE"}
    step = await _call("creator_deploy_server", spec_json=spec_json, approve=True)
    assert step["status"] == "deploying"
    assert ("PATCH", f"/api/2.0/apps/{name}") in fake.api_client.calls
    assert app["description"] == plan["plan"]["description"]
    assert app["resources"] == resources, "PATCH must not wipe resources"


async def test_deploy_needs_the_project_folder_first(fake, designed_spec):
    out = await _call(
        "creator_deploy_server", spec_json=designed_spec.model_dump_json()
    )
    assert out["status"] == "failed" and "app.yaml" in out["error"]


async def test_deploy_never_touches_someone_elses_app(fake, designed_spec):
    spec_json = designed_spec.model_dump_json()
    await _write(spec_json, dry_run=False)
    fake.api_client.owner = "other@agency.gov"
    await _call("creator_deploy_server", spec_json=spec_json, approve=True)
    out = await _call("creator_deploy_server", spec_json=spec_json, approve=True)
    assert out["status"] == "failed" and "belongs to" in out["error"]
    assert not [c for c in fake.api_client.calls if c[1].endswith("/deployments")]


async def test_a_blocked_review_stops_the_deploy(fake, designed_spec, monkeypatch):
    from mcp_server_creator.tools import deploy_server

    monkeypatch.setattr(
        deploy_server,
        "security_summary",
        lambda spec: {"blockers": ["writes without a guard"]},
    )
    out = await _call(
        "creator_deploy_server",
        spec_json=designed_spec.model_dump_json(),
        approve=True,
    )
    assert out["status"] == "blocked"
    assert not fake.api_client.calls


async def test_without_a_user_token_nothing_is_written(designed_spec):
    out = await _write(designed_spec.model_dump_json(), dry_run=False)
    assert "no user token" in out["error"]
