"""Optional live inspection of a real Unity Catalog table.

Everything else in this server works offline on pasted schema text. When the
operator has configured Databricks credentials and a warehouse, profiling can
read the real table instead, which produces better enum candidates and a true
row count.

Failures here are never fatal: the caller falls back to offline parsing and
reports why.

Identity. On Databricks Apps with user authorization enabled (the `sql`
scope), every request carries the caller's downscoped token in the
`x-forwarded-access-token` header. Profiling uses that token first, so a
person can profile exactly the tables Unity Catalog already lets them read,
and nothing more. Without it, profiling falls back to the app's service
principal, which needs its own grants.
"""

from __future__ import annotations

import re
from typing import Any

from mcp_server_creator.config import settings
from mcp_server_creator.models import FieldProfile
from mcp_server_creator.profiling import infer_role, normalize_type
from mcp_server_creator.utils import quote_uc_name

MAX_DISTINCT_PROBES = 8


class LiveInspectionError(RuntimeError):
    """A sanitized live-inspection failure. Never fatal to a profile call."""


USER_IDENTITY = "calling user"
APP_IDENTITY = "app service principal"
TOKEN_IDENTITY = "configured token"  # nosemgrep -- a display label, not a secret


def caller_token() -> str:
    """The calling user's downscoped token, when Databricks Apps forwards one.

    Returns an empty string outside an HTTP request, when user authorization
    is not enabled on the app, or when profiling as the user is turned off.
    The token is never logged or returned to the client.
    """
    if not settings.creator_profile_as_user:
        return ""
    try:
        from fastmcp.server.dependencies import get_http_headers
    except ImportError:
        return ""
    return (get_http_headers().get("x-forwarded-access-token") or "").strip()


def identity() -> str:
    """Which identity a live inspection would run as, in plain words."""
    if caller_token():
        return USER_IDENTITY
    if settings.databricks_client_id and settings.databricks_client_secret:
        return APP_IDENTITY
    if settings.databricks_token:
        return TOKEN_IDENTITY
    return "none"


_SECRETISH = re.compile(r"(dapi[0-9a-f]{8,}|eyJ[\w-]{10,}\.[\w.-]+|Bearer\s+\S+)")


def describe_error(error: BaseException) -> str:
    """Class and first line of an error, with anything token-like removed."""
    text = str(error).strip().splitlines()[0] if str(error).strip() else ""
    text = _SECRETISH.sub("[redacted]", text)[:200]
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


def connection_kwargs(prefer_user: bool = True) -> dict[str, Any]:
    """Connector arguments for the caller, else the app, else a token.

    Raises:
        LiveInspectionError: when the target or credentials are missing.
    """
    host = settings.databricks_host.replace("https://", "").rstrip("/")
    if not (host and settings.databricks_warehouse_id):
        raise LiveInspectionError(
            "DATABRICKS_HOST and DATABRICKS_WAREHOUSE_ID are not both set."
        )
    kwargs: dict[str, Any] = {
        "server_hostname": host,
        "http_path": f"/sql/1.0/warehouses/{settings.databricks_warehouse_id}",
    }
    user_token = caller_token() if prefer_user else ""
    if user_token:
        kwargs["access_token"] = user_token
    elif settings.databricks_client_id and settings.databricks_client_secret:

        def credentials_provider():
            # Built on connect, not here, so choosing an identity never
            # touches the network.
            from databricks.sdk.core import Config, oauth_service_principal

            config = Config(
                host=f"https://{host}",
                client_id=settings.databricks_client_id,
                client_secret=settings.databricks_client_secret,
            )
            return oauth_service_principal(config)

        kwargs["credentials_provider"] = credentials_provider
    elif settings.databricks_token:
        kwargs["access_token"] = settings.databricks_token
    else:
        raise LiveInspectionError("No Databricks credentials are configured.")
    return kwargs


def _connect(prefer_user: bool = True) -> Any:
    """Open a warehouse connection as the identity `connection_kwargs` picks.

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

    kwargs = connection_kwargs(prefer_user)
    try:
        return dbsql.connect(**kwargs)
    except Exception as error:
        raise LiveInspectionError(
            "Could not connect to the warehouse. Check the host, the "
            "warehouse id, and that the identity has CAN USE on it. "
            f"({describe_error(error)})"
        ) from None


def profile_table(
    catalog: str, schema: str, table: str
) -> tuple[list[FieldProfile], dict[str, Any]]:
    """Read a table's columns, row count, and enum candidates.

    Tries the caller first. If that fails and the app's service principal
    is configured, tries once more as the app and records why in
    `details['fallback']`, so a missing user scope or consent is visible
    instead of silently turning into a pasted schema.

    Raises:
        ValueError: if a name part is not a plain identifier.
        LiveInspectionError: with a sanitized message on any failure.
    """
    try:
        return _profile_table(catalog, schema, table, prefer_user=True)
    except LiveInspectionError as first:
        can_fall_back = (
            identity() == USER_IDENTITY
            and settings.databricks_client_id
            and settings.databricks_client_secret
        )
        if not can_fall_back:
            raise
        try:
            fields, details = _profile_table(catalog, schema, table, prefer_user=False)
        except LiveInspectionError as second:
            raise LiveInspectionError(f"As you: {first} As the app: {second}") from None
        details["fallback"] = {"from": USER_IDENTITY, "reason": str(first)}
        return fields, details


def _profile_table(
    catalog: str, schema: str, table: str, prefer_user: bool
) -> tuple[list[FieldProfile], dict[str, Any]]:
    qualified = quote_uc_name(catalog, schema, table)
    who = identity() if prefer_user else APP_IDENTITY
    connection = _connect(prefer_user)
    details: dict[str, Any] = {
        "source": "live",
        "table": f"{catalog}.{schema}.{table}",
        "identity": who,
    }
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
                # `qualified` and every column below come from quote_uc_name:
                # each part matched UC_PART_RE and is backtick-quoted, so no
                # caller text reaches the SQL unvalidated. No values are
                # interpolated.
                cursor.execute(
                    f"SELECT COUNT(*) AS row_count FROM {qualified}"  # nosemgrep
                )
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
                    cursor.execute(f"SELECT {selects} FROM {qualified}")  # nosemgrep
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
                quoted = quote_uc_name(field.name)
                sql = (
                    f"SELECT DISTINCT {quoted} AS value "  # nosemgrep
                    f"FROM {qualified} "
                    f"WHERE {quoted} IS NOT NULL LIMIT 30"  # nosemgrep
                )
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                    values = [str(row[0]) for row in cursor.fetchall()]
                field.enum_values = sorted(values)
            details["enum_columns"] = [f.name for f in enum_targets]
    except ValueError:
        raise
    except LiveInspectionError:
        raise
    except Exception as error:
        whose = "you have" if who == USER_IDENTITY else f"the {who} has"
        raise LiveInspectionError(
            f"Live inspection failed as the {who}. Check that {whose} USE "
            "CATALOG, USE SCHEMA, and SELECT on the table, or paste the "
            f"schema instead. ({describe_error(error)})"
        ) from None
    return fields, details


def available() -> bool:
    """True when live inspection is configured and worth attempting."""
    if settings.live_profiling_available:
        return True
    has_target = bool(settings.databricks_host and settings.databricks_warehouse_id)
    enabled = settings.creator_enable_live_profiling
    return enabled and has_target and bool(caller_token())
