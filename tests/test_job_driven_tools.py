"""Regression tests: ID columns, PII, and tools built from the user's questions."""

from __future__ import annotations

from mcp_server_creator.designer import design_tools
from mcp_server_creator.generators.toolcode import tool_module
from mcp_server_creator.models import (
    DataModality,
    DataSourceProfile,
    FieldProfile,
    FieldRole,
    ServerSpec,
    SourceKind,
)
from mcp_server_creator.profiling import infer_role, looks_like_pii

BAKERY_COLUMNS = [
    ("transactionID", "bigint", None),
    ("customerID", "bigint", None),
    ("franchiseID", "bigint", 48),
    ("dateTime", "timestamp", None),
    ("product", "string", 6),
    ("quantity", "bigint", None),
    ("unitPrice", "bigint", None),
    ("totalPrice", "bigint", None),
    ("paymentMethod", "string", 3),
    ("cardNumber", "bigint", None),
]
BAKERY_JOBS = [
    "What was total revenue by franchise last month?",
    "Which products sold the most units this week?",
    "Show daily sales for franchise 3000003 between two dates",
    "Which payment methods do customers use most?",
]


def _bakery() -> ServerSpec:
    source = DataSourceProfile(
        name="sales_transactions",
        kind=SourceKind.UC_TABLE,
        modality=DataModality.STRUCTURED,
        catalog="samples",
        schema_name="bakehouse",
        table="sales_transactions",
        grain="one row per sales transaction",
        fields=[
            FieldProfile(
                name=n,
                data_type=ty,
                role=infer_role(n, ty),
                pii=looks_like_pii(n),
                cardinality_hint=c,
            )
            for n, ty, c in BAKERY_COLUMNS
        ],
    )
    return ServerSpec(
        server_name="bakery-mcp",
        tool_prefix="bakery",
        purpose="Answer bakery sales questions for a franchise manager.",
        jobs_to_be_done=BAKERY_JOBS,
        data_sources=[source],
    )


def test_numeric_ids_are_identifiers_not_measures() -> None:
    for name in ("transactionID", "customerID", "franchiseID", "cardNumber"):
        assert infer_role(name, "bigint") is FieldRole.IDENTIFIER, name
    for name in ("quantity", "totalPrice", "fare_amount", "unit_count"):
        assert infer_role(name, "double") is FieldRole.MEASURE, name


def test_card_number_is_pii_and_card_type_is_not() -> None:
    assert looks_like_pii("cardNumber")
    assert looks_like_pii("customer_email")
    assert not looks_like_pii("card_type")


def test_every_job_gets_a_tool_with_the_right_columns() -> None:
    plans, _ = design_tools(_bakery())
    by_goal = {p.goal: p for p in plans}
    revenue = by_goal[BAKERY_JOBS[0]]
    assert (revenue.measure_field, revenue.group_field) == ("totalPrice", "franchiseID")
    assert {"start_date", "end_date"} <= {q.name for q in revenue.params}
    units = by_goal[BAKERY_JOBS[1]]
    assert (units.measure_field, units.group_field) == ("quantity", "product")
    daily = by_goal[BAKERY_JOBS[2]]
    assert daily.granularity == "day"
    assert any(q.source_field == "franchiseID" for q in daily.params)
    payments = by_goal[BAKERY_JOBS[3]]
    assert payments.aggregation == "count"
    assert payments.group_field == "paymentMethod"


def test_no_tool_measures_or_exposes_an_identifier_or_pii() -> None:
    spec = _bakery()
    plans, _ = design_tools(spec)
    spec.tools = plans
    for plan in plans:
        assert plan.measure_field not in {"transactionID", "customerID", "cardNumber"}
        assert "cardNumber" not in {q.source_field for q in plan.params}
        assert plan.group_field != "cardNumber"
        module = tool_module(spec, plan)
        assert "SELECT * FROM" not in module, plan.name


def test_titles_never_repeat_words() -> None:
    plans, _ = design_tools(_bakery())
    for plan in plans:
        words = plan.title.lower().split()
        assert all(a != b for a, b in zip(words, words[1:])), plan.title
        assert "avg_" not in plan.title, plan.title


def test_snapshot_lookup_and_search_strip_pii() -> None:
    source = DataSourceProfile(
        name="sales",
        kind=SourceKind.LOCAL_SNAPSHOT,
        modality=DataModality.STRUCTURED,
        snapshot_path="sales.csv",
        fields=[
            FieldProfile(name="sale_id", role=FieldRole.IDENTIFIER),
            FieldProfile(name="cardNumber", role=FieldRole.IDENTIFIER, pii=True),
            FieldProfile(name="note", role=FieldRole.TEXT),
        ],
    )
    spec = ServerSpec(
        server_name="sales-mcp",
        tool_prefix="sales",
        purpose="Look up sales records for an analyst.",
        data_sources=[source],
    )
    plans, _ = design_tools(spec)
    spec.tools = plans
    checked = 0
    for plan in plans:
        if plan.pattern.value in {"lookup", "search"}:
            module = tool_module(spec, plan)
            compile(module, plan.name, "exec")
            assert 'hidden = {"cardNumber"}' in module, plan.name
            checked += 1
    assert checked == 2
