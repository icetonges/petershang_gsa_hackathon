"""Environment profiles, design handles, and the environment-fit review.

The facts asserted here are from Databricks' published feature availability,
not from inference. If Databricks changes what IL5 supports, these tests
should fail and the table should be updated.
"""

from __future__ import annotations

import json

import pytest

from mcp_server_creator import designs, environments
from mcp_server_creator.config import settings
from mcp_server_creator.environments import Availability, EnvironmentProfile
from mcp_server_creator.models import ServerSpec
from mcp_server_creator.review import review_spec


def test_il5_lacks_storage_optimized_and_full_text_search():
    """The two gaps that make commercial retrieval advice wrong there."""
    profile = EnvironmentProfile.GOV_IL5
    assert (
        environments.availability(profile, "vector_search_storage_optimized")
        is Availability.UNSUPPORTED
    )
    assert (
        environments.availability(profile, "vector_search_full_text")
        is Availability.UNSUPPORTED
    )


def test_il5_has_apps_genie_and_standard_vector_search():
    profile = EnvironmentProfile.GOV_IL5
    for capability in (
        "databricks_apps",
        "apps_multi_replica",
        "genie_spaces",
        "genie_one",
        "vector_search_standard",
    ):
        assert (
            environments.availability(profile, capability) is Availability.SUPPORTED
        ), capability


def test_il5_cannot_reach_public_model_providers():
    assert (
        environments.availability(EnvironmentProfile.GOV_IL5, "public_model_providers")
        is Availability.UNSUPPORTED
    )


def test_unknown_environment_assumes_nothing():
    matrix = environments.capabilities(EnvironmentProfile.UNKNOWN)
    assert set(matrix.values()) == {"unknown"}


def test_unknown_environment_only_recommends_credential_free_sources():
    fit = environments.source_kind_fit(EnvironmentProfile.UNKNOWN)
    assert set(fit["usable"]) == {"rest_api", "local_snapshot", "document_corpus"}
    assert "uc_table" in fit["verify_first"]


def test_retrieval_guidance_sends_il5_to_a_sql_keyword_path():
    guidance = environments.retrieval_guidance(EnvironmentProfile.GOV_IL5)
    assert guidance["endpoint_type"] == "STANDARD"
    assert "SQL LIKE" in guidance["keyword_path"]


def test_retrieval_guidance_allows_bm25_commercially():
    guidance = environments.retrieval_guidance(EnvironmentProfile.COMMERCIAL)
    assert "BM25" in guidance["keyword_path"]


def test_model_access_guidance_is_endpoint_based_everywhere():
    for profile in (EnvironmentProfile.COMMERCIAL, EnvironmentProfile.GOV_IL5):
        guidance = environments.model_access_guidance(profile)
        assert "/serving-endpoints" in guidance["pattern"]
    restricted = environments.model_access_guidance(EnvironmentProfile.GOV_IL5)
    assert "not reachable" in restricted["egress"]


def test_state_guidance_warns_when_apps_scale_horizontally():
    assert "replica" in environments.state_guidance(EnvironmentProfile.GOV_IL5)


def test_check_design_flags_an_unpublished_capability():
    findings = environments.check_design(
        EnvironmentProfile.GOV_IL5, ["uc_function"], uses_in_process_state=False
    )
    assert findings
    assert any(f.capability == "uc_functions" for f in findings)
    assert all(f.fix for f in findings)


def test_check_design_flags_in_process_state():
    findings = environments.check_design(
        EnvironmentProfile.GOV_IL5, ["local_snapshot"], uses_in_process_state=True
    )
    assert any(f.subject == "state" for f in findings)


def test_a_handle_round_trips_and_is_short():
    handle = designs.save(ServerSpec(server_name="handle-mcp", purpose="x"))
    assert handle.startswith("dsn_")
    assert len(handle) <= 16
    assert designs.load(handle).server_name == "handle-mcp"


def test_resolve_accepts_inline_json_for_a_stateless_deployment():
    spec = ServerSpec(server_name="inline-mcp", purpose="x")
    payload = json.dumps(spec.model_dump(mode="json"))
    resolved, source = designs.resolve("", payload)
    assert source == "spec_json"
    assert resolved.server_name == "inline-mcp"


def test_resolve_refuses_both_inputs_at_once():
    with pytest.raises(designs.DesignError):
        designs.resolve("dsn_00000000", '{"server_name": "x-mcp"}')


def test_resolve_with_neither_input_says_what_to_pass():
    with pytest.raises(designs.DesignError) as error:
        designs.resolve("", "")
    assert "design_id" in str(error.value)


def test_an_unknown_handle_explains_why_it_might_be_missing():
    with pytest.raises(designs.DesignError) as error:
        designs.load("dsn_deadbeef")
    message = str(error.value)
    assert "replica" in message or "restart" in message


def test_malformed_json_is_reported_precisely():
    spec, source, failure = designs.resolve_result("", "{not json")
    assert spec is None
    assert failure is not None
    assert "spec_json" in failure["error"]


def test_the_file_store_survives_across_replicas(tmp_path, monkeypatch):
    """A directory every replica can read is the deployable configuration."""
    monkeypatch.setattr(settings, "creator_design_store", "file")
    monkeypatch.setattr(settings, "creator_design_dir", str(tmp_path))
    assert designs.store_mode() == "file"
    assert designs.is_durable() is True

    handle = designs.save(ServerSpec(server_name="durable-mcp", purpose="x"))
    assert (tmp_path / f"{handle}.json").is_file()
    assert designs.load(handle).server_name == "durable-mcp"
    assert designs.forget(handle) is True


def test_the_file_store_refuses_a_malformed_handle(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "creator_design_store", "file")
    monkeypatch.setattr(settings, "creator_design_dir", str(tmp_path))
    for attempt in ("../escape", "dsn_../x", "notahandle"):
        with pytest.raises(designs.DesignError):
            designs.load(attempt)


def test_handle_advice_warns_about_memory_plus_multiple_replicas():
    advice = designs.handle_advice(multi_replica=True)
    assert "spec_json" in advice


def test_review_scores_environment_fit(designed_spec):
    report = review_spec(designed_spec)
    assert "environment_fit" in report.scores


def test_review_flags_an_undeclared_environment(designed_spec):
    designed_spec.environment = EnvironmentProfile.UNKNOWN
    report = review_spec(designed_spec)
    checks = {finding.check for finding in report.findings}
    assert "environment_declared" in checks


def test_review_flags_the_missing_keyword_path_in_il5(designed_spec):
    """The sample spec has an unstructured source; IL5 has no BM25 index."""
    designed_spec.environment = EnvironmentProfile.GOV_IL5
    report = review_spec(designed_spec)
    checks = {finding.check for finding in report.findings}
    assert "keyword_retrieval_path" in checks


def test_review_accepts_a_declared_commercial_environment(designed_spec):
    designed_spec.environment = EnvironmentProfile.COMMERCIAL
    report = review_spec(designed_spec)
    assert "environment_declared" in report.passed_checks


def test_the_doctor_runs_and_reports_ready(capsys):
    from mcp_server_creator.doctor import main

    assert main([]) == 0
    output = capsys.readouterr().out
    assert "preflight" in output
    assert "Ready" in output


def test_the_doctor_reports_il5_constraints(capsys):
    from mcp_server_creator.doctor import main

    assert main(["--environment", "gov_il5"]) == 0
    output = capsys.readouterr().out
    assert "gov_il5" in output
    assert "Full-Text Search" in output
