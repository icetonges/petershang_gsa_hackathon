"""Score a server specification against the design and security rubric.

Every check here is mechanical: it reads the specification and reports what
is true, so the same review runs the same way twice. Judgment belongs in the
conversation that follows, not in the scoring.

Dimensions follow `knowledge/judging_rubric.md`, which maps them onto the GSA
hackathon's criteria.
"""

from __future__ import annotations

from mcp_server_creator import environments
from mcp_server_creator.generators import toolcode
from mcp_server_creator.models import (
    DataClassification,
    DataModality,
    DeploymentTarget,
    Finding,
    QueryPattern,
    ReviewReport,
    ServerSpec,
    Severity,
    SourceKind,
)

LIST_PATTERNS = {
    QueryPattern.SEARCH,
    QueryPattern.SEMANTIC_SEARCH,
    QueryPattern.RANK,
}

VAGUE_NAME_TAILS = {
    "data",
    "query",
    "records",
    "api",
    "all",
    "info",
    "stuff",
    "things",
    "results",
}

FORBIDDEN_PARAMS = {
    "url",
    "uri",
    "endpoint",
    "host",
    "sql",
    "statement",
    "path",
    "file_path",
    "filepath",
    "command",
    "script",
}

DIMENSIONS = (
    "goal_orientation",
    "input_design",
    "documentation",
    "context_economy",
    "error_quality",
    "security_posture",
    "evaluation",
    "reproducibility",
    "deployment",
    "environment_fit",
    "honesty",
)


def _penalize(scores: dict[str, int], dimension: str, amount: int) -> None:
    """Reduce one dimension's score, never below zero."""
    scores[dimension] = max(0, scores[dimension] - amount)


def review_spec(spec: ServerSpec) -> ReviewReport:
    """Review a specification and return scored findings."""
    findings: list[Finding] = []
    passed: list[str] = []
    scores = {dimension: 5 for dimension in DIMENSIONS}

    _check_goal_orientation(spec, findings, passed, scores)
    _check_inputs(spec, findings, passed, scores)
    _check_documentation(spec, findings, passed, scores)
    _check_context_economy(spec, findings, passed, scores)
    _check_errors(spec, findings, passed, scores)
    _check_security(spec, findings, passed, scores)
    _check_evaluation(spec, findings, passed, scores)
    _check_reproducibility(spec, findings, passed, scores)
    _check_deployment(spec, findings, passed, scores)
    _check_environment_fit(spec, findings, passed, scores)
    _check_honesty(spec, findings, passed, scores)

    blockers = [f for f in findings if f.severity is Severity.BLOCKER]
    high = [f for f in findings if f.severity is Severity.HIGH]
    average = sum(scores.values()) / len(scores)
    if blockers:
        verdict = (
            f"{len(blockers)} blocker(s) to fix before generating. "
            f"Average score {average:.1f} of 5."
        )
    elif high:
        verdict = (
            f"Generatable, with {len(high)} high-severity finding(s) worth "
            f"fixing first. Average score {average:.1f} of 5."
        )
    elif findings:
        verdict = (
            f"Sound design. {len(findings)} smaller improvement(s) available. "
            f"Average score {average:.1f} of 5."
        )
    else:
        verdict = f"Clean review. Average score {average:.1f} of 5."

    return ReviewReport(
        subject=spec.server_name,
        findings=findings,
        scores=scores,
        passed_checks=passed,
        verdict=verdict,
    )


def _check_goal_orientation(
    spec: ServerSpec,
    findings: list[Finding],
    passed: list[str],
    scores: dict[str, int],
) -> None:
    """Are the tools named after the user's questions?"""
    if not spec.tools:
        findings.append(
            Finding(
                check="tools_exist",
                severity=Severity.BLOCKER,
                summary="The server plans no tools.",
                fix="Run the tool designer before generating.",
                criterion="goal_orientation",
            )
        )
        _penalize(scores, "goal_orientation", 5)
        return

    if len(spec.tools) > 8:
        findings.append(
            Finding(
                check="tool_count",
                severity=Severity.MEDIUM,
                summary=f"{len(spec.tools)} tools is more than a focused server needs.",
                evidence=", ".join(plan.name for plan in spec.tools),
                fix=(
                    "Keep the tools that answer the user's stated questions "
                    "and drop the rest. A model choosing between twelve "
                    "similar tools chooses worse than one choosing between "
                    "five."
                ),
                criterion="goal_orientation",
            )
        )
        _penalize(scores, "goal_orientation", 1)
    else:
        passed.append("tool_count")

    vague = [
        plan.name for plan in spec.tools if plan.name.split("_")[-1] in VAGUE_NAME_TAILS
    ]
    if vague:
        findings.append(
            Finding(
                check="tool_names_are_goals",
                severity=Severity.HIGH,
                summary="Some tool names describe an endpoint, not a question.",
                evidence=", ".join(vague),
                fix=(
                    "Rename each after the question a real user asks. Read "
                    "the name aloud to the person whose job it is: would "
                    "they recognize it?"
                ),
                criterion="goal_orientation",
            )
        )
        _penalize(scores, "goal_orientation", 2)
    else:
        passed.append("tool_names_are_goals")

    unprefixed = [
        plan.name
        for plan in spec.tools
        if not plan.name.startswith(f"{spec.tool_prefix}_")
    ]
    if unprefixed:
        findings.append(
            Finding(
                check="consistent_prefix",
                severity=Severity.LOW,
                summary="Not every tool shares the server's prefix.",
                evidence=", ".join(unprefixed),
                fix=(
                    f"Prefix every tool with '{spec.tool_prefix}_' so the "
                    "surface stays scannable when a client has a dozen "
                    "servers connected."
                ),
                criterion="goal_orientation",
            )
        )
        _penalize(scores, "goal_orientation", 1)
    else:
        passed.append("consistent_prefix")

    missing_goal = [plan.name for plan in spec.tools if len(plan.goal.strip()) < 15]
    if missing_goal:
        findings.append(
            Finding(
                check="tool_goals_stated",
                severity=Severity.MEDIUM,
                summary="Some tools have no stated user goal.",
                evidence=", ".join(missing_goal),
                fix="Write the goal as the user's own question.",
                criterion="goal_orientation",
            )
        )
        _penalize(scores, "goal_orientation", 1)


def _check_inputs(
    spec: ServerSpec,
    findings: list[Finding],
    passed: list[str],
    scores: dict[str, int],
) -> None:
    """Are closed sets enums, and numbers bounded?"""
    free_text_filters: list[str] = []
    unbounded: list[str] = []
    for plan in spec.tools:
        source = spec.source_by_name(plan.source)
        for param in plan.params:
            if param.source_field and not param.enum_values and source:
                field = next(
                    (f for f in source.fields if f.name == param.source_field),
                    None,
                )
                if (
                    field is not None
                    and field.cardinality_hint is not None
                    and field.cardinality_hint <= 50
                ):
                    free_text_filters.append(f"{plan.name}.{param.name}")
            if param.python_type in {"int", "float"} and param.maximum is None:
                unbounded.append(f"{plan.name}.{param.name}")

    if free_text_filters:
        findings.append(
            Finding(
                check="closed_sets_are_enums",
                severity=Severity.HIGH,
                summary="Low-cardinality filters are exposed as free text.",
                evidence=", ".join(free_text_filters),
                fix=(
                    "Turn each into an enum of the real values. A model "
                    "cannot guess a spelling it has never seen, and this is "
                    "the single biggest accuracy win available."
                ),
                criterion="input_design",
            )
        )
        _penalize(scores, "input_design", 2)
    else:
        passed.append("closed_sets_are_enums")

    if unbounded:
        findings.append(
            Finding(
                check="numbers_are_bounded",
                severity=Severity.MEDIUM,
                summary="Numeric parameters have no maximum.",
                evidence=", ".join(unbounded),
                fix="Add an upper bound so one call cannot request everything.",
                criterion="input_design",
            )
        )
        _penalize(scores, "input_design", 1)
    else:
        passed.append("numbers_are_bounded")

    for source in spec.data_sources:
        if source.modality is DataModality.UNSTRUCTURED:
            continue
        needs_resolver = source.identifiers() and not any(
            plan.pattern is QueryPattern.SEARCH and plan.source == source.name
            for plan in spec.tools
        )
        if needs_resolver:
            findings.append(
                Finding(
                    check="resolver_available",
                    severity=Severity.MEDIUM,
                    summary=(f"'{source.name}' is reachable only by exact identifier."),
                    fix=(
                        "Add a search or resolver tool. Humans say 'Region "
                        "5'; the table says 'GSA FAS AAS REGION 5'."
                    ),
                    criterion="input_design",
                )
            )
            _penalize(scores, "input_design", 1)


def _check_documentation(
    spec: ServerSpec,
    findings: list[Finding],
    passed: list[str],
    scores: dict[str, int],
) -> None:
    """Does every tool docstring have what the model needs?"""
    missing_returns = [plan.name for plan in spec.tools if not plan.returns.strip()]
    if missing_returns:
        findings.append(
            Finding(
                check="returns_documented",
                severity=Severity.HIGH,
                summary="Some tools do not document their return shape.",
                evidence=", ".join(missing_returns),
                fix=(
                    "Name the fields the tool returns. The docstring is the "
                    "only documentation the model gets."
                ),
                criterion="documentation",
            )
        )
        _penalize(scores, "documentation", 2)
    else:
        passed.append("returns_documented")

    no_grain = [s.name for s in spec.data_sources if not s.grain]
    if no_grain:
        findings.append(
            Finding(
                check="grain_recorded",
                severity=Severity.HIGH,
                summary="Some sources do not say what one record represents.",
                evidence=", ".join(no_grain),
                fix=(
                    "Record the grain. One row per modification is not one "
                    "row per award, and a model will miscount."
                ),
                criterion="documentation",
            )
        )
        _penalize(scores, "documentation", 2)
    else:
        passed.append("grain_recorded")

    no_freshness = [
        s.name
        for s in spec.data_sources
        if not s.as_of or s.update_cadence in ("", "unknown")
    ]
    if no_freshness:
        findings.append(
            Finding(
                check="freshness_recorded",
                severity=Severity.MEDIUM,
                summary="Some sources have no as-of date or refresh cadence.",
                evidence=", ".join(no_freshness),
                fix=(
                    "Record both. An agent that believes it has today's "
                    "numbers will state today's numbers."
                ),
                criterion="documentation",
            )
        )
        _penalize(scores, "documentation", 1)
    else:
        passed.append("freshness_recorded")

    undescribed = [
        f"{plan.name}.{param.name}"
        for plan in spec.tools
        for param in plan.params
        if len(param.description.strip()) < 10
    ]
    if undescribed:
        findings.append(
            Finding(
                check="parameters_described",
                severity=Severity.MEDIUM,
                summary="Some parameters have no usable description.",
                evidence=", ".join(undescribed[:8]),
                fix="Say what the parameter selects, and give an example value.",
                criterion="documentation",
            )
        )
        _penalize(scores, "documentation", 1)


def _check_context_economy(
    spec: ServerSpec,
    findings: list[Finding],
    passed: list[str],
    scores: dict[str, int],
) -> None:
    """Do list-shaped tools paginate?"""
    # The principle is that a list-shaped tool bounds its own result size.
    # Pagination is the usual way; a capped count parameter is another, and
    # is the right one for similarity search, where an offset into ranked
    # results means little.
    unbounded = [
        plan.name
        for plan in spec.tools
        if plan.pattern in LIST_PATTERNS
        and not plan.paginated
        and not any(param.maximum is not None for param in plan.params)
    ]
    if unbounded:
        findings.append(
            Finding(
                check="list_tools_bound_their_results",
                severity=Severity.HIGH,
                summary="Some list-shaped tools do not bound their results.",
                evidence=", ".join(unbounded),
                fix=(
                    "Add limit and offset and return total, has_more and "
                    "next_offset, or cap a count parameter with a maximum. A "
                    "tool that returns 500 rows has spent the agent's "
                    "context without answering anything."
                ),
                criterion="context_economy",
            )
        )
        _penalize(scores, "context_economy", 2)
    else:
        passed.append("list_tools_bound_their_results")

    has_dictionary = any(
        plan.pattern is QueryPattern.DESCRIBE_SOURCE for plan in spec.tools
    )
    if not has_dictionary and spec.data_sources:
        findings.append(
            Finding(
                check="dictionary_tool_present",
                severity=Severity.MEDIUM,
                summary="No tool returns the data dictionary.",
                fix=(
                    "Add a describe tool. It is the cheapest accuracy win "
                    "available: the agent stops guessing column semantics "
                    "and allowed values."
                ),
                criterion="context_economy",
            )
        )
        _penalize(scores, "context_economy", 1)
    else:
        passed.append("dictionary_tool_present")


def _check_errors(
    spec: ServerSpec,
    findings: list[Finding],
    passed: list[str],
    scores: dict[str, int],
) -> None:
    """Does every tool teach the model what to do when it fails?"""
    no_hints = [
        plan.name
        for plan in spec.tools
        if not plan.error_hints and plan.pattern is not QueryPattern.DESCRIBE_SOURCE
    ]
    if no_hints:
        findings.append(
            Finding(
                check="error_hints_present",
                severity=Severity.MEDIUM,
                summary="Some tools record no actionable error hint.",
                evidence=", ".join(no_hints),
                fix=(
                    "Write the hint the model needs when the tool cannot "
                    "answer. An error is a turn already spent; make it buy "
                    "something."
                ),
                criterion="error_quality",
            )
        )
        _penalize(scores, "error_quality", 2)
    else:
        passed.append("error_hints_present")


def _check_security(
    spec: ServerSpec,
    findings: list[Finding],
    passed: list[str],
    scores: dict[str, int],
) -> None:
    """The failure modes that exist only because a model is driving."""
    offenders = [
        f"{plan.name}.{param.name}"
        for plan in spec.tools
        for param in plan.params
        if param.name.lower() in FORBIDDEN_PARAMS
    ]
    if offenders:
        findings.append(
            Finding(
                check="no_url_sql_or_path_arguments",
                severity=Severity.BLOCKER,
                summary=("A tool accepts a URL, raw SQL, or a filesystem path."),
                evidence=", ".join(offenders),
                fix=(
                    "Remove it. These three arguments turn a data tool into "
                    "an exfiltration primitive: an injected instruction in "
                    "retrieved content can then name the destination. If one "
                    "is genuinely required, allowlist exact values and "
                    "validate at connection time."
                ),
                criterion="security_posture",
            )
        )
        _penalize(scores, "security_posture", 5)
    else:
        passed.append("no_url_sql_or_path_arguments")

    writers = [plan.name for plan in spec.tools if not plan.read_only]
    if writers and spec.read_only:
        findings.append(
            Finding(
                check="read_only_consistent",
                severity=Severity.HIGH,
                summary=("The server claims to be read-only but plans a write tool."),
                evidence=", ".join(writers),
                fix=(
                    "Either set read_only to false and annotate the tool "
                    "honestly, or remove the write. A read-only annotation "
                    "on a tool that writes is a lie the client will act on."
                ),
                criterion="security_posture",
            )
        )
        _penalize(scores, "security_posture", 2)

    for plan in writers:
        findings.append(
            Finding(
                check="write_tool_confirmation",
                severity=Severity.HIGH,
                summary=f"'{plan}' writes and needs explicit confirmation.",
                fix=(
                    "Require a confirmation argument the model cannot "
                    "default to true, log every write with the caller "
                    "identity, and set destructiveHint honestly."
                ),
                criterion="security_posture",
            )
        )
        _penalize(scores, "security_posture", 1)

    unstructured = [
        source
        for source in spec.data_sources
        if source.modality is DataModality.UNSTRUCTURED
    ]
    if unstructured and not spec.evaluation.include_injection:
        findings.append(
            Finding(
                check="injection_tested",
                severity=Severity.HIGH,
                summary=(
                    "The server returns document text but tests no prompt injection."
                ),
                fix=(
                    "Plant an injection string in an indexed document and "
                    "assert the agent reports it rather than obeying it. "
                    "This is the failure mode unique to retrieval tools."
                ),
                criterion="security_posture",
            )
        )
        _penalize(scores, "security_posture", 2)
    elif unstructured:
        passed.append("injection_tested")

    if spec.pii_present and spec.classification is DataClassification.PUBLIC:
        findings.append(
            Finding(
                check="classification_consistent",
                severity=Severity.HIGH,
                summary=(
                    "The data is marked public but is said to contain "
                    "personal information."
                ),
                fix=(
                    "Reclassify. Classification drives logging rules, the "
                    "read-only posture, and whether a snapshot may be "
                    "committed at all."
                ),
                criterion="security_posture",
            )
        )
        _penalize(scores, "security_posture", 2)

    for source in spec.data_sources:
        if source.kind is SourceKind.REST_API and not source.api_base_url:
            findings.append(
                Finding(
                    check="fixed_api_origin",
                    severity=Severity.BLOCKER,
                    summary=f"'{source.name}' has no fixed API origin.",
                    fix=(
                        "Set api_base_url. The origin must be operator "
                        "controlled, never a tool argument."
                    ),
                    criterion="security_posture",
                )
            )
            _penalize(scores, "security_posture", 3)


def _check_evaluation(
    spec: ServerSpec,
    findings: list[Finding],
    passed: list[str],
    scores: dict[str, int],
) -> None:
    """Does the evaluation have the five ingredients that make it mean anything?"""
    plan = spec.evaluation
    missing: list[str] = []
    if not plan.baseline_without_tools:
        missing.append("a no-tool baseline")
    if not plan.include_decoys:
        missing.append("decoys")
    if not plan.include_no_action:
        missing.append("no-action cases")
    if not plan.include_injection:
        missing.append("an injection case")
    if not plan.include_out_of_scope:
        missing.append("an out-of-scope case")
    if missing:
        findings.append(
            Finding(
                check="evaluation_ingredients",
                severity=Severity.HIGH,
                summary="The evaluation is missing " + ", ".join(missing) + ".",
                fix=(
                    "Turn each on. Without the baseline there is no way to "
                    "say what the server contributed; without decoys and "
                    "no-action cases a server that pattern-matches scores "
                    "the same as one that works."
                ),
                criterion="evaluation",
            )
        )
        _penalize(scores, "evaluation", 2)
    else:
        passed.append("evaluation_ingredients")

    if plan.golden_question_count < 20:
        findings.append(
            Finding(
                check="golden_question_count",
                severity=Severity.MEDIUM,
                summary=(f"{plan.golden_question_count} golden questions is thin."),
                fix=(
                    "Aim for twenty or more: three per tool, one easy, one "
                    "needing a filter choice, one needing two tools composed."
                ),
                criterion="evaluation",
            )
        )
        _penalize(scores, "evaluation", 1)
    if plan.repeats < 3:
        findings.append(
            Finding(
                check="evaluation_repeats",
                severity=Severity.MEDIUM,
                summary=f"{plan.repeats} repeat(s) per question.",
                fix=(
                    "Use three to five. A single run of a stochastic agent "
                    "is an anecdote."
                ),
                criterion="evaluation",
            )
        )
        _penalize(scores, "evaluation", 1)
    if plan.agent_model and plan.agent_model == plan.judge_model:
        findings.append(
            Finding(
                check="distinct_judge_model",
                severity=Severity.MEDIUM,
                summary="The agent and the judge use the same model.",
                fix=(
                    "Judge with a different, usually stronger model. It "
                    "reduces self-preference bias and lets you hold the "
                    "judge fixed while iterating on the agent."
                ),
                criterion="evaluation",
            )
        )
        _penalize(scores, "evaluation", 1)


def _check_reproducibility(
    spec: ServerSpec,
    findings: list[Finding],
    passed: list[str],
    scores: dict[str, int],
) -> None:
    """Can a reviewer clone the repository and run it?"""
    keyless = any(
        source.kind is SourceKind.LOCAL_SNAPSHOT for source in spec.data_sources
    )
    if not keyless and spec.data_sources:
        findings.append(
            Finding(
                check="keyless_path_available",
                severity=Severity.MEDIUM,
                summary="Every source needs credentials to reach.",
                fix=(
                    "Package a small snapshot alongside the live path. A "
                    "reviewer who can clone and run every tool with no "
                    "credentials evaluates the server; one who cannot reads "
                    "the README and guesses."
                ),
                criterion="reproducibility",
            )
        )
        _penalize(scores, "reproducibility", 2)
    elif keyless:
        passed.append("keyless_path_available")


def _check_deployment(
    spec: ServerSpec,
    findings: list[Finding],
    passed: list[str],
    scores: dict[str, int],
) -> None:
    """Will the platform actually recognize and run this?"""
    deployment = spec.deployment
    if deployment.target is not DeploymentTarget.DATABRICKS_APPS:
        return

    if not deployment.app_name.startswith("mcp-"):
        findings.append(
            Finding(
                check="app_name_prefix",
                severity=Severity.BLOCKER,
                summary=(
                    f"The app name '{deployment.app_name}' does not start with 'mcp-'."
                ),
                fix=(
                    "Rename it. AI Playground and Unity Gateway use the "
                    "prefix to recognize an app as an MCP server; without it "
                    "the app deploys and is never offered as an MCP."
                ),
                criterion="deployment",
            )
        )
        _penalize(scores, "deployment", 4)
    else:
        passed.append("app_name_prefix")

    needs_grants = [
        source
        for source in spec.data_sources
        if source.kind
        in {
            SourceKind.UC_TABLE,
            SourceKind.UC_FUNCTION,
            SourceKind.AI_SEARCH_INDEX,
            SourceKind.UC_VOLUME_FILES,
            SourceKind.GENIE_SPACE,
        }
    ]
    undocumented = [s.name for s in needs_grants if not s.required_grants]
    if undocumented:
        findings.append(
            Finding(
                check="grants_recorded",
                severity=Severity.HIGH,
                summary="Some governed sources record no required grants.",
                evidence=", ".join(undocumented),
                fix=(
                    "Record them. The app runs as its own service principal, "
                    "and a server that works locally and returns empty "
                    "results once deployed has almost always missed this."
                ),
                criterion="deployment",
            )
        )
        _penalize(scores, "deployment", 2)
    elif needs_grants:
        passed.append("grants_recorded")

    if needs_grants and not deployment.use_bundle:
        findings.append(
            Finding(
                check="bundle_declared",
                severity=Severity.LOW,
                summary="No asset bundle, so resources are bound by hand.",
                fix=(
                    "Emit databricks.yml. Declaring a warehouse or secret as "
                    "a resource grants it on deploy and keeps the deployment "
                    "reproducible."
                ),
                criterion="deployment",
            )
        )
        _penalize(scores, "deployment", 1)


def _check_honesty(
    spec: ServerSpec,
    findings: list[Finding],
    passed: list[str],
    scores: dict[str, int],
) -> None:
    """Does the design say what it cannot do?"""
    if not spec.out_of_scope:
        findings.append(
            Finding(
                check="scope_declared",
                severity=Severity.MEDIUM,
                summary="Nothing is declared out of scope.",
                fix=(
                    "Name what the server must refuse. A server that answers "
                    "everything confidently cannot be evaluated, and scope "
                    "refusal is scoreable."
                ),
                criterion="honesty",
            )
        )
        _penalize(scores, "honesty", 2)
    else:
        passed.append("scope_declared")

    if not spec.jobs_to_be_done:
        findings.append(
            Finding(
                check="jobs_recorded",
                severity=Severity.BLOCKER,
                summary="No user questions are recorded.",
                fix=(
                    "Collect three to eight questions in the user's own "
                    "words. They become the tool names and the golden "
                    "questions; without them there is nothing to design "
                    "against or measure."
                ),
                criterion="honesty",
            )
        )
        _penalize(scores, "honesty", 3)
    else:
        passed.append("jobs_recorded")

    if not spec.primary_user:
        findings.append(
            Finding(
                check="user_named",
                severity=Severity.MEDIUM,
                summary="No primary user is named.",
                fix=(
                    "Name one person and the task they do by hand today. "
                    "Mission alignment is a judged criterion and it starts "
                    "here."
                ),
                criterion="honesty",
            )
        )
        _penalize(scores, "honesty", 1)

    stubbed = [
        plan.name
        for plan in spec.tools
        for source in [spec.source_by_name(plan.source)]
        if source is not None and not toolcode.is_supported(source.kind, plan.pattern)
    ]
    if stubbed:
        findings.append(
            Finding(
                check="all_tools_implementable",
                severity=Severity.MEDIUM,
                summary=(
                    "Some tools pair a pattern and a source the generator "
                    "cannot implement, so they will be stubs."
                ),
                evidence=", ".join(stubbed),
                fix=(
                    "Either implement them by hand after generating, or "
                    "replace them with a supported pattern for that source."
                ),
                criterion="honesty",
            )
        )
        _penalize(scores, "honesty", 1)


def _check_environment_fit(
    spec: ServerSpec,
    findings: list[Finding],
    passed: list[str],
    scores: dict[str, int],
) -> None:
    """Will this design work in the environment it is going to?

    A design validated against the commercial feature set can fail on
    deployment in a FedRAMP High or IL5 workspace, because the feature it
    depends on is not there. That is not a code problem and no code review
    finds it.
    """
    profile = spec.environment
    if profile is environments.EnvironmentProfile.UNKNOWN:
        findings.append(
            Finding(
                check="environment_declared",
                severity=Severity.MEDIUM,
                summary="The target environment is not declared.",
                fix=(
                    "Run creator_check_environment and set it. Commercial, "
                    "FedRAMP High and IL5 workspaces do not have the same "
                    "features, and the gaps are not guessable."
                ),
                criterion="environment_fit",
            )
        )
        _penalize(scores, "environment_fit", 2)
        return

    passed.append("environment_declared")
    kinds = [source.kind.value for source in spec.data_sources]
    for finding in environments.check_design(
        profile, kinds, uses_in_process_state=False
    ):
        severity = {
            "blocker": Severity.BLOCKER,
            "high": Severity.HIGH,
            "medium": Severity.MEDIUM,
        }.get(finding.severity, Severity.LOW)
        findings.append(
            Finding(
                check=f"environment_{finding.capability}",
                severity=severity,
                summary=finding.summary,
                evidence=f"source: {finding.subject}",
                fix=finding.fix,
                criterion="environment_fit",
            )
        )
        _penalize(scores, "environment_fit", 3 if severity is Severity.BLOCKER else 1)

    unstructured = [
        source
        for source in spec.data_sources
        if source.modality is DataModality.UNSTRUCTURED
    ]
    full_text = environments.availability(profile, "vector_search_full_text")
    if unstructured and full_text is environments.Availability.UNSUPPORTED:
        findings.append(
            Finding(
                check="keyword_retrieval_path",
                severity=Severity.MEDIUM,
                summary=(
                    "This environment has no full-text index, so there is no "
                    "BM25 path for identifier-like queries."
                ),
                fix=(
                    "Add a second tool that runs an exact match or SQL LIKE "
                    "against the chunk table. Semantic search is the wrong "
                    "tool for a clause number or a contract id."
                ),
                criterion="environment_fit",
            )
        )
        _penalize(scores, "environment_fit", 1)

    if (
        not any(
            source.kind is SourceKind.LOCAL_SNAPSHOT for source in spec.data_sources
        )
        and environments.availability(profile, "public_package_index")
        is not environments.Availability.SUPPORTED
    ):
        findings.append(
            Finding(
                check="offline_installability",
                severity=Severity.LOW,
                summary=(
                    "Package index reachability is not confirmed here and "
                    "the server has no credential-free path."
                ),
                fix=(
                    "Keep the dependency list short, and package a snapshot "
                    "so the server can be demonstrated without a warehouse "
                    "or a network."
                ),
                criterion="environment_fit",
            )
        )
