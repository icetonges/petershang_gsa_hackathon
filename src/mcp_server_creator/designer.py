"""Propose a tool surface from a specification, and name what is missing.

The designer is deliberately conservative. It proposes only tools the
profiled fields can actually support, and it reports the gaps rather than
inventing a tool that would return nulls.
"""

from __future__ import annotations

from mcp_server_creator.models import (
    DataModality,
    DataSourceProfile,
    FieldProfile,
    ParamPlan,
    QueryPattern,
    ServerSpec,
    Severity,
    SourceKind,
    SpecGap,
    ToolPlan,
)
from mcp_server_creator.utils import to_identifier

MAX_TOOLS = 8
VAGUE_TOOL_WORDS = {
    "get",
    "data",
    "query",
    "fetch",
    "all",
    "list",
    "info",
    "records",
    "api",
    "call",
    "run",
    "execute",
}


def _tool_name(prefix: str, *parts: str) -> str:
    """Build a prefixed snake_case tool name within the length limit."""
    pieces = [to_identifier(prefix)] + [to_identifier(p) for p in parts if p]
    name = "_".join(piece for piece in pieces if piece)
    if len(name) <= 63:
        return name
    trimmed = name[:63].rstrip("_")
    return trimmed or f"{to_identifier(prefix)}_tool"


def _enum_candidates(source: DataSourceProfile, limit: int = 2) -> list[FieldProfile]:
    """Dimension fields narrow enough to expose as enums."""
    candidates = [
        field
        for field in source.dimensions()
        if field.enum_values
        or (field.cardinality_hint is not None and field.cardinality_hint <= 50)
    ]
    return candidates[:limit]


def _filter_params(source: DataSourceProfile, limit: int = 2) -> list[ParamPlan]:
    """Optional filter parameters, as enums where the data allows."""
    params: list[ParamPlan] = []
    for field in _enum_candidates(source, limit):
        description = (
            field.description
            or f"Narrow results to one {field.name.replace('_', ' ')}."
        )
        params.append(
            ParamPlan(
                name=to_identifier(field.name),
                python_type="str",
                description=description,
                required=False,
                default=None,
                enum_values=list(field.enum_values),
                source_field=field.name,
            )
        )
    return params


def _seeds(source: DataSourceProfile, pattern: QueryPattern) -> list[str]:
    """Draft golden questions a tool should be able to answer."""
    label = source.title or source.name.replace("_", " ")
    measure = source.measures()[0].name if source.measures() else "the measure"
    dimension = source.dimensions()[0].name if source.dimensions() else "the category"
    if pattern is QueryPattern.LOOKUP:
        return [f"TODO: look up one known {label} record by its identifier."]
    if pattern is QueryPattern.SEARCH:
        return [f"TODO: search {label} for a term you know appears exactly once."]
    if pattern is QueryPattern.RANK:
        return [
            f"TODO: which {dimension} has the highest {measure} in {label}, "
            "and what share of the total is it?"
        ]
    if pattern is QueryPattern.AGGREGATE:
        return [f"TODO: what is the total {measure} in {label} for one scope?"]
    if pattern is QueryPattern.TREND:
        return [
            f"TODO: in which month did {measure} change most in {label}, "
            "and by how much?"
        ]
    if pattern is QueryPattern.SEMANTIC_SEARCH:
        return [
            f"TODO: ask a question whose answer sits in one passage of {label}, "
            "and check the citation points at the right document."
        ]
    if pattern is QueryPattern.DESCRIBE_SOURCE:
        return [f"TODO: how many fields does {label} have, and what is its grain?"]
    if pattern is QueryPattern.NL_QUERY:
        return [f"TODO: ask {label} an analytical question with a known answer."]
    if pattern is QueryPattern.VALIDATE:
        return [f"TODO: which field in {label} is least complete?"]
    return []


def _describe_tool(spec: ServerSpec, source: DataSourceProfile) -> ToolPlan:
    """A data dictionary tool: the cheapest accuracy win available."""
    return ToolPlan(
        name=_tool_name(spec.tool_prefix, "describe", source.name),
        title=f"Describe {source.title or source.name}",
        goal=(
            f"Tell me what is in {source.title or source.name}: the fields, "
            "what they mean, the allowed values, and how fresh it is."
        ),
        pattern=QueryPattern.DESCRIBE_SOURCE,
        source=source.name,
        returns=(
            "The source's grain, update cadence, as-of date, and every field "
            "with its type, role, description, and allowed values."
        ),
        freshness_note=(
            "This tool needs no credentials and no query: the dictionary "
            "ships with the server."
        ),
        eval_seeds=_seeds(source, QueryPattern.DESCRIBE_SOURCE),
    )


def _structured_tools(spec: ServerSpec, source: DataSourceProfile) -> list[ToolPlan]:
    """Propose the tools a tabular source can actually support."""
    plans: list[ToolPlan] = [_describe_tool(spec, source)]
    label = source.title or source.name.replace("_", " ")
    identifiers = source.identifiers()
    measures = source.measures()
    dimensions = source.dimensions()
    dates = source.dates()
    text_fields = source.text_fields()

    if identifiers:
        key = identifiers[0]
        plans.append(
            ToolPlan(
                name=_tool_name(spec.tool_prefix, "get", source.name),
                title=f"Get one {label} record",
                goal=f"Show me the full {label} record for one identifier.",
                pattern=QueryPattern.LOOKUP,
                source=source.name,
                params=[
                    ParamPlan(
                        name=to_identifier(key.name),
                        python_type="str",
                        description=(
                            key.description
                            or f"Exact {key.name.replace('_', ' ')} to fetch."
                            + (f" Example: {key.example}." if key.example else "")
                        ),
                        required=True,
                        source_field=key.name,
                    )
                ],
                returns=(
                    "The matching record, plus the field used to find it. An "
                    "error with a hint when nothing matches."
                ),
                error_hints=[
                    "Identifiers are exact; call the describe tool for example values."
                ],
                eval_seeds=_seeds(source, QueryPattern.LOOKUP),
            )
        )

    if text_fields or identifiers:
        plans.append(
            ToolPlan(
                name=_tool_name(spec.tool_prefix, "search", source.name),
                title=f"Search {label}",
                goal=f"Find {label} records whose text mentions something.",
                pattern=QueryPattern.SEARCH,
                source=source.name,
                params=[
                    ParamPlan(
                        name="query",
                        python_type="str",
                        description=(
                            "Keywords to match. This is a substring match, "
                            "not a semantic search."
                        ),
                        required=True,
                    ),
                    *_filter_params(source),
                ],
                paginated=True,
                returns=(
                    "A pagination envelope with total, count, offset, "
                    "has_more, next_offset, and the matching records."
                ),
                error_hints=["Try a shorter query, or drop a filter."],
                eval_seeds=_seeds(source, QueryPattern.SEARCH),
            )
        )

    if measures and dimensions:
        measure = measures[0]
        dimension = dimensions[0]
        plans.append(
            ToolPlan(
                name=_tool_name(
                    spec.tool_prefix, "top", dimension.name, "by", measure.name
                ),
                title=f"Top {dimension.name} by {measure.name}",
                goal=(
                    f"Which {dimension.name.replace('_', ' ')} accounts for "
                    f"the most {measure.name.replace('_', ' ')}?"
                ),
                pattern=QueryPattern.RANK,
                source=source.name,
                params=[*_filter_params(source)],
                paginated=True,
                returns=(
                    "The top groups with their measure total, record count, "
                    "and share of the whole, plus the population totals so "
                    "the share can be stated honestly."
                ),
                error_hints=["Drop a filter, or widen the period."],
                eval_seeds=_seeds(source, QueryPattern.RANK),
            )
        )

    if measures:
        plans.append(
            ToolPlan(
                name=_tool_name(spec.tool_prefix, source.name, "totals"),
                title=f"{label} totals",
                goal=(
                    f"Give me the totals for {label}: how many records and "
                    f"how much {measures[0].name.replace('_', ' ')}."
                ),
                pattern=QueryPattern.AGGREGATE,
                source=source.name,
                params=[*_filter_params(source)],
                returns=(
                    "Record count and the measure's sum, mean, minimum, and "
                    "maximum for the filtered scope."
                ),
                error_hints=["Drop a filter, or check the period."],
                eval_seeds=_seeds(source, QueryPattern.AGGREGATE),
            )
        )

    if measures and dates:
        plans.append(
            ToolPlan(
                name=_tool_name(spec.tool_prefix, source.name, "trend"),
                title=f"{label} over time",
                goal=(
                    f"How has {measures[0].name.replace('_', ' ')} moved "
                    "month by month, and where did it change most?"
                ),
                pattern=QueryPattern.TREND,
                source=source.name,
                params=[*_filter_params(source)],
                returns=(
                    "One row per month with the measure total, record count, "
                    "and the change and percentage change against the prior "
                    "month, computed server-side."
                ),
                error_hints=["Check the date filter; the period may be empty."],
                eval_seeds=_seeds(source, QueryPattern.TREND),
            )
        )

    return plans


def _unstructured_tools(spec: ServerSpec, source: DataSourceProfile) -> list[ToolPlan]:
    """Propose the retrieval tools an indexed corpus supports."""
    label = source.title or source.name.replace("_", " ")
    plans = [_describe_tool(spec, source)]
    if source.kind is not SourceKind.AI_SEARCH_INDEX:
        return plans
    plans.append(
        ToolPlan(
            name=_tool_name(spec.tool_prefix, "find", source.name, "passages"),
            title=f"Find passages in {label}",
            goal=f"Find the passages of {label} that bear on a question.",
            pattern=QueryPattern.SEMANTIC_SEARCH,
            source=source.name,
            params=[
                ParamPlan(
                    name="question",
                    python_type="str",
                    description="The question, in plain language.",
                    required=True,
                ),
                *_filter_params(source, limit=2),
                ParamPlan(
                    name="max_passages",
                    python_type="int",
                    description=(
                        "How many passages to return. Keep this small: each "
                        "passage costs context."
                    ),
                    required=False,
                    default=5,
                    minimum=1,
                    maximum=20,
                ),
            ],
            returns=(
                "Passages with their citation fields and similarity scores. "
                "No synthesized answer: the agent composes and attributes."
            ),
            error_hints=["Try a shorter question, or drop a filter."],
            freshness_note=(
                "Retrieval finds passages that resemble the question. It "
                "does not count documents, compare versions, or decide "
                "which rule is in force."
            ),
            eval_seeds=_seeds(source, QueryPattern.SEMANTIC_SEARCH),
        )
    )
    return plans


def _other_tools(spec: ServerSpec, source: DataSourceProfile) -> list[ToolPlan]:
    """Propose tools for the remaining source kinds."""
    label = source.title or source.name.replace("_", " ")
    plans = [_describe_tool(spec, source)]
    if source.kind is SourceKind.GENIE_SPACE:
        plans.append(
            ToolPlan(
                name=_tool_name(spec.tool_prefix, "ask", source.name),
                title=f"Ask {label}",
                goal=(
                    f"Ask {label} an analytical question the other tools do "
                    "not anticipate."
                ),
                pattern=QueryPattern.NL_QUERY,
                source=source.name,
                params=[
                    ParamPlan(
                        name="question",
                        python_type="str",
                        description="The analytical question, in plain language.",
                        required=True,
                    )
                ],
                returns=(
                    "Genie's narrative answer and the SQL it generated, so "
                    "the number can be checked."
                ),
                freshness_note=(
                    "Genie generates SQL and is not deterministic. Check the "
                    "generated SQL before relying on a number, and prefer a "
                    "deterministic tool when one fits."
                ),
                error_hints=["Rephrase the question, or name the table."],
                eval_seeds=_seeds(source, QueryPattern.NL_QUERY),
            )
        )
    elif source.kind is SourceKind.UC_FUNCTION:
        plans.append(
            ToolPlan(
                name=_tool_name(spec.tool_prefix, "check", source.name),
                title=f"Run {label}",
                goal=f"Run the governed {label} calculation.",
                pattern=QueryPattern.VALIDATE,
                source=source.name,
                params=[
                    ParamPlan(
                        name="value",
                        python_type="str",
                        description=(
                            "Argument for the function. Match the registered "
                            "signature; see the describe tool."
                        ),
                        required=True,
                    )
                ],
                returns="The function's result.",
                error_hints=["Check the argument types against the signature."],
                eval_seeds=_seeds(source, QueryPattern.VALIDATE),
            )
        )
    elif source.kind is SourceKind.UC_VOLUME_FILES:
        plans.append(
            ToolPlan(
                name=_tool_name(spec.tool_prefix, "list", source.name, "files"),
                title=f"List {label} files",
                goal=f"Show me what files are in {label}.",
                pattern=QueryPattern.SEARCH,
                source=source.name,
                paginated=True,
                returns="File names, paths, and sizes.",
                freshness_note=(
                    "This lists files. To answer questions about their "
                    "contents, index the corpus and add a retrieval tool."
                ),
                error_hints=["Check the volume path and the READ VOLUME grant."],
                eval_seeds=_seeds(source, QueryPattern.SEARCH),
            )
        )
    return plans


def design_tools(spec: ServerSpec) -> tuple[list[ToolPlan], list[str]]:
    """Propose a tool surface, with notes on what was left out and why."""
    plans: list[ToolPlan] = []
    notes: list[str] = []

    for source in spec.data_sources:
        if source.modality is DataModality.UNSTRUCTURED:
            plans.extend(_unstructured_tools(spec, source))
        elif source.kind in {
            SourceKind.UC_TABLE,
            SourceKind.LOCAL_SNAPSHOT,
            SourceKind.REST_API,
        }:
            plans.extend(_structured_tools(spec, source))
        else:
            plans.extend(_other_tools(spec, source))

        if not source.measures():
            notes.append(
                f"'{source.name}' has no numeric measure, so no totals, "
                "rankings, or trends were proposed for it."
            )
        if not source.dates():
            notes.append(
                f"'{source.name}' has no date field, so no trend or "
                "expiring-soon tool was proposed for it."
            )
        if not source.identifiers():
            notes.append(
                f"'{source.name}' has no stable identifier, so no lookup tool "
                "was proposed. Add one if a single record must be fetchable."
            )
        if not _enum_candidates(source):
            notes.append(
                f"'{source.name}' has no low-cardinality dimension, so its "
                "filters are free text. Profile a column's distinct values to "
                "turn it into an enum, which is the biggest single accuracy "
                "win available."
            )

    seen: set[str] = set()
    unique: list[ToolPlan] = []
    for plan in plans:
        if plan.name in seen:
            continue
        seen.add(plan.name)
        unique.append(plan)

    if len(unique) > MAX_TOOLS:
        notes.append(
            f"{len(unique)} tools were proposed, which is more than the {MAX_TOOLS} "
            "a focused server should carry. Keep the ones that answer the "
            "user's stated questions and drop the rest; a model choosing "
            "between twelve similar tools chooses worse than one choosing "
            "between five."
        )

    jobs = list(spec.jobs_to_be_done)
    for index, plan in enumerate(unique):
        if index < len(jobs):
            plan.eval_seeds = [jobs[index]] + plan.eval_seeds

    return unique, notes


def analyze_gaps(spec: ServerSpec) -> list[SpecGap]:
    """Name what is missing from a specification, with the question to ask."""
    gaps: list[SpecGap] = []

    if not spec.primary_user:
        gaps.append(
            SpecGap(
                field="primary_user",
                why_it_matters=(
                    "A server built for 'users' is built for nobody. The "
                    "named person decides the tool names and the golden "
                    "questions."
                ),
                question="Whose job does this make easier? Name one person.",
                severity=Severity.HIGH,
                suggested_default="an analyst",
            )
        )
    if len(spec.jobs_to_be_done) < 3:
        gaps.append(
            SpecGap(
                field="jobs_to_be_done",
                why_it_matters=(
                    "These become tool names and golden questions. Fewer "
                    "than three and there is not enough to design against."
                ),
                question=(
                    "What three to eight questions does that person ask "
                    "today, in their own words?"
                ),
                severity=Severity.BLOCKER,
            )
        )
    if not spec.data_sources:
        gaps.append(
            SpecGap(
                field="data_sources",
                why_it_matters="With no source there is nothing to query.",
                question=(
                    "What data should this read, and where does it live? A "
                    "Unity Catalog table, a folder of documents, an API, or "
                    "a file you can paste a sample of."
                ),
                severity=Severity.BLOCKER,
            )
        )
    if not spec.out_of_scope:
        gaps.append(
            SpecGap(
                field="out_of_scope",
                why_it_matters=(
                    "A server that answers everything confidently cannot be "
                    "evaluated, and scope refusal is scoreable."
                ),
                question="What should this server refuse to answer?",
                severity=Severity.MEDIUM,
            )
        )

    for source in spec.data_sources:
        if not source.fields:
            gaps.append(
                SpecGap(
                    field=f"data_sources.{source.name}.fields",
                    why_it_matters=(
                        "Without fields the designer cannot tell which "
                        "question shapes the data supports, and tools fall "
                        "back to positional guesses."
                    ),
                    question=(
                        f"Paste the schema for '{source.name}': a CREATE "
                        "TABLE statement, a CSV header with a few rows, a "
                        "DESCRIBE output, or a JSON sample."
                    ),
                    severity=Severity.BLOCKER,
                )
            )
        if not source.grain:
            gaps.append(
                SpecGap(
                    field=f"data_sources.{source.name}.grain",
                    why_it_matters=(
                        "The grain goes in every docstring. Without it a "
                        "model will miscount: one row per modification is "
                        "not one row per award."
                    ),
                    question=f"What does one row of '{source.name}' represent?",
                    severity=Severity.HIGH,
                )
            )
        if not source.as_of:
            gaps.append(
                SpecGap(
                    field=f"data_sources.{source.name}.as_of",
                    why_it_matters=(
                        "Every result reports an as-of date. Without one the "
                        "agent implies the data is current."
                    ),
                    question=f"What date does '{source.name}' currently reflect?",
                    severity=Severity.HIGH,
                )
            )
        if source.update_cadence in ("", "unknown"):
            gaps.append(
                SpecGap(
                    field=f"data_sources.{source.name}.update_cadence",
                    why_it_matters="Freshness belongs in the docstring.",
                    question=f"How often does '{source.name}' refresh?",
                    severity=Severity.MEDIUM,
                    suggested_default="unknown",
                )
            )
        if source.kind is SourceKind.UC_TABLE and not source.full_table_name:
            gaps.append(
                SpecGap(
                    field=f"data_sources.{source.name}.table",
                    why_it_matters="A query needs catalog, schema, and table.",
                    question=(f"What is the full three-part name of '{source.name}'?"),
                    severity=Severity.BLOCKER,
                )
            )
        if (
            source.modality is DataModality.UNSTRUCTURED
            and source.retrieval is not None
            and not source.retrieval.citation_columns
        ):
            gaps.append(
                SpecGap(
                    field=f"data_sources.{source.name}.retrieval.citation_columns",
                    why_it_matters=(
                        "A retrieval tool that cannot cite cannot be audited, "
                        "and an uncitable answer is not an answer."
                    ),
                    question=(
                        f"Which columns should travel with every passage from "
                        f"'{source.name}' so an answer can cite it? Document "
                        "title, page or section, source path, effective date."
                    ),
                    severity=Severity.HIGH,
                )
            )

    if spec.deployment.target.value == "databricks_apps":
        if not spec.deployment.workspace_host:
            gaps.append(
                SpecGap(
                    field="deployment.workspace_host",
                    why_it_matters="The deploy script and the runbook need it.",
                    question="What is the Databricks workspace URL?",
                    severity=Severity.MEDIUM,
                )
            )
    return gaps
