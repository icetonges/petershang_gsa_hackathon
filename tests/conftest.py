"""Shared fixtures: specifications that exercise the generators.

The sample spec deliberately mixes a governed table, an unstructured index,
and a packaged snapshot, because that combination is the common real case
and the one most likely to break a generator.
"""

from __future__ import annotations

import os

import pytest
from fastmcp import FastMCP

from mcp_server_creator.models import (
    DataClassification,
    DataModality,
    DataSourceProfile,
    DeploymentPlan,
    FieldProfile,
    FieldRole,
    RetrievalProfile,
    ServerSpec,
    SourceKind,
)

# Tests must never log real traces. mlflow.set_experiment() exports
# MLFLOW_EXPERIMENT_ID, so a test run started from a notebook would otherwise
# send every tool call to that experiment.
os.environ.pop("MLFLOW_EXPERIMENT_ID", None)

# FastMCP 3 and later expose list_tools() and list_prompts(), returning lists.
# FastMCP 2.x (pinned for Databricks Genie Code compatibility) exposes
# get_tools() and get_prompts(), returning dicts keyed by name. The tests use
# the newer names, so add them when the installed FastMCP is 2.x.
if not hasattr(FastMCP, "list_tools"):

    async def _list_tools(self):
        return list((await self.get_tools()).values())

    FastMCP.list_tools = _list_tools

if not hasattr(FastMCP, "list_prompts"):

    async def _list_prompts(self):
        return list((await self.get_prompts()).values())

    FastMCP.list_prompts = _list_prompts


@pytest.fixture
def contracts_table() -> DataSourceProfile:
    """A governed Unity Catalog table with every field role represented."""
    return DataSourceProfile(
        name="contracts",
        kind=SourceKind.UC_TABLE,
        modality=DataModality.STRUCTURED,
        title="Contract awards",
        catalog="main",
        schema_name="acquisition",
        table="contract_awards",
        grain="one contract award, not one modification",
        update_cadence="monthly, about a 32-day lag",
        as_of="2026-09-30",
        row_count_hint=48213,
        required_grants=["SELECT on main.acquisition.contract_awards"],
        fields=[
            FieldProfile(
                name="contract_id",
                data_type="string",
                role=FieldRole.IDENTIFIER,
                description="Award identifier.",
                example="47QFLA20C0002",
                nullable=False,
            ),
            FieldProfile(
                name="office",
                data_type="string",
                role=FieldRole.DIMENSION,
                description="Contracting office that owns the award.",
                cardinality_hint=12,
                enum_values=["GSA FAS AAS REGION 5", "GSA PBS REGION 5"],
            ),
            FieldProfile(
                name="vendor_name",
                data_type="string",
                role=FieldRole.TEXT,
                description="Recipient name as awarded.",
            ),
            FieldProfile(
                name="obligated_amount",
                data_type="double",
                role=FieldRole.MEASURE,
                description="Dollars obligated to date.",
            ),
            FieldProfile(
                name="period_end_date",
                data_type="date",
                role=FieldRole.DATE,
                description="End of the period of performance.",
            ),
        ],
    )


@pytest.fixture
def policy_index() -> DataSourceProfile:
    """An AI Search index over chunked policy documents."""
    return DataSourceProfile(
        name="policy_docs",
        kind=SourceKind.AI_SEARCH_INDEX,
        modality=DataModality.UNSTRUCTURED,
        title="Acquisition policy",
        index_name="main.acquisition.policy_chunks_index",
        grain="one chunk of one policy document",
        update_cadence="weekly",
        as_of="2026-10-31",
        retrieval=RetrievalProfile(
            endpoint_name="policy-endpoint",
            primary_key="chunk_id",
            text_column="content",
            citation_columns=["doc_title", "section", "page", "effective_date"],
        ),
        fields=[
            FieldProfile(
                name="chunk_id",
                data_type="string",
                role=FieldRole.IDENTIFIER,
                nullable=False,
            ),
            FieldProfile(name="doc_title", data_type="string", role=FieldRole.METADATA),
            FieldProfile(name="section", data_type="string", role=FieldRole.METADATA),
            FieldProfile(name="page", data_type="int", role=FieldRole.METADATA),
            FieldProfile(name="effective_date", data_type="date", role=FieldRole.DATE),
            FieldProfile(
                name="content",
                data_type="string",
                role=FieldRole.TEXT,
                nullable=False,
            ),
        ],
    )


@pytest.fixture
def reference_snapshot() -> DataSourceProfile:
    """A small packaged snapshot: the keyless path a reviewer can run."""
    return DataSourceProfile(
        name="offices",
        kind=SourceKind.LOCAL_SNAPSHOT,
        modality=DataModality.STRUCTURED,
        title="Contracting offices",
        snapshot_path="offices.csv",
        grain="one contracting office",
        update_cadence="annually",
        as_of="2026-01-01",
        fields=[
            FieldProfile(
                name="office_code",
                data_type="string",
                role=FieldRole.IDENTIFIER,
                nullable=False,
            ),
            FieldProfile(name="office_name", data_type="string", role=FieldRole.TEXT),
            FieldProfile(
                name="region",
                data_type="string",
                role=FieldRole.DIMENSION,
                cardinality_hint=11,
                enum_values=["R1", "R5", "R9"],
            ),
            FieldProfile(name="staff_count", data_type="int", role=FieldRole.MEASURE),
            FieldProfile(name="as_of_date", data_type="date", role=FieldRole.DATE),
        ],
    )


@pytest.fixture
def sample_spec(
    contracts_table: DataSourceProfile,
    policy_index: DataSourceProfile,
    reference_snapshot: DataSourceProfile,
) -> ServerSpec:
    """A realistic specification, before tools are designed."""
    return ServerSpec(
        server_name="contract-triage-mcp",
        purpose=(
            "Let a contracting officer see which awards need action and what "
            "the policy says about them."
        ),
        primary_user="a contracting officer with a shrinking staff",
        jobs_to_be_done=[
            "Which of my contracts expire this quarter?",
            "Which are past their end date with money still on them?",
            "How dependent is my office on one vendor?",
            "What does the rule say about exercising an option?",
        ],
        out_of_scope=[
            "Legal advice on a specific procurement",
            "Anything about a contract not in the public data",
            "Predicting whether a protest will succeed",
        ],
        domain="federal acquisition",
        classification=DataClassification.PUBLIC,
        data_sources=[contracts_table, policy_index, reference_snapshot],
        deployment=DeploymentPlan(
            workspace_host="https://dbc-aae0ba16-8727.cloud.databricks.com",
        ),
        repo_url="https://github.com/icetonges/contract-triage-mcp",
    )


@pytest.fixture
def designed_spec(sample_spec: ServerSpec) -> ServerSpec:
    """The sample specification with a designed tool surface."""
    from mcp_server_creator.designer import design_tools

    plans, _ = design_tools(sample_spec)
    sample_spec.tools = plans
    return sample_spec
