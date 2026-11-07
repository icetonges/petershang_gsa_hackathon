"""Specification validation, and turning pasted schemas into field profiles."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_server_creator.models import (
    DataModality,
    DataSourceProfile,
    DeploymentPlan,
    FileManifest,
    ServerSpec,
    SourceKind,
)
from mcp_server_creator.profiling import (
    parse_create_table_ddl,
    parse_csv_sample,
    parse_json_sample,
    parse_tabular_text,
    profile_summary,
)


def test_spec_derives_names_and_app_name():
    spec = ServerSpec(server_name="contract-triage", purpose="x")
    assert spec.package_name == "contract_triage"
    assert spec.title == "Contract Triage"
    assert spec.tool_prefix == "contract"
    assert spec.deployment.app_name == "mcp-contract-triage"


def test_spec_rejects_a_bad_slug():
    with pytest.raises(ValidationError):
        ServerSpec(server_name="Not A Slug!", purpose="x")


def test_app_name_without_the_mcp_prefix_is_rejected():
    """Without the prefix the app deploys and is never offered as an MCP."""
    with pytest.raises(ValidationError):
        DeploymentPlan(app_name="contract-triage-app")


def test_uc_table_requires_full_coordinates():
    with pytest.raises(ValidationError):
        DataSourceProfile(
            name="t",
            kind=SourceKind.UC_TABLE,
            modality=DataModality.STRUCTURED,
            catalog="main",
        )


def test_unstructured_source_gets_a_retrieval_profile():
    source = DataSourceProfile(
        name="docs",
        kind=SourceKind.DOCUMENT_CORPUS,
        modality=DataModality.UNSTRUCTURED,
    )
    assert source.retrieval is not None


def test_manifest_add_replaces_and_merge_folds():
    first = FileManifest(server_name="s", package_name="p")
    first.add("a.py", "one")
    first.add("a.py", "two")
    assert len(first.files) == 1
    assert first.files[0].content == "two"

    second = FileManifest(server_name="s", package_name="p")
    second.add("b.py", "three")
    second.warnings.append("careful")
    first.merge(second)
    assert first.paths() == ["a.py", "b.py"]
    assert first.warnings == ["careful"]


def test_ddl_parsing_reads_columns_types_and_comments():
    fields = parse_create_table_ddl(
        """
        CREATE TABLE main.acq.awards (
          contract_id STRING NOT NULL COMMENT 'Award id',
          obligated_amount DECIMAL(18,2),
          period_end_date DATE,
          tags ARRAY<STRING>,
          PRIMARY KEY (contract_id)
        ) USING DELTA
        """
    )
    names = [field.name for field in fields]
    assert names == [
        "contract_id",
        "obligated_amount",
        "period_end_date",
        "tags",
    ]
    assert fields[0].description == "Award id"
    assert fields[0].nullable is False
    assert fields[1].role.value == "measure"
    assert fields[2].role.value == "date"


def test_csv_parsing_infers_types_roles_and_enum_candidates():
    fields = parse_csv_sample(
        "contract_id,office,obligated_amount,awarded_date\n"
        "A1,R5,1000.50,2026-01-02\n"
        "A2,R5,2000.00,2026-02-03\n"
        "A3,R9,3000.75,2026-03-04\n"
    )
    by_name = {field.name: field for field in fields}
    assert by_name["contract_id"].role.value == "identifier"
    assert by_name["obligated_amount"].role.value == "measure"
    assert by_name["awarded_date"].role.value == "date"
    assert by_name["office"].enum_values == ["R5", "R9"]


def test_json_parsing_flattens_one_level():
    fields = parse_json_sample(
        '{"results": [{"id": "x", "period": {"end_date": "2026-01-01"}, '
        '"total_obligation": 5}]}'
    )
    names = {field.name for field in fields}
    assert "period.end_date" in names
    assert "total_obligation" in names


def test_tabular_text_detects_describe_output():
    fields = parse_tabular_text(
        "contract_id    string    Award id\n"
        "obligated_amount    double\n"
        "period_end_date    date\n"
    )
    assert [f.name for f in fields] == [
        "contract_id",
        "obligated_amount",
        "period_end_date",
    ]


def test_profile_summary_names_what_is_missing():
    fields = parse_csv_sample("label,note\nalpha,some text\nbeta,other text\n")
    summary = profile_summary(fields)
    joined = " ".join(summary["observations"])
    assert "No measures" in joined
    assert "No date field" in joined


def test_role_inference_does_not_match_hints_as_loose_substrings():
    """`vendor_name` is text, not a date: "vendor" happens to contain "end"."""
    from mcp_server_creator.profiling import infer_role

    assert infer_role("vendor_name", "string").value == "text"
    # "recommendation" contains neither a date nor a text hint as a token,
    # so it stays a plain dimension rather than being guessed at.
    assert infer_role("recommendation", "string").value == "dimension"
    assert infer_role("attendee_label", "string").value != "date"
    assert infer_role("appendix_label", "string").value != "date"
    assert infer_role("period_end_date", "date").value == "date"
    assert infer_role("end_date", "string").value == "date"
    assert infer_role("contract_id", "string").value == "identifier"
    assert infer_role("obligated_amount", "double").value == "measure"
    assert infer_role("fiscal_year", "int").value == "date"
    assert infer_role("is_active", "boolean").value == "flag"


def test_derived_server_name_leaves_room_for_the_app_prefix():
    """A slugified sentence produced an app name the platform rejects."""
    from mcp_server_creator.utils import derive_server_name

    purpose = (
        "Let a contracting officer see which awards need action and what "
        "the policy says about them."
    )
    name = derive_server_name(purpose)
    assert len(name) <= 40
    spec = ServerSpec(server_name=name, purpose=purpose)
    assert len(spec.deployment.app_name) <= 50
    assert spec.deployment.app_name.startswith("mcp-")


def test_a_long_purpose_still_yields_a_valid_spec():
    from mcp_server_creator.utils import derive_server_name

    purpose = " ".join(["deliver extremely thorough analytics"] * 12)
    spec = ServerSpec(server_name=derive_server_name(purpose), purpose=purpose)
    assert spec.package_name.isidentifier()


def test_app_name_does_not_repeat_the_mcp_marker():
    """ "mcp-contract-triage-mcp" reads like a mistake, because it is one."""
    spec = ServerSpec(server_name="contract-triage-mcp", purpose="x")
    assert spec.deployment.app_name == "mcp-contract-triage"


def test_an_explicit_app_name_is_respected():
    spec = ServerSpec(
        server_name="contract-triage-mcp",
        purpose="x",
        deployment=DeploymentPlan(app_name="mcp-my-chosen-name"),
    )
    assert spec.deployment.app_name == "mcp-my-chosen-name"
