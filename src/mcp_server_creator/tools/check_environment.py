"""Can this environment run what we are about to design?

Phase 0, and the first gate. Databricks is not one platform: a commercial
workspace, a FedRAMP High GovCloud workspace and an IL5 GovCloud DoD
workspace differ in which features exist at all. A design validated against
the commercial feature set can fail on deployment in a restricted one for
reasons no code review would catch.

This reports what it can actually probe, falls back to published feature
availability for the named environment, and says plainly which of the two a
given answer came from.
"""

from __future__ import annotations

import shutil
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from mcp_server_creator import environments, live
from mcp_server_creator.config import settings
from mcp_server_creator.designs import store_mode


def register(mcp: FastMCP) -> None:
    """Register the environment gate with the MCP server."""

    @mcp.tool(
        name="creator_check_environment",
        annotations={
            "title": "Check what this Databricks environment supports",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    def creator_check_environment(
        environment: Annotated[
            environments.EnvironmentProfile,
            Field(
                description=(
                    "Which environment the generated server will run in. "
                    "Pick 'gov_il5' for GovCloud DoD, 'gov_fedramp_high' for "
                    "GovCloud, 'commercial' for a normal workspace, or "
                    "'unknown' to get the conservative answer."
                )
            ),
        ] = environments.EnvironmentProfile.UNKNOWN,
        workspace_host: Annotated[
            str,
            Field(
                description=(
                    "Workspace URL, if you want it recorded. Probing uses "
                    "the credentials this server is configured with, not "
                    "this value."
                )
            ),
        ] = "",
    ) -> dict[str, Any]:
        """Report what this environment supports, and what that rules out.

        Run this before designing anything. It answers the three questions
        that decide whether a server can exist here: which data sources are
        reachable, how retrieval has to work, and how anything that needs a
        model is allowed to call one.

        Returns: `capabilities` per feature as supported, unsupported or
        unknown; `source_kinds` split into usable, verify_first and blocked;
        `retrieval`, `state` and `model_access` guidance; `probe` showing
        what was checked live rather than read from the table; and
        `next_step`.
        """
        probe: dict[str, Any] = {
            "attempted": False,
            "databricks_cli": shutil.which("databricks") is not None,
            "live_table_inspection": live.available(),
            "warehouse_configured": bool(settings.databricks_warehouse_id),
            "host_configured": bool(settings.databricks_host),
            "design_store": store_mode(),
        }
        if live.available():
            probe["attempted"] = True
            probe["note"] = (
                "Credentials and a warehouse are configured, so "
                "creator_add_data_source can read a real table's schema, "
                "row count and distinct values instead of parsing pasted "
                "text."
            )
        else:
            probe["note"] = (
                "No live inspection from here. That is normal and costs "
                "nothing: paste a CREATE TABLE statement, a CSV header with "
                "a few rows, or DESCRIBE output instead."
            )

        fit = environments.source_kind_fit(environment)
        blocking = [f"'{kind}' sources cannot work here" for kind in fit["blocked"]]

        return {
            "environment": environment.value,
            "workspace_host": workspace_host.strip(),
            "source_of_truth": (
                "Published Databricks feature availability for this "
                "environment, plus whatever the probe below could confirm. "
                "Anything Databricks does not publish for an environment is "
                "reported as unknown rather than guessed."
            ),
            "capabilities": environments.capabilities(environment),
            "source_kinds": fit,
            "retrieval": environments.retrieval_guidance(environment),
            "state": environments.state_guidance(environment),
            "model_access": environments.model_access_guidance(environment),
            "notes": environments.notes(environment),
            "blocking": blocking,
            "probe": probe,
            "next_step": (
                "Pass this environment to creator_start_design so the "
                "designer only proposes sources that work here, and the "
                "review flags the ones that do not."
            ),
        }
