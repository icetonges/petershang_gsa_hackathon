---
name: databricks-mcp-deployment
description: Deploy an MCP server to Databricks Apps and register it so agents can use it. Use when creating the app, writing app.yaml or databricks.yml, binding a SQL warehouse or secret, granting Unity Catalog access to the app service principal, connecting clients, or debugging a deployed server that returns nothing.
---

# Deploying an MCP server on Databricks

## Five requirements that are not optional

1. **The app name starts with `mcp-`.** AI Playground and Unity Gateway use
   the prefix to recognize an app as an MCP server. `mcp-contract-triage`
   works; `contract-triage-mcp` deploys and is never offered as an MCP.
2. **Streamable HTTP, bound to `0.0.0.0`.** Not `127.0.0.1`, or the platform
   health check never passes.
3. **Listen on `DATABRICKS_APP_PORT`**, default `8000`. Read it at startup.
4. **Ship `requirements.txt`.** The App build reads it. Keep it in step with
   `pyproject.toml`.
5. **Callers need `CAN USE` on the app and an OAuth token.** Personal access
   tokens are not supported.

## app.yaml

`command` is a sequence and is not run through a shell, so environment
variables are not expanded in it. The single exception is
`DATABRICKS_APP_PORT`, which the platform substitutes.

For a `src/` layout, invoke a shell explicitly so the package is importable:

```yaml
command:
  - /bin/sh
  - -c
  - export PYTHONPATH=/app/python/source_code/src:$PYTHONPATH && python -m my_server.app
env:
  - name: DATABRICKS_WAREHOUSE_ID
    valueFrom: sql_warehouse
  - name: MY_API_KEY
    valueFrom: my-api-secret
  - name: LOG_LEVEL
    value: INFO
```

`valueFrom` names a declared *resource key*, not a value. A secret resolves to
the decrypted secret, a SQL warehouse to its id, an app to its name. Never put
a secret in a `value:` field. If the warehouse id is empty at runtime, the
`valueFrom` key does not match the declared resource name.

## databricks.yml

A bundle makes the deployment reproducible and grants the app its resources.

```yaml
bundle:
  name: my-mcp-server

variables:
  warehouse_id:
    description: SQL warehouse the server queries through
    default: ""

resources:
  apps:
    my_mcp_server:
      name: mcp-my-server
      description: Domain tools for ...
      source_code_path: ../../
      resources:
        - name: sql_warehouse
          sql_warehouse:
            id: ${var.warehouse_id}
            permission: CAN_USE
        - name: my-api-secret
          secret:
            scope: my-mcp-secrets
            key: api_key
            permission: READ

targets:
  dev:
    mode: development
    default: true
```

To let an agent app call your MCP app, declare the MCP app as a resource of
the agent app with `permission: CAN_USE`; the bundle grants it on deploy.
Requires Databricks CLI v0.298.0 or later.

## Deploy

```bash
databricks auth login --host https://<workspace-host>
databricks current-user me

databricks apps create mcp-my-server
databricks sync . "/Workspace/Users/$DATABRICKS_USERNAME/mcp-my-server"
databricks apps deploy mcp-my-server \
  --source-code-path "/Workspace/Users/$DATABRICKS_USERNAME/mcp-my-server"
```

The endpoint is `https://<app-hostname>/mcp`, with health at `/health`.

The profile name `databricks auth login` saves defaults to the workspace host,
not `DEFAULT`. Set `DATABRICKS_CONFIG_PROFILE` to the name it actually saved;
`databricks auth profiles` lists them.

The first deploy builds a container and takes a few minutes. An
unauthenticated `curl` returning 302 or 401 is the app working, not failing.

## Grant the service principal, not yourself

This is the single most common deployment failure: the server works locally
and returns empty results once deployed.

The app runs as its own service principal, visible under App Authorization on
the app page. Every grant must go to it:

| Source | Grants |
|---|---|
| UC table | `USE CATALOG`, `USE SCHEMA`, `SELECT` on the table, `CAN USE` on the warehouse |
| AI Search index | `USE CATALOG`, `USE SCHEMA`, `SELECT` on the index and on the Delta table behind it |
| UC function | `USE CATALOG`, `USE SCHEMA`, `EXECUTE` on the function |
| UC volume | `USE CATALOG`, `USE SCHEMA`, `READ VOLUME` |
| Genie space | `CAN RUN` on the space, `SELECT` on its tables, `CAN USE` on its warehouse |

```sql
GRANT USE CATALOG ON CATALOG main TO `<service-principal-id>`;
GRANT USE SCHEMA ON SCHEMA main.finance TO `<service-principal-id>`;
GRANT SELECT ON TABLE main.finance.contracts TO `<service-principal-id>`;
```

## Secrets

```bash
databricks secrets create-scope my-mcp-secrets
databricks secrets put-secret my-mcp-secrets api_key --string-value "..."
```

Secret permissions apply at scope level, not per secret. Give each app its own
scope holding only what that app needs.

## Register and connect

- Workspace UI: Unity Gateway, then MCPs, then + MCP.
- Coding agents: `ug mcp add --names "app:mcp-my-server"`.
- Python agents: pass the app's `/mcp` URL to the framework's MCP client.
- Other clients: register an OAuth client with its redirect URL and the app's
  user authorization scopes, then configure the MCP URL.
- AI Playground lists the app automatically once the name is prefixed and the
  caller has `CAN USE`.

## Composing with managed servers

Do not rebuild what Databricks already hosts. Your custom server holds the
domain logic; managed servers supply the rest, each governed by Unity Catalog:

| Server | URL | Scope |
|---|---|---|
| Genie One | `/ai-gateway/mcp-services/system.ai.genie_one_mcp` | `ai-gateway` |
| Genie Agent | `/api/2.0/mcp/genie/{genie_space_id}` | `genie` |
| AI Search | `/api/2.0/mcp/ai-search/{catalog}/{schema}/{index}` | `ai-search` |
| Databricks SQL | `/api/2.0/mcp/sql` | `sql` |
| UC functions | `/api/2.0/mcp/functions/{catalog}/{schema}/{function}` | `unity-catalog` |

With on-behalf-of user authentication, request the scope for each server the
application uses. Databricks SQL can write depending on the caller's
permissions, so treat it as privileged; Genie endpoints are read-only.

## Debugging a deployed server

| Symptom | First thing to check |
|---|---|
| Playground does not list the app | Name is missing the `mcp-` prefix |
| Tools return empty results, fine locally | Service principal lacks the grant |
| App will not start | No `requirements.txt`, or bound to `127.0.0.1` |
| `ModuleNotFoundError` in app logs | `PYTHONPATH` not set; `command` is not a shell |
| Warehouse id empty at runtime | `valueFrom` key does not match the resource name |
| Client cannot authenticate | Using a PAT; OAuth is required |
| 302 or 401 from curl | Expected; the app is behind workspace auth |

Read the app's own logs from the app page before changing code. Most failures
announce themselves there in one line.

## Teardown

```bash
databricks apps delete mcp-my-server
databricks workspace delete "/Workspace/Users/$DATABRICKS_USERNAME/mcp-my-server" --recursive
```
