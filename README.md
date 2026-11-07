# MCP Server Creator

An MCP server whose domain is building MCP servers.

A person brings an intent and their own data, structured or unstructured.
This server walks them to a complete, deployable, evaluated MCP server of
their own: the methodology to follow, the generated code, the Databricks
deployment kit, and the review that says whether it is any good.

Built on the [GSA MCP hackathon template](https://github.com/GSA-TTS/mcp-hackathon-template),
with [FastMCP](https://github.com/jlowin/fastmcp) and [uv](https://docs.astral.sh/uv/).
Deployment target is Databricks Apps.

---

## Why a server and not just a skill

A skill alone tells a model how to build an MCP server, and the model
improvises the rest: schemas, SQL, deployment config, evaluation. The output
varies run to run, and nothing is testable.

This server does both halves:

- **Skills** carry the judgment: what to ask, which tools to design, where
  retrieval goes wrong, what the rubric wants. Any MCP client gets them.
- **Deterministic generators** carry the mechanics: the same specification
  always produces the same project, so the output can be tested, reviewed,
  and regenerated after a change.

The specification is the contract between them, and the state. MCP
2026-07-28 removes sessions, so the spec travels in the payload rather than
living on the server.

---

## The nine tools

| Tool | What it answers |
|---|---|
| `creator_list_library` | What methodology can you give me? |
| `creator_load_document` | Give me that skill or reference in full |
| `creator_check_environment` | What can this Databricks workspace actually run? |
| `creator_start_design` | Here is what I want; what is still missing? |
| `creator_add_data_source` | What does my data actually support? |
| `creator_design_tools` | What tools should my server have? |
| `creator_review_server` | Is this design any good, and what is weak? |
| `creator_generate_project` | Build the whole project |
| `creator_write_project` | Put it on disk (opt-in, dry-run by default) |

Plus three prompts a client shows as commands: `create_mcp_server`,
`review_my_mcp_server`, `mcp_from_my_data`.

Every parameter is a scalar, an enum or a list of strings. After
`creator_start_design` you pass a twelve-character `design_id`, not the
document. Both of those are deliberate; see **Portability** below.

---

## The eight skills

Bundled inside the package, because a deployed app only receives the synced
source and `.dockerignore` excludes `.agents/`. Anything the running server
must serve has to live under `src/mcp_server_creator/`.

| Skill | Covers |
|---|---|
| `mcp-server-creator` | Entry point: the six-phase build sequence, and routing to the rest |
| `mcp-tool-design` | Naming, parameters and enums, docstrings, return shapes, errors |
| `structured-data-mcp` | UC tables, SQL safety, UC functions, Genie, REST, snapshots |
| `unstructured-data-mcp` | Extraction, chunking, AI Search indexes, citations |
| `databricks-mcp-deployment` | Apps, `app.yaml`, bundles, grants, registration, debugging |
| `mcp-evaluation-harness` | Golden sets, answer keys, baselines, decoys, injection |
| `mcp-security-review` | The failure modes that exist because a model is driving |
| `mcp-portability` | Every model, every environment: schema budget and shape, state without stickiness, model access |

Five reference documents sit alongside them: `databricks-mcp`,
`mcp-design-rules`, `data-patterns`, `environment-portability`,
`judging-rubric`.

---

## The seven phases

| Phase | Tool | Ends with |
|---|---|---|
| 0. Environment | `creator_check_environment` | What this workspace can run |
| 1. Discovery | `creator_start_design` | A design handle, and the gaps that block it |
| 2. Data profiling | `creator_add_data_source` | A profile per source, with field roles |
| 3. Tool design | `creator_design_tools` | A tool list the user has approved |
| 4. Generation | `creator_generate_project` | A complete project manifest |
| 5. Evaluation | included in generation | A golden set, a harness, a baseline |
| 6. Deployment and review | `creator_review_server` | A running app and a clean review |

Each phase ends in something reviewable, so stopping at any boundary still
leaves something usable.

---

## What a generated project contains

```
<server-name>/
├── app.yaml                   # Databricks Apps runtime config
├── databricks.yml             # bundle: resources and grants
├── pyproject.toml  requirements.txt  Dockerfile  main.py
├── server.json                # MCP registry metadata
├── .github/workflows/ci.yml   # ruff check, ruff format --check, pytest
├── src/<package>/
│   ├── app.py  config.py  models.py  routes.py
│   ├── data/                  # one module per source kind + sources.py
│   ├── tools/                 # one file per tool, each with register(mcp)
│   ├── prompts/  resources/   # starters, and the data dictionary by URI
├── tests/                     # smoke, tool discovery, and security tests
├── deploy/databricks/         # deploy.sh, smoke-test.sh, grants.sql, runbook
├── eval/
│   ├── answer_key.py          # the independent answer key
│   └── phoenix/               # agent, judges, datasets, experiment runner
├── docs/limitations.md        # what the data cannot answer
└── README.md  QUICKSTART.md  SECURITY.md
```

### Data access the generators support

| Source kind | Modality | Patterns generated |
|---|---|---|
| `uc_table` | structured | lookup, search, rank, aggregate, trend, compare, validate, describe |
| `local_snapshot` | structured | lookup, search, rank, aggregate, trend, describe |
| `ai_search_index` | unstructured | semantic search with citations, describe |
| `rest_api` | structured | lookup, search, describe |
| `genie_space` | structured | natural-language query, describe |
| `uc_function` | structured | call, describe |
| `uc_volume_files` | unstructured | list files, describe |

An unsupported pairing is emitted as a clearly marked stub and reported as a
warning, rather than a tool that silently returns nothing.

---

## Portability

Two independent problems, and the first version of this server failed both.

### Will any model drive it?

MCP is model-agnostic by protocol. A server is not automatically
model-agnostic in practice, because every tool schema is sent on every
request and read by a model with its own limits.

Measured on the first version of this server: the tool listing was **48,287
bytes of schema, about 12,900 tokens on every request**, with four tools
nested **13 levels deep**, and after the design phase the model had to
re-emit a **17,525-byte specification on every call**.

That is not only cost. MLflow's AI Gateway forwards function schemas to
Gemini verbatim; Gemini's validator does not act on `$defs`, `$ref` or
nullable `anyOf` unions, and **nested object parameters come back empty**,
with no error anywhere. Databricks' AI Gateway is MLflow-based.

After the fix, measured the same way:

| | Before | After |
|---|---|---|
| Tool schema payload | 48,287 B | **10,392 B** |
| Full listing | ~12,900 tokens | **~3,600 tokens** |
| Maximum schema depth | 13 | **4** |
| Nested object parameters | 4 tools | **0** |
| `anyOf` / `$ref` / `$defs` | present | **none** |
| State passed per call | ~17,500 B | **a 12-character handle** |

The rules, which the generators follow and `tests/test_portability.py`
enforces:

- every parameter is a scalar, an enum or a list of strings;
- optional means a concrete default, never `None`;
- a document travels as a handle, with inline JSON as a stateless fallback;
- the listing is measured against a budget in a test.

A generated 14-tool server comes out at 3,492 bytes of schema, about 873
tokens.

### Will it run where it is going?

Databricks is not one platform. From Databricks' published feature
availability:

| Feature | Commercial | FedRAMP High | IL5 (us-gov-west-1) |
|---|---|---|---|
| Databricks Apps | yes | yes | yes, horizontally scaled |
| Genie Spaces / Genie One | yes | yes | yes |
| Vector Search Standard | yes | yes | yes |
| Vector Search Storage Optimized | yes | confirm | **no** |
| Vector Search Full-Text Search | yes | confirm | **no** |
| Lakebase Search | yes | confirm | **no** |
| Public model providers | per workspace | no | no |

So in an IL5 workspace: the keyword half of a retrieval design has to be SQL
against the chunk table rather than a BM25 index; state held in process is
unreliable because Apps scale horizontally; and anything that needs a model
must call Databricks Model Serving inside the boundary.

`creator_check_environment` reports this per environment, marks anything
Databricks does not publish as unknown rather than guessing, and the review
scores an `environment_fit` dimension against it.

### One model-access pattern everywhere

The servers this generates call no model at all: tools return data and the
client's model reasons, which is portable across every provider by
construction. For the parts that do need a model, such as the evaluation
harness, there is one pattern:

```python
client = OpenAI(
    api_key=databricks_token,
    base_url=f"{workspace_host}/serving-endpoints",
)
client.chat.completions.create(model="<serving-endpoint-name>", messages=[...])
```

The `model` parameter is an endpoint name, not a vendor model id. Databricks
Model Serving presents Foundation Model APIs, external models and
customer-hosted models through that one OpenAI-compatible interface, so open
weights, a Databricks-hosted model, a routed commercial model and a
self-served model all arrive the same way, and the workspace administrator
decides which exist.

---

## Getting started

```bash
cp .env.example .env
uv sync
uv run mcp-server-creator-doctor     # will this even start here?
uv run python main.py
```

The doctor answers the first gate in one command: whether the server starts,
which skills loaded, what is optional and missing, and what the target
environment can run. It exits non-zero only when something is genuinely
broken rather than merely unconfigured.

```bash
uv run mcp-server-creator-doctor --environment gov_il5
```

The server starts in stdio mode and waits for a client. That is correct: a
local MCP server is launched by its client as a subprocess.

Connect a client with an absolute path to this repository:

```json
{
  "mcpServers": {
    "mcp-server-creator": {
      "command": "uv",
      "args": ["run", "mcp-server-creator"],
      "cwd": "/absolute/path/to/petershang_gsa_hackathon"
    }
  }
}
```

Then say: *"I want to build an MCP server over my contract data."*

### Verify

```bash
uv sync --group dev
uv run pytest tests/ -v
uv run ruff check .
uv run ruff format --check .
```

The test suite includes a round trip: it generates a sample server from a
specification, writes it to a temporary directory, and asserts the generated
project parses, registers its tools, and carries no unsafe arguments.

---

## Deploying

```bash
cp deploy/databricks/.env.example deploy/databricks/.env
set -a; source deploy/databricks/.env; set +a
bash deploy/databricks/deploy-databricks-app.sh
```

The app name must start with `mcp-`, which is what makes AI Playground and
Unity Gateway treat it as an MCP server. See
[deploy/databricks/README.md](deploy/databricks/README.md).

---

## Writing to disk

`creator_generate_project` returns a file manifest. Materializing it is a
side effect, so it is off unless an operator sets both `CREATOR_ALLOW_WRITE=true` and `CREATOR_OUTPUT_ROOT`. Every path is then resolved
inside that root and traversal is refused, and `creator_write_project`
defaults to a dry run that reports what it would write.

Leave writing off in a deployed app: there is no durable filesystem there,
and the client can write the files from the manifest instead.

---

## Honest limitations

- Generated servers are Python and FastMCP only. No TypeScript path.
- The environment table covers what Databricks publishes. Anything it does
  not publish is reported as unknown, which is honest but still means
  someone has to check the workspace.
- The in-memory design store is a development default. On Databricks Apps,
  which scale horizontally, set `CREATOR_DESIGN_STORE=file` with a directory
  every replica can read, or pass `spec_json` instead of a handle.
- The generators cover the source and pattern pairs in the table above.
  Anything else is a stub with a TODO.
- Golden answers cannot be invented. The evaluation scaffold creates the
  questions and the place to put the answers; the answers have to be
  computed independently, which is the point.
- Live Unity Catalog inspection needs credentials and a warehouse. Without
  them, profiling parses a pasted schema, which works but produces weaker
  enum candidates and no row count.
- Genie-backed tools are not deterministic, so they should not be scored
  with exact-match golden answers.

---

## Security

Read-only apart from the one guarded write tool. Tool arguments, pasted
schemas, and sample data are treated as untrusted input: they are material
to analyze, never instructions to the model. See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE).
