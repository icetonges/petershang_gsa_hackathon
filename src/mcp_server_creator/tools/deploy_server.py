"""Deploy a generated server as a live MCP server, after a security gate.

The flow is the same every time, and stateless, so the client simply calls
again until the server is live:

1. Security gate. The design is reviewed; any blocker stops here.
2. Plan and approval. With approve=false (the default) nothing changes: the
   tool returns what it would create, what the new app could reach, and a
   question to put to the user, whose default answer is yes.
3. With approve=true it creates the app as the caller, then on later calls
   starts it, deploys the folder, and finally reports the MCP URL to add
   in Genie Code. Each call moves one step and says what happens next.

Everything runs with the caller's own token, so the app belongs to them.
An existing app owned by someone else is never touched.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from mcp_server_creator import designs, user_workspace
from mcp_server_creator.config import settings
from mcp_server_creator.generators.project import app_description
from mcp_server_creator.models import ServerSpec, SourceKind
from mcp_server_creator.review import review_spec
from mcp_server_creator.utils import slugify

APP_NAME_LIMIT = 30


def app_name_for(spec: ServerSpec) -> str:
    """mcp-<slug>, at most 30 characters, as Databricks Apps requires."""
    if spec.deployment.app_name:
        return spec.deployment.app_name[:APP_NAME_LIMIT].rstrip("-")
    slug = slugify(spec.server_name)
    slug = slug.removeprefix("mcp-").removesuffix("-mcp") or "server"
    return f"mcp-{slug}"[:APP_NAME_LIMIT].rstrip("-")


def _tables(spec: ServerSpec) -> list[str]:
    names = []
    for source in spec.data_sources:
        if source.kind is SourceKind.UC_TABLE and source.catalog and source.table:
            names.append(f"{source.catalog}.{source.schema_name}.{source.table}")
    return names


def _resources(spec: ServerSpec, grant_tables: bool) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    tables = _tables(spec)
    if tables and settings.databricks_warehouse_id:
        resources.append(
            {
                "name": spec.deployment.sql_warehouse_resource or "sql_warehouse",
                "description": "Warehouse the tools query.",
                "sql_warehouse": {
                    "id": settings.databricks_warehouse_id,
                    "permission": "CAN_USE",
                },
            }
        )
    if grant_tables:
        for index, table in enumerate(tables):
            resources.append(
                {
                    "name": f"table_{index + 1}",
                    "description": f"Read access to {table}.",
                    "uc_securable": {
                        "securable_full_name": table,
                        "securable_type": "TABLE",
                        "permission": "SELECT",
                    },
                }
            )
    return resources


def security_summary(spec: ServerSpec) -> dict[str, Any]:
    """What a reviewer needs before saying yes."""
    report = review_spec(spec)
    security = [
        {"severity": f.severity.value, "summary": f.summary, "fix": f.fix}
        for f in report.findings
        if f.criterion == "security_posture"
    ]
    write_tools = [tool.name for tool in spec.tools if not tool.read_only]
    return {
        "verdict": report.verdict,
        "blockers": [f.summary for f in report.blockers],
        "security_score": report.scores.get("security_posture"),
        "security_findings": security,
        "classification": spec.classification.value,
        "pii_present": spec.pii_present,
        "read_only": not write_tools,
        "write_tools": write_tools,
        "refuses": spec.out_of_scope,
        "tool_count": len(spec.tools),
        "tables_read": _tables(spec),
        "runs_as": (
            "the new app's own service principal, which can read only the "
            "resources listed here"
        ),
    }


def _api(method: str, path: str, body: dict | None = None) -> Any:
    return user_workspace.client().api_client.do(method, path, body=body)


def _get_app(name: str) -> dict[str, Any] | None:
    try:
        return _api("GET", f"/api/2.0/apps/{name}")
    except user_workspace.UserWorkspaceError:
        raise
    except Exception as error:
        if user_workspace._is_not_found(error):
            return None
        raise user_workspace.UserWorkspaceError(
            f"could not read app {name}: {error}"
        ) from None


def register(mcp: FastMCP) -> None:
    """Register the deploy tool with the MCP server."""

    @mcp.tool(
        name="creator_deploy_server",
        annotations={
            "title": "Deploy the generated server as a live MCP server",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    def creator_deploy_server(
        design_id: Annotated[
            str, Field(description=designs.DESIGN_ID_DESCRIPTION)
        ] = "",
        spec_json: Annotated[
            str, Field(description=designs.SPEC_JSON_DESCRIPTION)
        ] = "",
        source_path: Annotated[
            str,
            Field(
                description=(
                    "Workspace folder holding the project, as written by "
                    "creator_write_project. Defaults to "
                    "/Workspace/Users/<you>/mcp-servers/<server-name>."
                )
            ),
        ] = "",
        approve: Annotated[
            bool,
            Field(
                description=(
                    "False returns the security review and the plan and "
                    "changes nothing. Show both to the user and ask; set true "
                    "only after they agree (the default answer is yes)."
                )
            ),
        ] = False,
        grant_tables: Annotated[
            bool,
            Field(
                description=(
                    "Give the new app SELECT on the tables it reads. Needs "
                    "you to be able to grant on them; set false if creation "
                    "fails on a grant and grant access separately."
                )
            ),
        ] = True,
        redeploy: Annotated[
            bool,
            Field(description="Deploy a fresh snapshot even if the app is live."),
        ] = False,
    ) -> dict[str, Any]:
        """Security-review, then create and deploy the server as your app.

        Call with approve=false first and show the user `security` and
        `plan`. After they agree, call with approve=true, then call again
        while `status` is creating, starting or deploying.

        Returns: `status` (needs_approval, blocked, creating, starting,
        deploying, live, or failed), `app_name`, `security`, `plan`,
        `next_step`, and once live the `mcp_url` and how to connect it.
        """
        spec, _, failure = designs.resolve_result(design_id, spec_json)
        if failure:
            return failure
        assert spec is not None

        name = app_name_for(spec)
        security = security_summary(spec)
        if security["blockers"]:
            return {
                "status": "blocked",
                "app_name": name,
                "security": security,
                "next_step": (
                    "Fix the blockers (creator_review_server lists the fix for "
                    "each), then call again."
                ),
            }

        try:
            source = source_path.strip() or user_workspace.default_folder(
                spec.server_name
            )
            user_workspace.check_folder(source)
            entries = user_workspace.folder_entries(source)
        except user_workspace.UserWorkspaceError as error:
            return {"status": "failed", "app_name": name, "error": str(error)}
        if not entries or "app.yaml" not in entries:
            return {
                "status": "failed",
                "app_name": name,
                "error": f"No app.yaml in {source}.",
                "hint": (
                    "Write the project first with creator_write_project "
                    "(destination 'workspace', include_deployment true)."
                ),
            }

        resources = _resources(spec, grant_tables)
        plan = {
            "app_name": name,
            "source_path": source,
            "owner": "you",
            "resources": resources,
            "description": app_description(spec),
            "steps": [
                "create the app",
                "start its compute",
                "deploy the folder",
                "connect it in Genie Code",
            ],
        }
        if not approve:
            return {
                "status": "needs_approval",
                "app_name": name,
                "security": security,
                "plan": plan,
                "approval": {
                    "ask_user": (
                        f"Deploy {name} from {source} as a live MCP server "
                        "that you own? (default: yes)"
                    ),
                    "default": "yes",
                    "if_yes": "Call again with approve=true.",
                },
            }

        try:
            return _advance(name, source, plan, redeploy)
        except user_workspace.UserWorkspaceError as error:
            return {"status": "failed", "app_name": name, "error": str(error)}
        except Exception as error:
            hint = ""
            if grant_tables and "permission" in str(error).lower():
                hint = "Retry with grant_tables=false and grant SELECT separately."
            return {
                "status": "failed",
                "app_name": name,
                "error": f"{type(error).__name__}: {str(error)[:300]}",
                "hint": hint or "Check the app page for details.",
            }


def _advance(name: str, source: str, plan: dict, redeploy: bool) -> dict[str, Any]:
    """Move the deployment one step forward and say where it stands."""
    again = "Call again with approve=true in about a minute."
    app = _get_app(name)
    if app is None:
        _api(
            "POST",
            "/api/2.0/apps",
            {
                "name": name,
                "description": plan["description"],
                "resources": plan["resources"],
            },
        )
        return {"status": "creating", "app_name": name, "next_step": again}

    me = user_workspace.caller_email()
    if app.get("creator") and app["creator"].lower() != me.lower():
        return {
            "status": "failed",
            "app_name": name,
            "error": f"An app named {name} already exists and belongs to "
            f"{app['creator']}; it was left alone.",
            "hint": "Set deployment.app_name in the design to another mcp- name.",
        }

    compute = (app.get("compute_status") or {}).get("state", "")
    if compute == "STOPPED":
        _api("POST", f"/api/2.0/apps/{name}/start")
        return {"status": "starting", "app_name": name, "next_step": again}
    if compute != "ACTIVE":
        return {
            "status": "creating" if compute in ("", "STARTING") else "starting",
            "app_name": name,
            "compute": compute,
            "next_step": again,
        }

    pending = app.get("pending_deployment") or {}
    pending_state = (pending.get("status") or {}).get("state")
    if pending_state == "IN_PROGRESS":
        return {"status": "deploying", "app_name": name, "next_step": again}
    if pending_state == "FAILED" and not redeploy:
        return {
            "status": "failed",
            "app_name": name,
            "error": (pending.get("status") or {}).get("message", "Deploy failed."),
            "hint": (
                "Open the app's Logs tab, fix the files in the source folder, "
                "then call again with redeploy=true."
            ),
        }

    active = app.get("active_deployment") or {}
    deployed_here = active.get("source_code_path", "").rstrip("/") == source.rstrip("/")
    succeeded = (active.get("status") or {}).get("state") == "SUCCEEDED"
    if deployed_here and succeeded and not redeploy:
        url = app.get("url", "")
        return {
            "status": "live",
            "app_name": name,
            "url": url,
            "mcp_url": f"{url}/mcp" if url else "",
            "app_state": (app.get("app_status") or {}).get("state", ""),
            "connect": (
                "In Genie Code open Settings > Connectors > Custom MCP Server, "
                f"choose {name}, then save."
            ),
            "redeploy": "After editing the files, call again with redeploy=true.",
        }

    # Keep the app description current (it carries the MCP A Creator
    # annotation). PATCH replaces the whole object, so always send the
    # description and the resources together, or one wipes the other.
    if app.get("description", "") != plan["description"]:
        _api(
            "PATCH",
            f"/api/2.0/apps/{name}",
            {
                "name": name,
                "description": plan["description"],
                "resources": plan["resources"] or app.get("resources", []),
            },
        )

    deployment = _api(
        "POST",
        f"/api/2.0/apps/{name}/deployments",
        {"source_code_path": source, "mode": "SNAPSHOT"},
    )
    return {
        "status": "deploying",
        "app_name": name,
        "deployment_id": (deployment or {}).get("deployment_id", ""),
        "next_step": again,
    }
