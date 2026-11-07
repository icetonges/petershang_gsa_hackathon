"""Turn an intent into a validated specification, and name what is missing.

Phase 1. Pass whatever the user has said. The tool returns a short handle
plus the specification it could build, and the specific gaps that still
block generation, each with the question that would fill it.

Every parameter here is a flat scalar, an enum or a list of strings. Nothing
nested, and nothing nullable, so the schema survives any model and any
gateway that forwards it to a provider with a narrower schema subset.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field, ValidationError

from mcp_server_creator import designs, environments
from mcp_server_creator.designer import analyze_gaps
from mcp_server_creator.models import (
    DataClassification,
    DeploymentPlan,
    DeploymentTarget,
    ServerSpec,
    Severity,
)
from mcp_server_creator.utils import derive_server_name, slugify


def register(mcp: FastMCP) -> None:
    """Register the design intake tool with the MCP server."""

    @mcp.tool(
        name="creator_start_design",
        annotations={
            "title": "Start a server design",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    def creator_start_design(
        purpose: Annotated[
            str,
            Field(
                description=("One sentence: what this server should let an agent do.")
            ),
        ],
        primary_user: Annotated[
            str,
            Field(
                description=(
                    "The one person whose job gets easier, e.g. 'a budget "
                    "analyst in a component comptroller shop'. A server "
                    "built for everyone is built for nobody."
                )
            ),
        ] = "",
        jobs_to_be_done: Annotated[
            list[str],
            Field(
                description=(
                    "Three to eight questions that person asks today, in "
                    "their own words. These become tool names and golden "
                    "questions, so do not paraphrase them into jargon."
                )
            ),
        ] = [],
        out_of_scope: Annotated[
            list[str],
            Field(
                description=(
                    "Questions the server must refuse. Scope refusal is a "
                    "feature and it is measurable."
                )
            ),
        ] = [],
        environment: Annotated[
            environments.EnvironmentProfile,
            Field(
                description=(
                    "Which Databricks environment this will run in. Run "
                    "creator_check_environment first if you are unsure: a "
                    "design valid in a commercial workspace can fail to "
                    "deploy in an IL5 one."
                )
            ),
        ] = environments.EnvironmentProfile.UNKNOWN,
        server_name: Annotated[
            str,
            Field(
                description=(
                    "Slug for the server, e.g. 'contract-triage-mcp'. "
                    "Derived from the purpose when left empty."
                )
            ),
        ] = "",
        domain: Annotated[
            str,
            Field(description="Subject area, e.g. 'federal budget execution'."),
        ] = "",
        workspace_host: Annotated[
            str, Field(description="Databricks workspace URL, if known.")
        ] = "",
        classification: Annotated[
            DataClassification,
            Field(description="Sensitivity of the data the server will expose."),
        ] = DataClassification.PUBLIC,
        deployment_target: Annotated[
            DeploymentTarget,
            Field(description="Where the generated server will run."),
        ] = DeploymentTarget.DATABRICKS_APPS,
        maintainer: Annotated[str, Field(description="Who will own the server.")] = "",
        repo_url: Annotated[
            str, Field(description="Repository URL, if one exists yet.")
        ] = "",
    ) -> dict[str, Any]:
        """Build a draft specification and list what still blocks generation.

        Call this first, with whatever the user has already said. Do not
        invent answers: anything you leave out comes back as a gap with the
        question to ask, which is more useful than a confident guess.

        Returns: `design_id`, the handle to pass to every later call; `spec`,
        the document itself in case you would rather hold it; `blocking_gaps`
        and `gaps` with the question that fills each; `derived` showing the
        names chosen for you; `environment` with the constraints that apply;
        and `handle_advice` on which input to pass back.
        """
        name = slugify(server_name) if server_name else derive_server_name(purpose)
        try:
            spec = ServerSpec(
                server_name=name,
                purpose=purpose.strip(),
                primary_user=primary_user.strip(),
                jobs_to_be_done=[j.strip() for j in jobs_to_be_done if j.strip()],
                out_of_scope=[s.strip() for s in out_of_scope if s.strip()],
                domain=domain.strip(),
                environment=environment,
                classification=classification,
                maintainer=maintainer.strip(),
                repo_url=repo_url.strip(),
                deployment=DeploymentPlan(
                    target=deployment_target,
                    workspace_host=workspace_host.strip(),
                ),
            )
        except ValidationError as error:
            return {
                "error": "The specification is not valid.",
                "details": error.errors(include_url=False),
                "hint": (
                    "server_name must be a lowercase slug of 3 to 40 "
                    "characters, e.g. 'contract-triage-mcp'."
                ),
            }

        design_id = designs.save(spec)
        gaps = analyze_gaps(spec)
        blocking = [
            gap.model_dump()
            for gap in gaps
            if gap.severity in {Severity.BLOCKER, Severity.HIGH}
        ]
        fit = environments.source_kind_fit(environment)
        multi_replica = (
            environments.availability(environment, "apps_multi_replica")
            is not environments.Availability.UNSUPPORTED
        )

        return {
            "design_id": design_id,
            "spec": spec.model_dump(mode="json"),
            "blocking_gaps": blocking,
            "gaps": [gap.model_dump() for gap in gaps],
            "derived": {
                "server_name": spec.server_name,
                "package_name": spec.package_name,
                "title": spec.title,
                "tool_prefix": spec.tool_prefix,
                "app_name": spec.deployment.app_name,
                "dataset_name": spec.evaluation.dataset_name,
            },
            "environment": {
                "profile": environment.value,
                "source_kinds_usable": fit["usable"],
                "source_kinds_verify_first": fit["verify_first"],
                "source_kinds_blocked": fit["blocked"],
                "notes": environments.notes(environment),
            },
            "handle_advice": designs.handle_advice(multi_replica),
            "next_step": (
                "Ask the user the blocking gap questions, then call "
                "creator_add_data_source once per source with this "
                "design_id."
            ),
        }
