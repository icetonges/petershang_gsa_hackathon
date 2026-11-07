"""Generate the Databricks Apps deployment kit for a server.

Everything here encodes a rule that is easy to get wrong and expensive to
debug: the `mcp-` name prefix, the shell-less `command` sequence, binding a
warehouse by resource key, and granting the app's own service principal.
"""

from __future__ import annotations

from mcp_server_creator.models import (
    DeploymentTarget,
    FileManifest,
    ServerSpec,
    SourceKind,
)
from mcp_server_creator.profiling import default_grants
from mcp_server_creator.utils import render

APP_YAML = """# Databricks Apps runtime configuration.
#
# `command` is a sequence and is NOT run through a shell, so environment
# variables are not expanded in it. The one exception is DATABRICKS_APP_PORT,
# which the platform substitutes at runtime. Because this project uses a src/
# layout, a shell is invoked explicitly to put the package on the path.
#
# The MCP endpoint is https://<app-hostname>/mcp, with health at /health.
command:
  - /bin/sh
  - -c
  - >-
    export PYTHONPATH=/app/python/source_code/src:$PYTHONPATH &&
    python -m {{package}}.app
{{env_block}}"""

DATABRICKS_YML = """# Databricks Asset Bundle for {{app_name}}.
#
# Declaring resources here is what grants the app access to them on deploy
# and keeps the deployment reproducible. Requires Databricks CLI v0.298.0+.
#
#   databricks bundle validate
#   databricks bundle deploy --var="warehouse_id=<id>"
bundle:
  name: {{server_name}}

variables:
  warehouse_id:
    description: SQL warehouse the server queries through
    default: ""

resources:
  apps:
    {{resource_key}}:
      name: {{app_name}}
      description: {{purpose}}
      source_code_path: ../../
{{resource_block}}
targets:
  dev:
    mode: development
    default: true
"""

DEPLOY_SCRIPT = r"""#!/usr/bin/env bash
# Idempotent deploy of this MCP server to Databricks Apps.
#
#   cp deploy/databricks/.env.example deploy/databricks/.env
#   # edit it, then:
#   set -a; source deploy/databricks/.env; set +a
#   bash deploy/databricks/deploy.sh
set -euo pipefail

APP_NAME="${APP_NAME:-{{app_name}}}"
SOURCE_CODE_PATH="${SOURCE_CODE_PATH:-}"
PROFILE_ARGS=()
if [[ -n "${DATABRICKS_CONFIG_PROFILE:-}" ]]; then
  PROFILE_ARGS=(--profile "${DATABRICKS_CONFIG_PROFILE}")
fi

if ! command -v databricks >/dev/null 2>&1; then
  echo "The Databricks CLI is not installed." >&2
  echo "See https://docs.databricks.com/dev-tools/cli/install.html" >&2
  exit 1
fi

if ! databricks "${PROFILE_ARGS[@]}" current-user me >/dev/null 2>&1; then
  echo "Not authenticated. Run:" >&2
  echo "  databricks auth login --host https://<workspace-host>" >&2
  echo "Then set DATABRICKS_CONFIG_PROFILE to the profile name it saved." >&2
  echo "List profiles with: databricks auth profiles" >&2
  exit 1
fi

if [[ "${APP_NAME}" != mcp-* ]]; then
  echo "APP_NAME must start with 'mcp-' or AI Playground will not" >&2
  echo "recognize this app as an MCP server. Got: ${APP_NAME}" >&2
  exit 1
fi

if [[ -z "${SOURCE_CODE_PATH}" ]]; then
  ME="$(databricks "${PROFILE_ARGS[@]}" current-user me --output json)"
  USER_NAME="$(printf '%s' "${ME}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["userName"])')"
  SOURCE_CODE_PATH="/Workspace/Users/${USER_NAME}/${APP_NAME}"
  echo "SOURCE_CODE_PATH not set; using ${SOURCE_CODE_PATH}"
fi

if [[ ! -f app.yaml ]]; then
  echo "app.yaml must be at the repository root: Databricks reads it from" >&2
  echo "the deployed source root." >&2
  exit 1
fi

echo "==> Ensuring the app exists"
if databricks "${PROFILE_ARGS[@]}" apps get "${APP_NAME}" >/dev/null 2>&1; then
  echo "    ${APP_NAME} already exists; reusing it"
else
  databricks "${PROFILE_ARGS[@]}" apps create "${APP_NAME}"
fi

echo "==> Syncing source to ${SOURCE_CODE_PATH}"
databricks "${PROFILE_ARGS[@]}" sync . "${SOURCE_CODE_PATH}"

echo "==> Deploying"
databricks "${PROFILE_ARGS[@]}" apps deploy "${APP_NAME}" \
  --source-code-path "${SOURCE_CODE_PATH}"

echo "==> Deployed"
databricks "${PROFILE_ARGS[@]}" apps get "${APP_NAME}" --output json \
  | python3 -c 'import json,sys;print("URL:", json.load(sys.stdin).get("url"))' \
  || true
echo
echo "Next:"
echo "  1. Apply deploy/databricks/grants.sql to the app service principal."
echo "  2. Smoke test:  bash deploy/databricks/smoke-test.sh <app-url>"
echo "  3. Register it: Unity Gateway > MCPs, or"
echo "     ug mcp add --names \"app:${APP_NAME}\""
"""

SMOKE_SCRIPT = r"""#!/usr/bin/env bash
# Check a deployed app answers /health and /mcp.
#
#   bash deploy/databricks/smoke-test.sh https://<app-url>
#
# Without a token, 302 or 401 is the CORRECT result: Databricks Apps sit
# behind workspace authentication. For an authenticated check:
#
#   DATABRICKS_TOKEN="$(databricks auth token --output json | jq -r .access_token)" \
#     bash deploy/databricks/smoke-test.sh https://<app-url>
set -euo pipefail

APP_URL="${1:-}"
if [[ -z "${APP_URL}" ]]; then
  echo "usage: $0 https://<app-url>" >&2
  exit 1
fi
APP_URL="${APP_URL%/}"

AUTH_ARGS=()
if [[ -n "${DATABRICKS_TOKEN:-}" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${DATABRICKS_TOKEN}")
  echo "Using a bearer token."
else
  echo "No DATABRICKS_TOKEN set; expect 302 or 401 (that is the app working)."
fi

echo "==> GET ${APP_URL}/health"
curl -sS -o /dev/null -w "    HTTP %{http_code}\n" "${AUTH_ARGS[@]}" \
  "${APP_URL}/health"

echo "==> POST ${APP_URL}/mcp (tools/list)"
curl -sS -o /tmp/mcp-smoke.json -w "    HTTP %{http_code}\n" \
  "${AUTH_ARGS[@]}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  "${APP_URL}/mcp"

if [[ -s /tmp/mcp-smoke.json ]]; then
  echo "    response head:"
  head -c 400 /tmp/mcp-smoke.json
  echo
fi
"""

DEPLOY_ENV = """# Deploy coordinates. No secrets here: auth is your CLI login.
#
#   set -a; source deploy/databricks/.env; set +a
#   bash deploy/databricks/deploy.sh

# Workspace URL, e.g. https://dbc-00000000-0000.cloud.databricks.com
DATABRICKS_HOST={{workspace_host}}

# The CLI profile name `databricks auth login` actually saved. It defaults to
# the workspace host, NOT "DEFAULT". List them: databricks auth profiles
DATABRICKS_CONFIG_PROFILE=DEFAULT

# App name. MUST start with mcp- so AI Playground recognizes it.
APP_NAME={{app_name}}

# Absolute workspace path the app deploys from. Leave blank to derive it
# from your username.
SOURCE_CODE_PATH={{source_code_path}}
"""


def _env_block(spec: ServerSpec) -> str:
    """Render the app.yaml env section from what the sources need."""
    kinds = {source.kind for source in spec.data_sources}
    entries: list[str] = []
    if kinds & {SourceKind.UC_TABLE, SourceKind.UC_FUNCTION}:
        entries.append(
            "  - name: DATABRICKS_WAREHOUSE_ID\n"
            f"    valueFrom: {spec.deployment.sql_warehouse_resource}"
        )
    if SourceKind.REST_API in kinds and spec.deployment.secret_scope:
        entries.append("  - name: API_KEY\n    valueFrom: upstream-api-key")
    if not entries:
        return ""
    return "env:\n" + "\n".join(entries) + "\n"


def _resource_block(spec: ServerSpec) -> str:
    """Render the bundle resources section, or a commented placeholder."""
    kinds = {source.kind for source in spec.data_sources}
    lines: list[str] = []
    if kinds & {SourceKind.UC_TABLE, SourceKind.UC_FUNCTION}:
        lines += [
            "      resources:",
            f"        - name: {spec.deployment.sql_warehouse_resource}",
            "          sql_warehouse:",
            "            id: ${var.warehouse_id}",
            "            permission: CAN_USE",
        ]
    if SourceKind.REST_API in kinds and spec.deployment.secret_scope:
        if not lines:
            lines.append("      resources:")
        lines += [
            "        - name: upstream-api-key",
            "          secret:",
            f"            scope: {spec.deployment.secret_scope}",
            "            key: api_key",
            "            permission: READ",
        ]
    if not lines:
        lines = [
            "      # No warehouse or secret is needed: this server reads",
            "      # only data packaged with it.",
        ]
    return "\n".join(lines) + "\n"


def grants_sql(spec: ServerSpec) -> str:
    """Render the GRANT statements the app service principal needs."""
    lines = [
        "-- Grants for the Databricks App service principal.",
        "--",
        "-- The app runs as its OWN identity, not as you. A server that works",
        "-- locally and returns empty results once deployed has almost always",
        "-- skipped this file.",
        "--",
        "-- Find the service principal's application id under App",
        "-- Authorization on the app's page, then replace the placeholder",
        "-- below and run these in a SQL editor.",
        "",
        "-- SET VAR app_sp = '<service-principal-application-id>';",
        "",
    ]
    seen: set[str] = set()
    for source in spec.data_sources:
        lines.append(f"-- {source.name} ({source.kind.value})")
        if source.kind is SourceKind.UC_TABLE and source.full_table_name:
            statements = [
                f"GRANT USE CATALOG ON CATALOG {source.catalog} TO `$app_sp`;",
                "GRANT USE SCHEMA ON SCHEMA "
                f"{source.catalog}.{source.schema_name} TO `$app_sp`;",
                f"GRANT SELECT ON TABLE {source.full_table_name} TO `$app_sp`;",
            ]
        elif source.kind is SourceKind.AI_SEARCH_INDEX and source.index_name:
            statements = [
                f"GRANT SELECT ON TABLE {source.index_name} TO `$app_sp`;",
                "-- Also grant SELECT on the Delta table behind the index.",
            ]
        elif source.kind is SourceKind.UC_FUNCTION and source.function_name:
            statements = [
                "GRANT EXECUTE ON FUNCTION "
                f"{source.catalog}.{source.schema_name}."
                f"{source.function_name} TO `$app_sp`;",
            ]
        elif source.kind is SourceKind.UC_VOLUME_FILES:
            statements = [
                "-- GRANT READ VOLUME ON VOLUME <volume> TO `$app_sp`;",
            ]
        else:
            statements = [
                "-- Not a Unity Catalog grant. Needed instead:",
                *[
                    f"--   {need}"
                    for need in (
                        source.required_grants
                        or default_grants(source.kind, source.modality)
                    )
                ],
            ]
        for statement in statements:
            if statement not in seen:
                seen.add(statement)
                lines.append(statement)
        lines.append("")
    lines += [
        "-- The SQL warehouse is granted through the bundle resource in",
        "-- databricks.yml (permission: CAN_USE), not with a GRANT here.",
    ]
    return "\n".join(lines) + "\n"


RUNBOOK = """# Deploying {{title}} to Databricks Apps

- **Transport:** MCP over streamable HTTP at `/mcp`, health at `/health`
- **Port:** Databricks injects `DATABRICKS_APP_PORT`; `app.py` reads it
- **App name:** `{{app_name}}` — the `mcp-` prefix is what makes AI Playground
  and Unity Gateway treat this as an MCP server

## 1. Prerequisites

```bash
databricks --version          # need v0.205+, v0.298+ for bundles
databricks auth login --host https://<workspace-host>
databricks current-user me
```

The profile name `databricks auth login` saves defaults to the workspace
host, **not** `DEFAULT`. Set `DATABRICKS_CONFIG_PROFILE` to the name it
actually saved; `databricks auth profiles` lists them.

Databricks Apps must be enabled on the workspace. If `apps` commands fail
with a feature error, ask a workspace admin.

## 2. Configure

```bash
cp deploy/databricks/.env.example deploy/databricks/.env
# edit APP_NAME, DATABRICKS_HOST, DATABRICKS_CONFIG_PROFILE
set -a; source deploy/databricks/.env; set +a
```

No secrets live in that file. Authentication comes from your CLI login.

## 3. Deploy

```bash
bash deploy/databricks/deploy.sh
```

The script refuses to proceed if the app name lacks the `mcp-` prefix or if
`app.yaml` is not at the repository root. The first deploy builds a container
and takes a few minutes.

With the bundle instead:

```bash
databricks bundle validate
databricks bundle deploy --var="warehouse_id=<warehouse-id>"
```

## 4. Grant the service principal

{{grants_note}}

Open the app's page, find the service principal under **App Authorization**,
then apply [grants.sql](grants.sql) with that application id.

This is the most common cause of a deployed server returning nothing.

## 5. Smoke test

```bash
bash deploy/databricks/smoke-test.sh https://<app-url>

DATABRICKS_TOKEN="$(databricks auth token --output json | jq -r .access_token)" \\
  bash deploy/databricks/smoke-test.sh https://<app-url>
```

An unauthenticated 302 or 401 is the app working, not failing.

## 6. Register and connect

- **Workspace UI:** Unity Gateway, then MCPs, then + MCP.
- **Coding agents:** `ug mcp add --names "app:{{app_name}}"`.
- **Python agents:** pass `https://<app-url>/mcp` to the framework's MCP
  client.
- **Other clients:** register an OAuth client with its redirect URL and the
  app's user authorization scopes, then configure the MCP URL.

Callers need `CAN USE` on the app and an OAuth token. Personal access tokens
are not supported.

## Troubleshooting

| Symptom | First thing to check |
|---|---|
| Playground does not list the app | Name is missing the `mcp-` prefix |
| Tools return nothing, fine locally | Service principal lacks the grant |
| App will not start | No `requirements.txt`, or bound to 127.0.0.1 |
| `ModuleNotFoundError` in app logs | `PYTHONPATH` not set in `command` |
| Warehouse id empty at runtime | `valueFrom` key does not match the resource |
| Client cannot authenticate | Using a PAT; OAuth is required |

Read the app's own logs from its page before changing code.

## Teardown

```bash
databricks apps delete {{app_name}}
databricks workspace delete "${SOURCE_CODE_PATH}" --recursive
```
"""


def generate_deployment(spec: ServerSpec) -> FileManifest:
    """Generate every Databricks deployment artifact for a server."""
    manifest = FileManifest(
        server_name=spec.server_name, package_name=spec.package_name
    )
    if spec.deployment.target is not DeploymentTarget.DATABRICKS_APPS:
        manifest.warnings.append(
            "The deployment target is "
            f"'{spec.deployment.target.value}', so no Databricks artifacts "
            "were generated. Set the target to databricks_apps to get them."
        )
        return manifest

    kinds = {source.kind for source in spec.data_sources}
    needs_grants = bool(
        kinds
        & {
            SourceKind.UC_TABLE,
            SourceKind.UC_FUNCTION,
            SourceKind.AI_SEARCH_INDEX,
            SourceKind.UC_VOLUME_FILES,
            SourceKind.GENIE_SPACE,
        }
    )
    values = {
        "package": spec.package_name,
        "server_name": spec.server_name,
        "app_name": spec.deployment.app_name,
        "resource_key": spec.package_name,
        "purpose": spec.purpose.replace("\n", " ")[:200],
        "title": spec.title,
        "env_block": _env_block(spec),
        "resource_block": _resource_block(spec),
        "workspace_host": spec.deployment.workspace_host,
        "source_code_path": spec.deployment.source_code_path,
        "grants_note": (
            "This server reads governed data, so grants are required."
            if needs_grants
            else "This server reads only data packaged with it, so no Unity "
            "Catalog grant is needed. The file is still generated, listing "
            "what would be required if you add a governed source later."
        ),
    }

    manifest.add(
        "app.yaml",
        render(APP_YAML, values),
        "Databricks Apps runtime config; must sit at the repository root",
    )
    if spec.deployment.use_bundle:
        manifest.add(
            "databricks.yml",
            render(DATABRICKS_YML, values),
            "Asset bundle: declares resources and grants them on deploy",
        )
    manifest.add(
        "deploy/databricks/deploy.sh",
        render(DEPLOY_SCRIPT, values),
        "Idempotent create, sync, deploy",
        executable=True,
    )
    manifest.add(
        "deploy/databricks/smoke-test.sh",
        SMOKE_SCRIPT,
        "Live /health and /mcp reachability check",
        executable=True,
    )
    manifest.add(
        "deploy/databricks/.env.example",
        render(DEPLOY_ENV, values),
        "Deploy coordinates; no secrets",
    )
    manifest.add(
        "deploy/databricks/grants.sql",
        grants_sql(spec),
        "Grants the app service principal needs",
    )
    manifest.add(
        "deploy/databricks/README.md",
        render(RUNBOOK, values),
        "Deployment runbook and troubleshooting",
    )

    if not spec.deployment.workspace_host:
        manifest.warnings.append(
            "No workspace host recorded, so deploy/databricks/.env.example "
            "ships with a blank DATABRICKS_HOST."
        )
    manifest.next_steps = [
        "Set APP_NAME and DATABRICKS_HOST in deploy/databricks/.env.",
        "Run bash deploy/databricks/deploy.sh.",
        "Apply deploy/databricks/grants.sql to the app service principal.",
        "Smoke test, then register the app in Unity Gateway > MCPs.",
    ]
    return manifest
