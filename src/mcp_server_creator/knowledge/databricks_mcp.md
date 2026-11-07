# Databricks MCP reference

Verified against Databricks documentation on 2026-11-07. Where a rule is a
hard requirement rather than a convention, it says so.

## The three kinds of MCP server on Databricks

| Kind | Who runs it | What it is for |
|---|---|---|
| Managed | Databricks | Governed access to data already in the workspace. No code. |
| External | A third party | A self-hosted or vendor server registered through the MCPs UI. |
| Custom | You | Your own tools and business logic, hosted on Databricks Apps. |

Pick a managed server when it already does the job. Build a custom server when
the tools have to encode domain logic, combine sources, or answer a goal-level
question that no single query covers.

## Managed servers: URLs and OAuth scopes

| Server | URL | Scope |
|---|---|---|
| Genie One | `https://<host>/ai-gateway/mcp-services/system.ai.genie_one_mcp` | `ai-gateway` |
| Genie Agent | `https://<host>/api/2.0/mcp/genie/{genie_space_id}` | `genie` |
| AI Search | `https://<host>/api/2.0/mcp/ai-search/{catalog}/{schema}/{index}` | `ai-search` |
| Databricks SQL | `https://<host>/api/2.0/mcp/sql` | `sql` |
| Unity Catalog functions | `https://<host>/api/2.0/mcp/functions/{catalog}/{schema}/{function}` | `unity-catalog` |

Notes that matter when composing them with a custom server:

- Unity Catalog enforces permissions on every call, so an agent reaches only
  what its identity is granted.
- Genie endpoints are read-only. Databricks SQL can write, depending on the
  caller's permissions; treat it as a privileged tool.
- A functions endpoint is pinned to one catalog and schema, AI Search to one
  index, and a Genie Agent to one space. The URL itself is the scope boundary.
- `_meta` parameters are presets set in agent code, not model-generated
  arguments. Use them when a call must be deterministic.
- Pricing differs per server: UC functions bill serverless general compute,
  Genie Agents serverless SQL, Databricks SQL standard SQL, AI Search its own
  rate, and a custom server bills Databricks Apps.

## Custom server on Databricks Apps: hard requirements

1. **Name the app with an `mcp-` prefix.** AI Playground and Unity Gateway use
   the prefix to recognize an app as an MCP server. `mcp-contract-triage` is
   fine; `contract-triage-mcp` will deploy but will not be offered as an MCP.
2. **Serve Streamable HTTP, bound to `0.0.0.0`.**
3. **Listen on `DATABRICKS_APP_PORT`** (default `8000`). Read it at startup.
4. **Ship a dependency file.** `requirements.txt` for Python. Keep it in step
   with `pyproject.toml`; the App build reads the requirements file.
5. **Callers need `CAN USE` on the app and an OAuth token.** Personal access
   tokens are not supported for app-hosted MCP servers.

## What Databricks injects at runtime

| Variable | Use |
|---|---|
| `DATABRICKS_APP_PORT` | The port to listen on. |
| `DATABRICKS_HOST` | Workspace URL, for calling back into Databricks. |
| `DATABRICKS_CLIENT_ID` | App service principal client id. |
| `DATABRICKS_CLIENT_SECRET` | App service principal OAuth secret. |

The service principal is a *separate identity* from you. Every grant your tools
need must be given to it, not to your user account. This is the single most
common cause of a server that works locally and returns empty results once
deployed.

## app.yaml

`command` is a sequence, not a shell string. Databricks does not run it through
a shell, so environment variables are not expanded there. The one exception is
`DATABRICKS_APP_PORT`, which is substituted at runtime.

```yaml
command: ["python", "-m", "my_server.app"]
env:
  - name: DATABRICKS_WAREHOUSE_ID
    valueFrom: sql_warehouse
  - name: MY_API_KEY
    valueFrom: my-api-secret
  - name: LOG_LEVEL
    value: INFO
```

To put a package on the path when the source layout is `src/`, invoke a shell
explicitly:

```yaml
command:
  - /bin/sh
  - -c
  - export PYTHONPATH=/app/python/source_code/src:$PYTHONPATH && python -m my_server.app
```

`valueFrom` resolves a declared *resource key*, not a literal. A secret
resolves to the decrypted value, a SQL warehouse to its id, an app to its name.
Never put a secret in a `value:` field.

## databricks.yml

Declaring resources in a bundle is what grants the app access and keeps the
deployment reproducible.

```yaml
bundle:
  name: my-mcp-server

resources:
  apps:
    my_mcp_server:
      name: mcp-my-server
      description: Domain tools for ...
      source_code_path: ../src
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
```

Declaring another app as a resource with `permission: CAN_USE` grants access on
deploy, which is how one agent app reaches an MCP app. That requires Databricks
CLI v0.298.0 or later; earlier versions warn about an unknown `name` field.

## Deploy sequence

```bash
databricks auth login --host https://<workspace-host>
databricks current-user me                      # confirm identity
databricks apps create mcp-my-server            # name must start with mcp-
databricks sync . "/Workspace/Users/$USER/mcp-my-server"
databricks apps deploy mcp-my-server \
  --source-code-path "/Workspace/Users/$USER/mcp-my-server"
```

The MCP endpoint is then `https://<app-hostname>/mcp`.

The profile name that `databricks auth login` saves defaults to the workspace
host, not `DEFAULT`. Set `DATABRICKS_CONFIG_PROFILE` to the name it actually
saved, which `databricks auth profiles` lists.

## Registering and connecting

- Workspace UI: Unity Gateway, then MCPs, then + MCP.
- Coding agents: `ug mcp add --names "app:mcp-my-server"`.
- Python agents: pass the app's `/mcp` URL to the agent framework's MCP client.
- Other clients: register an OAuth client with the client's redirect URL and
  the app's user authorization scopes, then configure the MCP URL.

## Secrets

```bash
databricks secrets create-scope my-mcp-secrets
databricks secrets put-secret my-mcp-secrets api_key --string-value "..."
```

Secret permissions apply at scope level, not per secret, so give each app its
own scope and put only what that app needs in it.

## AI Search (vector search) for unstructured data

- Enable change data feed on the source table first; a Delta Sync index needs
  it: `ALTER TABLE ... SET TBLPROPERTIES (delta.enableChangeDataFeed = true)`.
- Endpoint types are `STANDARD` and `STORAGE_OPTIMIZED`. Storage-optimized
  supports triggered sync only, and supports a keyword (BM25) full-text index
  with no embeddings at all.
- **Neither storage-optimized endpoints nor full-text search exist in every
  environment.** Databricks' published IL5 availability for us-gov-west-1
  lists Vector Search (Standard), High QPS and Reranker as available, and
  Vector Search (Storage Optimized) and Vector Search: Full-Text Search as
  unavailable. Design the keyword path as SQL against the chunk table unless
  you have confirmed the index exists where the server will run.
- Managed embeddings: pass `embedding_source_column` and
  `embedding_model_endpoint_name`, and Databricks computes vectors for you.
- Self-managed embeddings: pass `embedding_vector_column` and
  `embedding_dimension`, with vectors already in the table. You cannot convert
  a self-managed index to a managed one; you recreate it.
- `pipeline_type` is `TRIGGERED` (you call `index.sync()`) or `CONTINUOUS`.
- Creating an index needs `CREATE TABLE` on the target schema; querying needs
  `SELECT` on the index and on the Delta table behind it.

```python
from databricks.vector_search.client import VectorSearchClient

client = VectorSearchClient()
index = client.create_delta_sync_index_and_wait(
    endpoint_name="my-endpoint",
    source_table_name="catalog.schema.doc_chunks",
    index_name="catalog.schema.doc_chunks_index",
    pipeline_type="TRIGGERED",
    primary_key="chunk_id",
    embedding_source_column="content",
    embedding_model_endpoint_name="databricks-gte-large-en",
)
index.sync()
results = index.similarity_search(
    query_text="how do I request an option exercise",
    columns=["chunk_id", "doc_title", "page", "content"],
    num_results=5,
)
```

## Troubleshooting

| Symptom | Cause to check first |
|---|---|
| App deploys, Playground does not list it | App name lacks the `mcp-` prefix. |
| Tools return empty results in the app but work locally | The app service principal lacks the Unity Catalog grant. |
| `401` or `302` from an unauthenticated curl | Expected. Apps sit behind workspace auth. |
| Client cannot authenticate with a PAT | PATs are unsupported; use OAuth. |
| `command` fails with a missing module | `PYTHONPATH` is not set; `command` is not run in a shell. |
| App will not start | No `requirements.txt`, or the server bound `127.0.0.1`. |
| Warehouse id is empty at runtime | `valueFrom` key does not match the declared resource name. |

## Environment differences that change a design

Verified from Databricks' published feature availability, 2026-11-07.

A commercial workspace, a FedRAMP High GovCloud workspace and an IL5 GovCloud
DoD workspace are not the same platform. The gaps are not guessable, and a
design validated against the commercial feature set can fail on deployment in
a restricted one for reasons no code review catches.

For IL5 (`us-gov-west-1`), available: Databricks Apps including V2,
horizontal scaling, on-behalf-of user authorization and install from Git;
Genie Spaces, Genie One, Genie Agent Mode and Genie Code; Vector Search
Standard, High QPS and Reranker.

For IL5, **not** available: Vector Search Storage Optimized; Vector Search
Full-Text Search; Lakebase Search; AI Search Quality Evaluation; Genie for
Teams and Slack.

Three consequences worth internalizing:

1. **Keyword retrieval is SQL there, not BM25.** See the AI Search section.
2. **Apps scale horizontally**, so state held in process is not reliable: a
   later request may reach a different replica. Return an explicit handle and
   keep the document in the payload, or in a store every replica can read.
3. **Public model providers are not reachable.** Anything that needs a model,
   including an evaluation harness, must call Databricks Model Serving inside
   the boundary. Defaulting to a public endpoint does not merely fail, it
   fails closed in an accredited environment and the attempt is itself a
   finding.

Whether the managed MCP endpoints and the Unity Gateway MCPs UI exist in a
government environment is not published. Do not make either a dependency:
deploy the app, grant `CAN USE`, and connect by URL with an OAuth token. The
UI is a convenience.

## Model access that works in any environment

Databricks Model Serving presents Foundation Model APIs, external models
(OpenAI, Anthropic, Gemini and others) and customer-hosted models through one
OpenAI-compatible interface, and its function calling is OpenAI-compatible.
So the portable pattern for anything that needs a model is:

```python
from openai import OpenAI

client = OpenAI(
    api_key=databricks_token,
    base_url=f"{workspace_host}/serving-endpoints",
)
response = client.chat.completions.create(
    model="databricks-claude-sonnet-4-5",   # a serving endpoint name
    messages=[...],
)
```

The `model` parameter is an endpoint name, not a vendor model id. That single
indirection is what makes the same code work in a commercial workspace and in
an IL5 enclave: whoever administers the workspace decides which models exist,
and the code does not change.
