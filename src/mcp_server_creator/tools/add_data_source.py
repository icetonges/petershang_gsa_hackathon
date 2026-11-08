"""Normalize whatever the user has into a data source on the design.

Phase 2. Accepts a Unity Catalog table name, a CREATE TABLE statement, a CSV
header with sample rows, DESCRIBE output, a JSON response sample, or a
description of a document corpus, and appends a normalized profile to the
design.

The field *roles* in the result are what matter: measures unlock aggregates,
dates unlock trends, identifiers unlock lookups, low-cardinality dimensions
become enums, and text fields force a choice between keyword filtering and
semantic retrieval.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field, ValidationError

from mcp_server_creator import designs, environments, live
from mcp_server_creator.models import (
    DataModality,
    DataSourceProfile,
    RetrievalProfile,
    SourceKind,
)
from mcp_server_creator.profiling import (
    default_grants,
    describe_document_corpus,
    looks_like_pii,
    parse_tabular_text,
    profile_summary,
    validate_source_coordinates,
)


def register(mcp: FastMCP) -> None:
    """Register the data source profiler with the MCP server."""

    @mcp.tool(
        name="creator_add_data_source",
        annotations={
            "title": "Profile a data source and add it to the design",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    def creator_add_data_source(
        name: Annotated[
            str,
            Field(
                description=(
                    "Short lowercase slug used in generated code, e.g. "
                    "'contracts' or 'policy_docs'."
                )
            ),
        ],
        kind: Annotated[SourceKind, Field(description="Where the data lives.")],
        modality: Annotated[
            DataModality,
            Field(description="Rows, free text, or something in between."),
        ],
        design_id: Annotated[
            str, Field(description=designs.DESIGN_ID_DESCRIPTION)
        ] = "",
        spec_json: Annotated[
            str, Field(description=designs.SPEC_JSON_DESCRIPTION)
        ] = "",
        schema_text: Annotated[
            str,
            Field(
                description=(
                    "Paste anything that describes the fields: a CREATE "
                    "TABLE statement, a CSV header with a few rows, "
                    "DESCRIBE output, or a JSON response sample. Leave empty "
                    "for a Unity Catalog table if live inspection works."
                )
            ),
        ] = "",
        catalog: Annotated[str, Field(description="Unity Catalog name.")] = "",
        schema_name: Annotated[str, Field(description="Schema name.")] = "",
        table: Annotated[str, Field(description="Table name.")] = "",
        index_name: Annotated[
            str,
            Field(description="Full AI Search index name, catalog.schema.index."),
        ] = "",
        genie_space_id: Annotated[str, Field(description="Genie space id.")] = "",
        function_name: Annotated[
            str, Field(description="Unity Catalog function name.")
        ] = "",
        volume_path: Annotated[
            str,
            Field(description="Volume path, /Volumes/<catalog>/<schema>/<volume>/..."),
        ] = "",
        api_base_url: Annotated[
            str,
            Field(
                description=(
                    "Fixed origin for a REST source. Operator controlled, "
                    "never a tool argument on the generated server."
                )
            ),
        ] = "",
        snapshot_path: Annotated[
            str,
            Field(
                description=(
                    "Path of a data file packaged with the server, relative "
                    "to its data directory, e.g. 'contracts.csv'. This is "
                    "the one source kind that needs no credentials at all."
                )
            ),
        ] = "",
        title: Annotated[str, Field(description="Human-readable name.")] = "",
        description: Annotated[
            str, Field(description="What it contains and who maintains it.")
        ] = "",
        grain: Annotated[
            str,
            Field(
                description=(
                    "What one row or document represents, e.g. 'one contract "
                    "award, not one modification'."
                )
            ),
        ] = "",
        update_cadence: Annotated[
            str,
            Field(description="How often it refreshes, e.g. 'monthly, 32-day lag'."),
        ] = "",
        as_of: Annotated[
            str, Field(description="The date the data currently reflects.")
        ] = "",
        document_count: Annotated[
            int,
            Field(description="For a document corpus: roughly how many.", ge=0),
        ] = 0,
        text_column: Annotated[
            str,
            Field(description="For unstructured: the column holding chunk text."),
        ] = "",
        citation_columns: Annotated[
            list[str],
            Field(
                description=(
                    "For unstructured: columns returned with every passage "
                    "so an answer can cite it, e.g. "
                    "['doc_title','page','url']. Retrieval that cannot cite "
                    "cannot be audited."
                )
            ),
        ] = [],
        search_endpoint: Annotated[
            str, Field(description="AI Search endpoint name.")
        ] = "",
        try_live_inspection: Annotated[
            bool,
            Field(
                description=(
                    "For a Unity Catalog table, read the real schema, row "
                    "count and distinct values when credentials allow."
                )
            ),
        ] = True,
    ) -> dict[str, Any]:
        """Profile one source, add it to the design, and say what it supports.

        Returns: `design_id`, `summary` with the fields grouped by role,
        `enum_candidates`, `observations` naming what the data cannot
        support, `required_grants` for the app service principal,
        `environment_fit` for this source kind, and `inspection` saying
        whether the fields came from a live read or from pasted text.
        """
        spec, _, failure = designs.resolve_result(design_id, spec_json)
        if failure:
            return failure
        assert spec is not None

        fields: list[Any] = []
        inspection: dict[str, Any] = {"source": "none"}

        wants_live = (
            try_live_inspection
            and kind is SourceKind.UC_TABLE
            and bool(catalog and schema_name and table)
            and not schema_text
        )
        if wants_live:
            valid, reason = validate_source_coordinates(catalog, schema_name, table)
            if not valid:
                return {
                    "error": f"Invalid Unity Catalog coordinates: {reason}.",
                    "hint": (
                        "Catalog, schema and table must be plain "
                        "identifiers: letters, digits and underscores."
                    ),
                }
            if live.available():
                try:
                    fields, inspection = live.profile_table(catalog, schema_name, table)
                except (ValueError, live.LiveInspectionError) as error:
                    inspection = {
                        "source": "none",
                        "attempted": True,
                        "why": str(error),
                    }
            else:
                inspection = {
                    "source": "none",
                    "attempted": False,
                    "why": (
                        "Live inspection is not configured on this server. "
                        "Paste the schema instead; it works just as well."
                    ),
                }

        if not fields and schema_text:
            fields = parse_tabular_text(schema_text)
            inspection = {"source": "pasted_text", "parsed_fields": len(fields)}

        if not fields and modality is DataModality.UNSTRUCTURED:
            fields = describe_document_corpus(
                doc_count=document_count, average_pages=0.0, file_types=[]
            )
            inspection = {
                "source": "corpus_defaults",
                "note": (
                    "A standard chunk schema was assumed. Replace it once "
                    "the chunking table exists."
                ),
            }

        for field in fields:
            if looks_like_pii(field.name):
                field.pii = True

        retrieval = None
        if modality is DataModality.UNSTRUCTURED or kind is SourceKind.AI_SEARCH_INDEX:
            retrieval = RetrievalProfile(
                text_column=text_column or "content",
                citation_columns=[c for c in citation_columns if c.strip()],
                endpoint_name=search_endpoint.strip(),
            )

        try:
            profile = DataSourceProfile(
                name=name,
                kind=kind,
                modality=modality,
                title=title.strip(),
                description=description.strip(),
                catalog=catalog.strip(),
                schema_name=schema_name.strip(),
                table=table.strip(),
                index_name=index_name.strip(),
                function_name=function_name.strip(),
                volume_path=volume_path.strip(),
                genie_space_id=genie_space_id.strip(),
                api_base_url=api_base_url.strip(),
                snapshot_path=snapshot_path.strip(),
                grain=grain.strip(),
                update_cadence=(update_cadence or "unknown").strip(),
                as_of=as_of.strip(),
                row_count_hint=inspection.get("row_count"),
                fields=fields,
                retrieval=retrieval,
                required_grants=default_grants(kind, modality),
            )
        except ValidationError as error:
            return {
                "error": "The source profile is not valid.",
                "details": error.errors(include_url=False),
                "hint": (
                    "A uc_table needs catalog, schema_name and table; an "
                    "ai_search_index needs index_name; a rest_api needs "
                    "api_base_url; a local_snapshot needs snapshot_path."
                ),
            }

        spec.data_sources = [s for s in spec.data_sources if s.name != profile.name]
        spec.data_sources.append(profile)
        if any(field.pii for field in profile.fields):
            spec.pii_present = True
        designs.persist(spec, design_id)

        summary = profile_summary(profile.fields)
        observations = list(summary["observations"])
        if (
            profile.modality is DataModality.UNSTRUCTURED
            and retrieval is not None
            and not retrieval.citation_columns
        ):
            observations.append(
                "No citation columns are set. Retrieval that cannot cite "
                "cannot be audited; add document title, page or section, "
                "source path and effective date."
            )
        if not profile.as_of:
            observations.append(
                "No as-of date. Every tool result reports one, so without "
                "it the agent will imply the data is current."
            )
        if not profile.grain:
            observations.append(
                "No grain recorded. One row per modification is not one row "
                "per award, and a model will miscount."
            )

        fit_findings = environments.check_design(
            spec.environment, [profile.kind.value], uses_in_process_state=False
        )
        environment_fit: dict[str, Any] = {
            "profile": spec.environment.value,
            "findings": [f.model_dump() for f in fit_findings],
        }
        if profile.modality is DataModality.UNSTRUCTURED:
            environment_fit["retrieval"] = environments.retrieval_guidance(
                spec.environment
            )

        return {
            "design_id": design_id or "",
            "source": profile.name,
            "sources_on_design": [s.name for s in spec.data_sources],
            "summary": summary,
            "enum_candidates": summary["enum_candidates"],
            "observations": observations,
            "required_grants": profile.required_grants,
            "environment_fit": environment_fit,
            "inspection": inspection,
            "spec": spec.model_dump(mode="json") if not design_id else None,
            "next_step": (
                "Repeat for any other source, then call creator_design_tools."
            ),
        }
