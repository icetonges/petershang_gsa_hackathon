"""Turn whatever the user has into a normalized data source profile.

A person describing their data rarely has a clean schema to hand. They have a
CREATE TABLE statement, a CSV header with a few rows, a JSON response sample,
a Unity Catalog table name, or a folder of PDFs. This module accepts any of
those and produces `FieldProfile` objects the tool designer can reason about.

Live Unity Catalog inspection is attempted only when the server is configured
for it (see `config.Settings.live_profiling_available`); otherwise everything
here works offline on pasted text.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from mcp_server_creator.models import (
    DataModality,
    FieldProfile,
    FieldRole,
    SourceKind,
)
from mcp_server_creator.utils import validate_uc_identifier

# The name class deliberately excludes spaces: allowing them made the pattern
# swallow the type ("contract_id STRING NOT NULL" as one name). A quoted name
# containing spaces is not supported, which is a fair trade for correctness.
DDL_COLUMN_RE = re.compile(
    r"^\s*[`\"\[]?(?P<name>[A-Za-z_][A-Za-z0-9_]*)[`\"\]]?\s+"
    r"(?P<type>[A-Za-z][A-Za-z0-9_]*(?:\s*\([^)]*\))?(?:<[^>]*>)?)",
)

DATE_TYPES = {"date", "timestamp", "timestamp_ntz", "datetime"}
INT_TYPES = {"int", "integer", "bigint", "smallint", "tinyint", "long"}
FLOAT_TYPES = {"double", "float", "real", "decimal", "numeric", "number"}
BOOL_TYPES = {"boolean", "bool", "bit"}

DATE_NAME_HINTS = (
    "date",
    "_dt",
    "year",
    "month",
    "quarter",
    "fy",
    "period",
    "timestamp",
    "created",
    "updated",
    "expires",
    "start",
    "end",
)
MEASURE_NAME_HINTS = (
    "amount",
    "amt",
    "total",
    "count",
    "qty",
    "quantity",
    "value",
    "cost",
    "price",
    "balance",
    "obligat",
    "outlay",
    "spend",
    "budget",
    "rate",
    "pct",
    "percent",
    "score",
    "duration",
    "days",
    "sum",
    "avg",
    "revenue",
    "salary",
)
IDENTIFIER_NAME_HINTS = (
    "id",
    "uuid",
    "guid",
    "key",
    "number",
    "no",
    "code",
    "piid",
    "tas",
    "ein",
    "duns",
    "uei",
)
TEXT_NAME_HINTS = (
    "description",
    "desc",
    "text",
    "body",
    "content",
    "abstract",
    "narrative",
    "comment",
    "notes",
    "summary",
    "title",
    "name",
    "statement",
    "justification",
)
GEO_NAME_HINTS = ("lat", "lon", "lng", "latitude", "longitude", "zip", "geohash")
FLAG_NAME_PREFIXES = ("is_", "has_", "flag_", "can_", "should_")


MIN_SUBSTRING_HINT = 4


def _matches_hint(name: str, hints: tuple[str, ...]) -> bool:
    """Match a field name against hints without false substring hits.

    A plain substring test classified `vendor_name` as a date, because
    "vendor" contains "end". Short hints must therefore match a whole
    underscore-separated token; only longer hints may match as substrings.
    """
    lower = name.strip().lower()
    tokens = set(lower.replace("-", "_").split("_"))
    for hint in hints:
        if hint.startswith("_"):
            if lower.endswith(hint):
                return True
            continue
        if hint in tokens:
            return True
        if len(hint) >= MIN_SUBSTRING_HINT and hint in lower:
            return True
    return False


def normalize_type(raw: str) -> str:
    """Reduce a declared type to a lowercase base type name."""
    base = raw.strip().lower()
    base = re.sub(r"\(.*?\)", "", base)
    base = re.sub(r"<.*?>", "", base)
    return base.strip()


def infer_role(
    name: str,
    data_type: str,
    samples: list[str] | None = None,
    cardinality: int | None = None,
) -> FieldRole:
    """Infer what a field is for from its name, type, and sample values."""
    lower = name.strip().lower()
    base = normalize_type(data_type)
    samples = samples or []

    if base in BOOL_TYPES or lower.startswith(FLAG_NAME_PREFIXES):
        return FieldRole.FLAG
    if base in DATE_TYPES or _matches_hint(lower, DATE_NAME_HINTS):
        if base in DATE_TYPES or base not in FLOAT_TYPES | INT_TYPES:
            return FieldRole.DATE
        if _matches_hint(lower, ("year", "month", "quarter", "fy")):
            return FieldRole.DATE
    if any(lower == hint or lower.endswith(f"_{hint}") for hint in GEO_NAME_HINTS):
        return FieldRole.GEO
    if base in INT_TYPES | FLOAT_TYPES:
        if _matches_hint(lower, MEASURE_NAME_HINTS):
            return FieldRole.MEASURE
        if any(
            lower == hint or lower.endswith(f"_{hint}")
            for hint in IDENTIFIER_NAME_HINTS
        ):
            return FieldRole.IDENTIFIER
        return FieldRole.MEASURE
    if any(
        lower == hint or lower.endswith(f"_{hint}") or lower.startswith(f"{hint}_")
        for hint in IDENTIFIER_NAME_HINTS
    ):
        return FieldRole.IDENTIFIER
    longest = max((len(s) for s in samples), default=0)
    if longest > 120 or _matches_hint(lower, TEXT_NAME_HINTS):
        return FieldRole.TEXT
    if cardinality is not None and cardinality <= 50:
        return FieldRole.DIMENSION
    return FieldRole.DIMENSION


def parse_create_table_ddl(ddl: str) -> list[FieldProfile]:
    """Extract columns from a CREATE TABLE statement.

    Tolerates Databricks, Postgres, and SQL Server spellings, nested complex
    types, and trailing table properties.
    """
    body = ddl
    open_paren = ddl.find("(")
    if open_paren != -1:
        depth = 0
        for index in range(open_paren, len(ddl)):
            char = ddl[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    body = ddl[open_paren + 1 : index]
                    break

    fields: list[FieldProfile] = []
    for raw_line in _split_top_level(body):
        line = raw_line.strip().rstrip(",")
        if not line:
            continue
        upper = line.upper()
        if upper.startswith(
            (
                "PRIMARY KEY",
                "FOREIGN KEY",
                "CONSTRAINT",
                "UNIQUE",
                "CHECK",
                "PARTITIONED",
                "USING",
                "COMMENT",
                "TBLPROPERTIES",
                "INDEX",
                "KEY ",
            )
        ):
            continue
        match = DDL_COLUMN_RE.match(line)
        if not match:
            continue
        name = match.group("name").strip()
        data_type = normalize_type(match.group("type"))
        comment = ""
        comment_match = re.search(r"COMMENT\s+'([^']*)'", line, re.IGNORECASE)
        if comment_match:
            comment = comment_match.group(1)
        nullable = "NOT NULL" not in upper
        fields.append(
            FieldProfile(
                name=name,
                data_type=data_type,
                role=infer_role(name, data_type),
                description=comment,
                nullable=nullable,
            )
        )
    return fields


def _split_top_level(body: str) -> list[str]:
    """Split a DDL column list on commas that are not inside brackets."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char in "(<[":
            depth += 1
        elif char in ")>]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def parse_csv_sample(text: str, max_rows: int = 200) -> list[FieldProfile]:
    """Infer fields from a CSV header plus however many rows were pasted."""
    cleaned = text.strip()
    if not cleaned:
        return []
    try:
        dialect = csv.Sniffer().sniff(cleaned[:4096], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.reader(io.StringIO(cleaned), delimiter=delimiter)
    rows = [row for _, row in zip(range(max_rows + 1), reader)]
    if not rows:
        return []
    header = [h.strip() or f"column_{i}" for i, h in enumerate(rows[0], start=1)]
    data_rows = rows[1:]

    fields: list[FieldProfile] = []
    for position, name in enumerate(header):
        values = [
            row[position].strip()
            for row in data_rows
            if position < len(row) and row[position].strip()
        ]
        data_type = _infer_type_from_values(values)
        distinct = len(set(values))
        has_blanks = len(values) < len(data_rows)
        role = infer_role(name, data_type, values, distinct or None)
        enum_values: list[str] = []
        if (
            role is FieldRole.DIMENSION
            and values
            and distinct <= 12
            and distinct < len(values)
        ):
            enum_values = sorted(set(values))
        fields.append(
            FieldProfile(
                name=name,
                data_type=data_type,
                role=role,
                example=values[0] if values else "",
                nullable=has_blanks or not values,
                cardinality_hint=distinct or None,
                enum_values=enum_values,
            )
        )
    return fields


def _infer_type_from_values(values: list[str]) -> str:
    """Guess a column type from sample strings."""
    if not values:
        return "string"
    sample = values[:200]
    if all(v.lower() in {"true", "false", "0", "1", "y", "n"} for v in sample):
        if any(v.lower() in {"true", "false", "y", "n"} for v in sample):
            return "boolean"
    if all(re.fullmatch(r"-?\d+", v) for v in sample):
        return "bigint"
    if all(
        re.fullmatch(r"-?[\d,]*\.?\d+(?:[eE][-+]?\d+)?", v.replace("$", ""))
        for v in sample
    ):
        return "double"
    if all(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T].*)?", v)
        or re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", v)
        for v in sample
    ):
        return "date"
    return "string"


def parse_json_sample(text: str) -> list[FieldProfile]:
    """Infer fields from a JSON object, or the first object in an array.

    Nested objects are flattened one level with dotted names, which is how an
    API response is usually projected into tool output.
    """
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    records: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "records", "rows"):
            value = payload.get(key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                records = [row for row in value if isinstance(row, dict)]
                break
        if not records:
            records = [payload]
    elif isinstance(payload, list):
        records = [row for row in payload if isinstance(row, dict)]
    if not records:
        return []

    flat: dict[str, list[str]] = {}
    for record in records[:200]:
        for key, value in _flatten(record).items():
            flat.setdefault(key, [])
            if value is not None:
                flat[key].append(str(value))

    fields: list[FieldProfile] = []
    for name, values in flat.items():
        data_type = _infer_type_from_values(values)
        distinct = len(set(values))
        fields.append(
            FieldProfile(
                name=name,
                data_type=data_type,
                role=infer_role(name, data_type, values, distinct or None),
                example=values[0] if values else "",
                nullable=len(values) < len(records),
                cardinality_hint=distinct or None,
            )
        )
    return fields


def _flatten(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten one level of nesting into dotted keys."""
    flat: dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            for inner_key, inner_value in value.items():
                if not isinstance(inner_value, (dict, list)):
                    flat[f"{name}.{inner_key}"] = inner_value
        elif isinstance(value, list):
            flat[name] = f"[{len(value)} items]" if value else None
        else:
            flat[name] = value
    return flat


def parse_tabular_text(text: str) -> list[FieldProfile]:
    """Best-effort parse of pasted schema text in any supported shape."""
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith(("{", "[")):
        fields = parse_json_sample(stripped)
        if fields:
            return fields
    if re.search(r"\bcreate\s+(or\s+replace\s+)?(external\s+)?table\b", stripped, re.I):
        fields = parse_create_table_ddl(stripped)
        if fields:
            return fields
    if _looks_like_column_list(stripped):
        fields = _parse_column_list(stripped)
        if fields:
            return fields
    fields = parse_csv_sample(stripped)
    if fields:
        return fields
    return parse_create_table_ddl(stripped)


def _looks_like_column_list(text: str) -> bool:
    """True for `name type` or `name: type` lines, as DESCRIBE TABLE prints."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    matches = 0
    for line in lines[:20]:
        if re.match(
            r"^[`\"]?[A-Za-z_][A-Za-z0-9_]*[`\"]?\s*[:|\t ]\s*"
            r"(string|int|integer|bigint|double|float|decimal|boolean|bool|"
            r"date|timestamp|array|map|struct|varchar|text|numeric|long)",
            line,
            re.IGNORECASE,
        ):
            matches += 1
    return matches >= max(2, len(lines[:20]) // 2)


def _parse_column_list(text: str) -> list[FieldProfile]:
    """Parse `name type [comment]` lines from DESCRIBE-style output."""
    fields: list[FieldProfile] = []
    for line in text.splitlines():
        line = line.strip().strip("|").strip()
        if not line or line.startswith(("#", "--", "+")):
            continue
        parts = re.split(r"[:|\t]|\s{2,}|\s", line, maxsplit=2)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) < 2:
            continue
        name, raw_type = parts[0].strip('`"'), parts[1]
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            continue
        data_type = normalize_type(raw_type)
        description = parts[2].strip() if len(parts) > 2 else ""
        fields.append(
            FieldProfile(
                name=name,
                data_type=data_type,
                role=infer_role(name, data_type),
                description=description,
            )
        )
    return fields


def describe_document_corpus(
    doc_count: int,
    average_pages: float,
    file_types: list[str],
    has_tables: bool = False,
) -> list[FieldProfile]:
    """Standard field set for an unstructured corpus heading for AI Search."""
    fields = [
        FieldProfile(
            name="chunk_id",
            data_type="string",
            role=FieldRole.IDENTIFIER,
            description="Stable id for one chunk; the index primary key.",
            nullable=False,
        ),
        FieldProfile(
            name="doc_title",
            data_type="string",
            role=FieldRole.METADATA,
            description="Document title, returned with every hit for citation.",
        ),
        FieldProfile(
            name="source_path",
            data_type="string",
            role=FieldRole.METADATA,
            description="Volume path or URL the chunk came from.",
        ),
        FieldProfile(
            name="page",
            data_type="int",
            role=FieldRole.METADATA,
            description="Page or section number for citation.",
        ),
        FieldProfile(
            name="content",
            data_type="string",
            role=FieldRole.TEXT,
            description="Chunk text; the embedding source column.",
            nullable=False,
        ),
        FieldProfile(
            name="ingested_at",
            data_type="timestamp",
            role=FieldRole.DATE,
            description="When the chunk was indexed, for freshness reporting.",
        ),
    ]
    if has_tables:
        fields.append(
            FieldProfile(
                name="table_markdown",
                data_type="string",
                role=FieldRole.TEXT,
                description=(
                    "Tables extracted as markdown, kept beside prose so "
                    "numbers survive chunking."
                ),
            )
        )
    _ = (doc_count, average_pages, file_types)
    return fields


def profile_summary(fields: list[FieldProfile]) -> dict[str, Any]:
    """Counts and observations a tool designer needs from a field list."""
    by_role: dict[str, list[str]] = {}
    for field in fields:
        by_role.setdefault(field.role.value, []).append(field.name)
    enum_ready = [
        f.name
        for f in fields
        if f.enum_values or (f.cardinality_hint and f.cardinality_hint <= 50)
    ]
    observations: list[str] = []
    if not by_role.get("measure"):
        observations.append(
            "No measures detected, so aggregate and trend tools are not "
            "supported. Lookup, search, and rank tools still are."
        )
    if not by_role.get("date"):
        observations.append(
            "No date field detected, so trend and 'expiring soon' tools are "
            "not supported. Confirm whether a date column exists."
        )
    if not by_role.get("identifier"):
        observations.append(
            "No identifier detected. A lookup tool needs one stable key so "
            "the agent can fetch a single record."
        )
    if by_role.get("text"):
        observations.append(
            "Text fields present. Decide between keyword filtering and "
            "semantic retrieval over an AI Search index."
        )
    return {
        "field_count": len(fields),
        "by_role": by_role,
        "enum_candidates": enum_ready,
        "observations": observations,
    }


def default_grants(kind: SourceKind, modality: DataModality) -> list[str]:
    """Unity Catalog grants an app service principal needs for a source."""
    _ = modality
    if kind is SourceKind.UC_TABLE:
        return [
            "USE CATALOG on the catalog",
            "USE SCHEMA on the schema",
            "SELECT on the table",
            "CAN USE on the SQL warehouse bound in app.yaml",
        ]
    if kind is SourceKind.AI_SEARCH_INDEX:
        return [
            "USE CATALOG and USE SCHEMA on the index's schema",
            "SELECT on the index",
            "SELECT on the Delta table backing the index",
        ]
    if kind is SourceKind.UC_FUNCTION:
        return [
            "USE CATALOG and USE SCHEMA on the function's schema",
            "EXECUTE on the function",
        ]
    if kind is SourceKind.UC_VOLUME_FILES:
        return [
            "USE CATALOG and USE SCHEMA on the volume's schema",
            "READ VOLUME on the volume",
        ]
    if kind is SourceKind.GENIE_SPACE:
        return [
            "CAN RUN on the Genie space",
            "SELECT on the tables the space queries",
            "CAN USE on the space's SQL warehouse",
        ]
    if kind is SourceKind.REST_API:
        return ["No Unity Catalog grant; store the API key as an app secret"]
    return ["None; data ships inside the server image"]


def validate_source_coordinates(
    catalog: str, schema_name: str, table: str
) -> tuple[bool, str]:
    """Check Unity Catalog coordinates before they reach generated SQL."""
    for label, part in (
        ("catalog", catalog),
        ("schema", schema_name),
        ("table", table),
    ):
        if not part:
            return False, f"{label} is missing"
        try:
            validate_uc_identifier(part, label)
        except ValueError as error:
            return False, str(error)
    return True, "ok"
