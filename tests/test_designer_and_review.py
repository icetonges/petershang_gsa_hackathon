"""The designer proposes only supportable tools; the rubric catches problems."""

from __future__ import annotations

from mcp_server_creator.designer import analyze_gaps, design_tools
from mcp_server_creator.models import (
    DataModality,
    DataSourceProfile,
    FieldProfile,
    FieldRole,
    QueryPattern,
    ServerSpec,
    Severity,
    SourceKind,
)
from mcp_server_creator.review import review_spec


def test_designer_covers_every_source(sample_spec):
    plans, _ = design_tools(sample_spec)
    sources = {plan.source for plan in plans}
    assert sources == {"contracts", "policy_docs", "offices"}


def test_designer_always_proposes_a_dictionary_tool(sample_spec):
    plans, _ = design_tools(sample_spec)
    patterns = {plan.pattern for plan in plans}
    assert QueryPattern.DESCRIBE_SOURCE in patterns


def test_designer_proposes_retrieval_for_an_index(sample_spec):
    plans, _ = design_tools(sample_spec)
    retrieval = [plan for plan in plans if plan.pattern is QueryPattern.SEMANTIC_SEARCH]
    assert len(retrieval) == 1
    assert retrieval[0].source == "policy_docs"


def test_designer_names_tools_with_the_server_prefix(sample_spec):
    plans, _ = design_tools(sample_spec)
    assert all(plan.name.startswith(sample_spec.tool_prefix) for plan in plans)


def test_designer_turns_low_cardinality_columns_into_enums(sample_spec):
    plans, _ = design_tools(sample_spec)
    office_params = [
        param
        for plan in plans
        for param in plan.params
        if param.source_field == "office"
    ]
    assert office_params
    assert all(param.enum_values for param in office_params)


def test_designer_refuses_to_invent_unsupported_tools():
    """No measure and no date means no totals and no trend, and it says so."""
    spec = ServerSpec(
        server_name="thin-mcp",
        purpose="x",
        data_sources=[
            DataSourceProfile(
                name="labels",
                kind=SourceKind.LOCAL_SNAPSHOT,
                modality=DataModality.STRUCTURED,
                snapshot_path="labels.csv",
                fields=[
                    FieldProfile(name="label", data_type="string", role=FieldRole.TEXT)
                ],
            )
        ],
    )
    plans, notes = design_tools(spec)
    patterns = {plan.pattern for plan in plans}
    assert QueryPattern.AGGREGATE not in patterns
    assert QueryPattern.TREND not in patterns
    joined = " ".join(notes)
    assert "no numeric measure" in joined
    assert "no date field" in joined


def test_designer_seeds_evaluation_questions_from_the_user(sample_spec):
    plans, _ = design_tools(sample_spec)
    seeded = [seed for plan in plans for seed in plan.eval_seeds]
    assert sample_spec.jobs_to_be_done[0] in seeded


def test_gaps_block_on_a_source_with_no_fields():
    spec = ServerSpec(
        server_name="empty-mcp",
        purpose="x",
        jobs_to_be_done=["a", "b", "c"],
        data_sources=[
            DataSourceProfile(
                name="mystery",
                kind=SourceKind.LOCAL_SNAPSHOT,
                modality=DataModality.STRUCTURED,
                snapshot_path="mystery.csv",
            )
        ],
    )
    gaps = analyze_gaps(spec)
    blocking = [gap for gap in gaps if gap.severity is Severity.BLOCKER]
    assert any("fields" in gap.field for gap in blocking)
    assert all(gap.question for gap in gaps)


def test_gaps_block_on_missing_user_questions():
    spec = ServerSpec(server_name="vague-mcp", purpose="x")
    fields = {gap.field for gap in analyze_gaps(spec)}
    assert "jobs_to_be_done" in fields
    assert "data_sources" in fields


def test_review_of_a_designed_spec_is_clean_enough_to_build(designed_spec):
    report = review_spec(designed_spec)
    assert not report.blockers, [f.summary for f in report.blockers]
    assert report.scores["security_posture"] >= 4
    assert report.scores["goal_orientation"] >= 4


def test_review_flags_a_missing_baseline(designed_spec):
    designed_spec.evaluation.baseline_without_tools = False
    report = review_spec(designed_spec)
    checks = {finding.check for finding in report.findings}
    assert "evaluation_ingredients" in checks


def test_review_flags_a_list_tool_that_does_not_bound_its_results(
    designed_spec,
):
    for plan in designed_spec.tools:
        if plan.pattern is QueryPattern.SEARCH:
            plan.paginated = False
            for param in plan.params:
                param.maximum = None
    report = review_spec(designed_spec)
    checks = {finding.check for finding in report.findings}
    assert "list_tools_bound_their_results" in checks


def test_review_accepts_a_capped_count_instead_of_pagination(designed_spec):
    """Similarity search bounds with max_passages; an offset means little."""
    report = review_spec(designed_spec)
    checks = {finding.check for finding in report.findings}
    assert "list_tools_bound_their_results" not in checks


def test_review_flags_a_read_only_claim_contradicted_by_a_write_tool(
    designed_spec,
):
    designed_spec.tools[0].read_only = False
    report = review_spec(designed_spec)
    checks = {finding.check for finding in report.findings}
    assert "read_only_consistent" in checks


def test_review_flags_pii_marked_public(designed_spec):
    designed_spec.pii_present = True
    report = review_spec(designed_spec)
    checks = {finding.check for finding in report.findings}
    assert "classification_consistent" in checks


def test_review_rewards_a_keyless_path(designed_spec):
    report = review_spec(designed_spec)
    assert "keyless_path_available" in report.passed_checks


def test_review_every_finding_carries_a_fix(designed_spec):
    designed_spec.out_of_scope = []
    designed_spec.evaluation.repeats = 1
    report = review_spec(designed_spec)
    assert report.findings
    assert all(finding.fix for finding in report.findings)
