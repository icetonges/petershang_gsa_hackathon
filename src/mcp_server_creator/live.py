"""Optional live inspection of a real Unity Catalog table.

Everything else in this server works offline on pasted schema text. When the
operator has configured Databricks credentials and a warehouse, profiling can
read the real table instead, which produces better enum candidates and a true
row count.

Failures here are never fatal: the caller falls back to offline parsing and
reports why.
"""

from __future__ import annotations

from typing import Any

from mcp_server_creator.config import settings
from mcp_server_creator.models import FieldProfile
from mcp_server_creator.profiling import infer_role, normalize_type
from mcp_server_creator.utils import quote_uc_name

MAX_DISTINCT_PROBES = 8


class LiveInspectionError(RuntimeError):
    """A sanitized live-inspection failure. Never fatal to a profile call."""


def _connect() -> Any:
    """Open a warehouse connection, preferring OAuth over a token.

    Raises:
        LiveInspectionError: when the connector or credentials are missing.
    """
    try:
        from databricks import sql as dbsql
    except ImportError as error:
        raise LiveInspectionError(
            "databricks-sql-connector is not installed, so live inspection "
            "is unavailable. Install the 'databricks' extra, or paste the "
            "schema instead."
        ) from error

    host = settings.databricks_host.replace("https://", "").rstrip("/")
    if not (host and settings.databricks_warehouse_id):
        raise LiveInspectionError(
            "DATABRICKS_HOST and DATABRICKS_WAREHOUSE_ID are not both set."
        )
    kwargs: dict[str, Any] = {
        "server_hostname": host,
        "http_path": f"/sql/1.0/warehouses/{settings.databricks_warehouse_id}",
    }
    if settings.databricks_client_id and settings.databricks_client_secret:
        from databricks.sdk.core import Config, oauth_service_principal

        config = Config(
            host=f"https://{host}",
            client_id=settings.databricks_client_id,
            client_secret=settings.databricks_client_secret,
        )

        def credentials_provider():
            return oauth_service_principal(config)

        kwargs["credentials_provider"] = credentials_provider
    elif settings.databricks_token:
        kwargs["access_token"] = settings.databricks_token
    else:
        raise LiveInspectionError("No Databricks credentials are configured.")

    try:
        return dbsql.connect(**kwargs)
    except Exception:
        raise LiveInspectionError(
            "Could not connect to the warehouse. Check the host, the "
            "warehouse id, and that the identity has CAN USE on it."
        ) from None


def profile_table(
    catalog: str, schema: str, table: str
) -> tuple[list[FieldProfile], dict[str, Any]]:
    """Read a table's columns, row count, and enum candidates.

    Raises:
        ValueError: if a name part is not a plain identifier.
        LiveInspectionError: with a sanitized message on any failure.
    """
    qualified = quote_uc_name(catalog, schema, table)
    connection = _connect()
    details: dict[str, Any] = {"source": "live", "table": f"{catalog}.{schema}.{table}"}
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DESCRIBE TABLE {qualified}")
                described = cursor.fetchall()
                columns = [c[0] for c in cursor.description]

            rows: list[dict[str, Any]] = [dict(zip(columns, row)) for row in described]
            fields: list[FieldProfile] = []
            for row in rows:
                name = str(row.get("col_name") or "").strip()
                if not name or name.startswith("#"):
                    continue
                data_type = normalize_type(str(row.get("data_type") or "string"))
                comment = str(row.get("comment") or "")
                fields.append(
                    FieldProfile(
                        name=name,
                        data_type=data_type,
                        role=infer_role(name, data_type),
                        description=comment if comment != "None" else "",
                    )
                )

            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) AS row_count FROM {qualified}")
                counted = cursor.fetchone()
            details["row_count"] = int(counted[0]) if counted else None

            probes = [
                field for field in fields if field.data_type in {"string", "varchar"}
            ][:MAX_DISTINCT_PROBES]
            if probes:
                selects = ", ".join(
                    f"approx_count_distinct({quote_uc_name(field.name)}) AS d_{index}"
                    for index, field in enumerate(probes)
                )
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT {selects} FROM {qualified}")
                    distinct_row = cursor.fetchone()
                if distinct_row is not None:
                    for index, field in enumerate(probes):
                        value = distinct_row[index]
                        if value is not None:
                            field.cardinality_hint = int(value)

            enum_targets = [
                field
                for field in fields
                if field.cardinality_hint is not None
                and 0 < field.cardinality_hint <= 25
            ]
            for field in enum_targets:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT DISTINCT {quote_uc_name(field.name)} AS value "
                        f"FROM {qualified} "
                        f"WHERE {quote_uc_name(field.name)} IS NOT NULL "
                        "LIMIT 30"
                    )
                    values = [str(row[0]) for row in cursor.fetchall()]
                field.enum_values = sorted(values)
            details["enum_columns"] = [f.name for f in enum_targets]
    except ValueError:
        raise
    except LiveInspectionError:
        raise
    except Exception:
        raise LiveInspectionError(
            "Live inspection failed. Check that the identity has USE "
            "CATALOG, USE SCHEMA, and SELECT on the table."
        ) from None
    return fields, details


def available() -> bool:
    """True when live inspection is configured and worth attempting."""
    return settings.live_profiling_available
