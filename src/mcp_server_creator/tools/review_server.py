"""Review a design against the quality, security and environment rubric.

Phase 6, and worth running before generation too: a blocker found in the
design costs a minute, and the same blocker found after deployment costs an
afternoon.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from mcp_server_creator import designs
from mcp_server_creator.models import Severity
from mcp_server_creator.review import review_spec


def register(mcp: FastMCP) -> None:
    """Register the design review tool with the MCP server."""

    @mcp.tool(
        name="creator_review_server",
        annotations={
            "title": "Review a server design",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def creator_review_server(
        design_id: Annotated[
            str, Field(description=designs.DESIGN_ID_DESCRIPTION)
        ] = "",
        spec_json: Annotated[
            str, Field(description=designs.SPEC_JSON_DESCRIPTION)
        ] = "",
    ) -> dict[str, Any]:
        """Score a design and return findings with concrete fixes.

        Eleven dimensions, each scored 0 to 5: goal orientation, input
        design, documentation, context economy, error quality, security
        posture, evaluation, reproducibility, deployment, environment fit and
        honesty. Below 3 on any dimension is worth fixing before you build.

        Returns: `verdict`, `scores` per dimension, `blockers` to fix first,
        `findings` each with a fix, and `passed_checks` so you can see what
        the design already gets right.
        """
        spec, _, failure = designs.resolve_result(design_id, spec_json)
        if failure:
            return failure
        assert spec is not None

        report = review_spec(spec)
        ranking = {
            Severity.BLOCKER: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
            Severity.INFO: 4,
        }
        ordered = sorted(report.findings, key=lambda f: ranking[f.severity])
        weakest = sorted(report.scores.items(), key=lambda item: item[1])[:3]
        return {
            "design_id": design_id or "",
            "subject": report.subject,
            "verdict": report.verdict,
            "scores": report.scores,
            "weakest_dimensions": [name for name, _ in weakest],
            "blockers": [f.model_dump() for f in report.blockers],
            "findings": [f.model_dump() for f in ordered],
            "passed_checks": report.passed_checks,
            "next_step": (
                "Fix every blocker, then the high-severity findings, then "
                "re-run this review before generating."
            ),
        }
