"""Typed specification models for generating MCP servers.

These models are the contract between the server's tools. A caller describes
intent and data sources; the generators turn that description into a complete,
deployable MCP server project.

Design notes:
  - Every model is JSON-serializable so an MCP client can hold the spec between
    tool calls. The MCP 2026-07-28 specification removes sessions, so state
    travels in the payload rather than on the server.
  - Enums are used for every closed set so the calling model cannot invent a
    value that the generators do not handle.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from mcp_server_creator.environments import EnvironmentProfile

# A server name must leave room for the "mcp-" app-name prefix inside the
# platform's own 50-character limit, so it is capped at 40.
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
APP_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,48}[a-z0-9]$")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class DataModality(str, Enum):
    """Whether a source is rows, free text, or something in between."""

    STRUCTURED = "structured"
    UNSTRUCTURED = "unstructured"
    SEMI_STRUCTURED = "semi_structured"


class SourceKind(str, Enum):
    """Where the data physically lives and how it is reached."""

    UC_TABLE = "uc_table"
    UC_FUNCTION = "uc_function"
    UC_VOLUME_FILES = "uc_volume_files"
    GENIE_SPACE = "genie_space"
    AI_SEARCH_INDEX = "ai_search_index"
    REST_API = "rest_api"
    LOCAL_SNAPSHOT = "local_snapshot"
    DOCUMENT_CORPUS = "document_corpus"


class FieldRole(str, Enum):
    """What a column is for, which decides how tools may use it."""

    IDENTIFIER = "identifier"
    DIMENSION = "dimension"
    MEASURE = "measure"
    DATE = "date"
    TEXT = "text"
    GEO = "geo"
    FLAG = "flag"
    METADATA = "metadata"


class QueryPattern(str, Enum):
    """The shape of the question a tool answers."""

    LOOKUP = "lookup"
    SEARCH = "search"
    SEMANTIC_SEARCH = "semantic_search"
    RANK = "rank"
    AGGREGATE = "aggregate"
    TREND = "trend"
    COMPARE = "compare"
    DESCRIBE_SOURCE = "describe_source"
    VALIDATE = "validate"
    NL_QUERY = "nl_query"


class DeploymentTarget(str, Enum):
    """Where the generated server will run."""

    DATABRICKS_APPS = "databricks_apps"
    LOCAL_STDIO = "local_stdio"
    CONTAINER = "container"


class AuthMode(str, Enum):
    """How callers of the generated server authenticate."""

    NONE = "none"
    DATABRICKS_OAUTH = "databricks_oauth"
    API_KEY = "api_key"


class DataClassification(str, Enum):
    """Sensitivity of the data the generated server exposes."""

    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"


class Severity(str, Enum):
    """How badly a review finding needs fixing."""

    BLOCKER = "blocker"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FieldProfile(BaseModel):
    """One column, document field, or API response field."""

    name: str = Field(description="Field name exactly as the source spells it.")
    data_type: str = Field(
        default="string",
        description="Source type, e.g. 'string', 'bigint', 'date', 'double'.",
    )
    role: FieldRole = Field(
        default=FieldRole.DIMENSION,
        description=(
            "What the field is for. Measures become aggregates, dimensions "
            "become filters and group-bys, dates drive trend tools, text "
            "fields drive search."
        ),
    )
    description: str = Field(default="", description="What the field means.")
    example: str = Field(default="", description="One real example value.")
    nullable: bool = Field(default=True)
    cardinality_hint: int | None = Field(
        default=None,
        description=(
            "Approximate distinct values. Under ~50 means the field can be "
            "exposed as an enum rather than a free-text filter."
        ),
    )
    enum_values: list[str] = Field(
        default_factory=list,
        description="Closed set of allowed values, if the field has one.",
    )

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field name cannot be empty")
        return value.strip()


class RetrievalProfile(BaseModel):
    """How unstructured content is chunked, embedded, and cited."""

    chunk_strategy: str = Field(
        default="recursive",
        description="One of 'recursive', 'by_section', 'by_page', 'whole_doc'.",
    )
    chunk_tokens: int = Field(default=512, ge=64, le=4096)
    chunk_overlap_tokens: int = Field(default=64, ge=0, le=1024)
    embedding_model: str = Field(
        default="databricks-gte-large-en",
        description="Databricks embedding endpoint used for managed embeddings.",
    )
    managed_embeddings: bool = Field(
        default=True,
        description=(
            "True uses Databricks-computed embeddings (embedding_source_column). "
            "False means the source table already holds a vector column."
        ),
    )
    endpoint_name: str = Field(default="", description="AI Search endpoint name.")
    pipeline_type: str = Field(
        default="TRIGGERED", description="'TRIGGERED' or 'CONTINUOUS'."
    )
    primary_key: str = Field(default="id")
    text_column: str = Field(default="content")
    citation_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Columns returned with every hit so answers can cite a source, "
            "e.g. ['doc_title', 'page', 'url']."
        ),
    )


class DataSourceProfile(BaseModel):
    """A normalized description of one source the generated server will read."""

    name: str = Field(description="Short slug used in generated code and docs.")
    kind: SourceKind
    modality: DataModality
    title: str = Field(default="", description="Human-readable name.")
    description: str = Field(
        default="", description="What this source contains and who maintains it."
    )

    catalog: str = Field(default="")
    schema_name: str = Field(default="")
    table: str = Field(default="")
    index_name: str = Field(default="")
    function_name: str = Field(default="")
    volume_path: str = Field(default="")
    genie_space_id: str = Field(default="")
    api_base_url: str = Field(
        default="",
        description=(
            "Fixed origin for a REST source. Operator-controlled; never a "
            "tool argument."
        ),
    )
    snapshot_path: str = Field(
        default="",
        description="Repo-relative path of a data file packaged with the server.",
    )

    grain: str = Field(
        default="",
        description="What one row or document represents, e.g. 'one contract'.",
    )
    row_count_hint: int | None = Field(default=None)
    update_cadence: str = Field(
        default="unknown",
        description="How often the source refreshes, e.g. 'monthly, ~32-day lag'.",
    )
    as_of: str = Field(default="", description="Date the data currently reflects.")
    fields: list[FieldProfile] = Field(default_factory=list)
    retrieval: RetrievalProfile | None = Field(
        default=None, description="Required for unstructured sources."
    )
    required_grants: list[str] = Field(
        default_factory=list,
        description="Unity Catalog grants the app service principal needs.",
    )
    notes: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _slug(cls, value: str) -> str:
        value = value.strip().lower().replace(" ", "_")
        if not IDENTIFIER_RE.match(value):
            raise ValueError(
                "source name must be a lowercase identifier, e.g. 'contracts'"
            )
        return value

    @model_validator(mode="after")
    def _check_coordinates(self) -> DataSourceProfile:
        if self.kind is SourceKind.UC_TABLE and not (
            self.catalog and self.schema_name and self.table
        ):
            raise ValueError("uc_table sources need catalog, schema_name, and table")
        if self.kind is SourceKind.AI_SEARCH_INDEX and not self.index_name:
            raise ValueError("ai_search_index sources need index_name")
        if self.kind is SourceKind.GENIE_SPACE and not self.genie_space_id:
            raise ValueError("genie_space sources need genie_space_id")
        if self.kind is SourceKind.REST_API and not self.api_base_url:
            raise ValueError("rest_api sources need api_base_url")
        if self.kind is SourceKind.LOCAL_SNAPSHOT and not self.snapshot_path:
            raise ValueError("local_snapshot sources need snapshot_path")
        if self.modality is DataModality.UNSTRUCTURED and self.retrieval is None:
            self.retrieval = RetrievalProfile()
        return self

    @property
    def full_table_name(self) -> str:
        """Fully qualified Unity Catalog name, or an empty string."""
        parts = [p for p in (self.catalog, self.schema_name, self.table) if p]
        return ".".join(parts) if len(parts) == 3 else ""

    def measures(self) -> list[FieldProfile]:
        return [f for f in self.fields if f.role is FieldRole.MEASURE]

    def dimensions(self) -> list[FieldProfile]:
        return [f for f in self.fields if f.role is FieldRole.DIMENSION]

    def dates(self) -> list[FieldProfile]:
        return [f for f in self.fields if f.role is FieldRole.DATE]

    def identifiers(self) -> list[FieldProfile]:
        return [f for f in self.fields if f.role is FieldRole.IDENTIFIER]

    def text_fields(self) -> list[FieldProfile]:
        return [f for f in self.fields if f.role is FieldRole.TEXT]


class ParamPlan(BaseModel):
    """One input parameter of a planned tool."""

    name: str
    python_type: str = Field(
        default="str",
        description="Rendered annotation, e.g. 'str', 'int', 'list[str]'.",
    )
    description: str = ""
    required: bool = True
    default: Any | None = None
    enum_values: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    source_field: str = Field(
        default="", description="Field this parameter filters or selects."
    )

    @field_validator("name")
    @classmethod
    def _ident(cls, value: str) -> str:
        value = value.strip()
        if not IDENTIFIER_RE.match(value):
            raise ValueError(f"'{value}' is not a valid parameter name")
        return value


class ToolPlan(BaseModel):
    """One planned tool: the job it does and the contract it exposes."""

    name: str = Field(description="Tool name, prefixed and snake_case.")
    title: str = Field(default="", description="Short display title.")
    goal: str = Field(
        description=(
            "The user's job in their own words, e.g. 'tell me which of my "
            "contracts expire this quarter'. Not an endpoint description."
        )
    )
    pattern: QueryPattern
    source: str = Field(description="name of the DataSourceProfile it reads.")
    params: list[ParamPlan] = Field(default_factory=list)
    returns: str = Field(
        default="",
        description="Shape of the return value, described for the model.",
    )
    read_only: bool = True
    idempotent: bool = True
    open_world: bool = True
    paginated: bool = False
    error_hints: list[str] = Field(
        default_factory=list,
        description="Actionable hints the tool returns when it cannot answer.",
    )
    freshness_note: str = Field(default="")
    eval_seeds: list[str] = Field(
        default_factory=list,
        description="Draft golden questions this tool should be able to answer.",
    )

    @field_validator("name")
    @classmethod
    def _tool_name(cls, value: str) -> str:
        value = value.strip()
        if not TOOL_NAME_RE.match(value):
            raise ValueError(f"'{value}' must be snake_case, 3-64 chars, starting a-z")
        return value

    @property
    def module_name(self) -> str:
        """File name for the generated tool module."""
        return self.name


class EvaluationPlan(BaseModel):
    """How the generated server will be measured."""

    dataset_name: str = Field(default="")
    golden_question_count: int = Field(default=20, ge=5, le=200)
    include_decoys: bool = Field(
        default=True,
        description="Cases that look answerable but must return 'nothing found'.",
    )
    include_no_action: bool = Field(
        default=True, description="Cases whose correct answer is 'nothing to do'."
    )
    include_out_of_scope: bool = Field(default=True)
    include_injection: bool = Field(
        default=True,
        description="A prompt-injection string planted in retrieved content.",
    )
    baseline_without_tools: bool = Field(
        default=True, description="Measure the same questions with no server."
    )
    repeats: int = Field(default=3, ge=1, le=10)
    target_accuracy: float = Field(default=0.85, ge=0.0, le=1.0)
    agent_model: str = Field(default="databricks-claude-sonnet-4-5")
    judge_model: str = Field(default="databricks-claude-opus-4-1")


class DeploymentPlan(BaseModel):
    """Where and how the generated server is deployed."""

    target: DeploymentTarget = DeploymentTarget.DATABRICKS_APPS
    app_name: str = Field(
        default="",
        description=(
            "Databricks App name. Must start with 'mcp-' so AI Playground and "
            "Unity Gateway recognize it as an MCP server."
        ),
    )
    workspace_host: str = Field(default="")
    source_code_path: str = Field(
        default="", description="Absolute /Workspace path the app deploys from."
    )
    sql_warehouse_resource: str = Field(
        default="sql_warehouse",
        description="Resource key bound in app.yaml via valueFrom.",
    )
    secret_scope: str = Field(default="")
    auth_mode: AuthMode = AuthMode.DATABRICKS_OAUTH
    use_bundle: bool = Field(
        default=True,
        description="Emit databricks.yml so resources and grants are declared.",
    )

    @model_validator(mode="after")
    def _check_app_name(self) -> DeploymentPlan:
        if self.target is DeploymentTarget.DATABRICKS_APPS and self.app_name:
            if not self.app_name.startswith("mcp-"):
                raise ValueError(
                    "Databricks App names for MCP servers must start with "
                    "'mcp-' or AI Playground will not recognize the server"
                )
            if not APP_SLUG_RE.match(self.app_name):
                raise ValueError(
                    "app_name must be 3 to 50 lowercase letters, numbers, and hyphens"
                )
        return self


class ServerSpec(BaseModel):
    """The complete description of an MCP server to generate."""

    server_name: str = Field(
        description="Slug for the server, e.g. 'contract-triage-mcp'."
    )
    title: str = Field(default="", description="Display name for MCP clients.")
    purpose: str = Field(description="One sentence: what this server lets an agent do.")
    primary_user: str = Field(
        default="",
        description="The person whose job gets easier, e.g. 'budget analyst'.",
    )
    jobs_to_be_done: list[str] = Field(
        default_factory=list,
        description="Real questions the user asks today, in their words.",
    )
    domain: str = Field(default="")
    out_of_scope: list[str] = Field(
        default_factory=list,
        description="Questions the server must decline, to keep scope honest.",
    )
    data_sources: list[DataSourceProfile] = Field(default_factory=list)
    tools: list[ToolPlan] = Field(default_factory=list)
    deployment: DeploymentPlan = Field(default_factory=DeploymentPlan)
    evaluation: EvaluationPlan = Field(default_factory=EvaluationPlan)
    environment: EnvironmentProfile = Field(
        default=EnvironmentProfile.UNKNOWN,
        description=(
            "Which Databricks environment this will run in. A commercial "
            "workspace and an IL5 GovCloud workspace do not have the same "
            "features, and a design valid in one can fail to deploy in the "
            "other."
        ),
    )
    classification: DataClassification = DataClassification.PUBLIC
    read_only: bool = Field(
        default=True,
        description="True means no tool mutates the source system.",
    )
    pii_present: bool = Field(default=False)
    tool_prefix: str = Field(
        default="",
        description="Prefix for every tool name, e.g. 'contract'.",
    )
    license_name: str = Field(default="MIT")
    repo_url: str = Field(default="")
    maintainer: str = Field(default="")

    @field_validator("server_name")
    @classmethod
    def _server_slug(cls, value: str) -> str:
        value = value.strip().lower().replace("_", "-").replace(" ", "-")
        if not SLUG_RE.match(value):
            raise ValueError(
                "server_name must be a lowercase slug of 3 to 40 "
                "characters, e.g. 'contract-triage-mcp'"
            )
        return value

    @model_validator(mode="after")
    def _derive_defaults(self) -> ServerSpec:
        if not self.title:
            words = self.server_name.replace("-", " ").split()
            self.title = " ".join(w.capitalize() for w in words)
        if not self.tool_prefix:
            first = self.server_name.split("-")[0]
            self.tool_prefix = first.replace("-", "_")
        if not self.deployment.app_name:
            base = self.server_name
            # Avoid "mcp-contract-triage-mcp": the platform needs the prefix,
            # so a trailing "-mcp" on the server name is redundant.
            if base.endswith("-mcp"):
                base = base[: -len("-mcp")]
            self.deployment.app_name = (
                base if base.startswith("mcp-") else f"mcp-{base}"
            )
        if not self.evaluation.dataset_name:
            self.evaluation.dataset_name = f"{self.tool_prefix}-eval-0"
        return self

    @property
    def package_name(self) -> str:
        """Importable Python package name for the generated server."""
        name = self.server_name.replace("-", "_")
        return name if IDENTIFIER_RE.match(name) else f"s_{name}"

    def source_by_name(self, name: str) -> DataSourceProfile | None:
        for source in self.data_sources:
            if source.name == name:
                return source
        return None

    def has_unstructured(self) -> bool:
        return any(s.modality is DataModality.UNSTRUCTURED for s in self.data_sources)

    def has_structured(self) -> bool:
        return any(s.modality is DataModality.STRUCTURED for s in self.data_sources)

    def needs_databricks_sdk(self) -> bool:
        kinds = {s.kind for s in self.data_sources}
        return bool(
            kinds
            & {
                SourceKind.UC_TABLE,
                SourceKind.UC_FUNCTION,
                SourceKind.UC_VOLUME_FILES,
                SourceKind.GENIE_SPACE,
                SourceKind.AI_SEARCH_INDEX,
            }
        )


class SpecGap(BaseModel):
    """Something missing from a spec, with the question that would fill it."""

    field: str
    why_it_matters: str
    question: str
    severity: Severity = Severity.MEDIUM
    suggested_default: str = ""


class GeneratedFile(BaseModel):
    """One file in a generated project."""

    path: str = Field(description="Project-relative path with forward slashes.")
    content: str
    purpose: str = Field(default="")
    executable: bool = Field(default=False)

    @property
    def line_count(self) -> int:
        return self.content.count("\n") + 1

    @property
    def byte_count(self) -> int:
        return len(self.content.encode("utf-8"))


class FileManifest(BaseModel):
    """A complete set of generated files, ready to review or write to disk."""

    server_name: str
    package_name: str
    files: list[GeneratedFile] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def add(
        self,
        path: str,
        content: str,
        purpose: str = "",
        executable: bool = False,
    ) -> None:
        """Append a file, replacing any earlier file at the same path."""
        self.files = [f for f in self.files if f.path != path]
        self.files.append(
            GeneratedFile(
                path=path,
                content=content,
                purpose=purpose,
                executable=executable,
            )
        )

    def merge(self, other: FileManifest) -> None:
        """Fold another manifest's files and notes into this one."""
        for generated in other.files:
            self.add(
                generated.path,
                generated.content,
                generated.purpose,
                generated.executable,
            )
        self.next_steps.extend(other.next_steps)
        self.warnings.extend(other.warnings)

    def paths(self) -> list[str]:
        return sorted(f.path for f in self.files)

    def index(self) -> list[dict[str, Any]]:
        """Table of contents without file bodies, for a cheap overview."""
        return [
            {
                "path": f.path,
                "purpose": f.purpose,
                "lines": f.line_count,
                "bytes": f.byte_count,
            }
            for f in sorted(self.files, key=lambda f: f.path)
        ]

    @property
    def total_bytes(self) -> int:
        return sum(f.byte_count for f in self.files)


class Finding(BaseModel):
    """One review finding against the design and security rubric."""

    check: str
    severity: Severity
    summary: str
    evidence: str = ""
    fix: str = ""
    criterion: str = Field(
        default="",
        description="Judging or rubric category this finding rolls up to.",
    )


class ReviewReport(BaseModel):
    """Scored output of a design review."""

    subject: str
    findings: list[Finding] = Field(default_factory=list)
    scores: dict[str, int] = Field(default_factory=dict)
    passed_checks: list[str] = Field(default_factory=list)
    verdict: str = ""

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.BLOCKER]
