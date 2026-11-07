# Quickstart

From clone to building someone's MCP server, in about five minutes.

## 1. Install

```bash
pip install uv          # or: brew install uv
cp .env.example .env
uv sync
```

## 2. Check it will start

```bash
uv run mcp-server-creator-doctor
```

One command, plain language: whether the server imports, which skills
loaded, what is optional and absent, and what your target environment can
run. It exits non-zero only when something is genuinely broken rather than
merely unconfigured.

Add the environment the servers you build will be deployed into, and it
reports the constraints that apply there:

```bash
uv run mcp-server-creator-doctor --environment gov_il5
```

## 3. Run

```bash
uv run python main.py
```

The server starts in **stdio** mode and waits. Nothing prints a URL, which
is correct: a local MCP server is launched by its client as a subprocess.
Press Ctrl-C to stop.

## 4. Connect a client

### Claude Code

Create or edit `.mcp.json` with an **absolute path** to this repository:

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

### Claude Desktop

The same block in
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows), then restart.

## 5. Build something

Ask the client:

> I want an MCP server over my contract data so a budget analyst can ask
> which contracts expire this quarter.

A well-behaved client will call `creator_list_library`, load the
`mcp-server-creator` skill, and start asking discovery questions before
writing anything. If it jumps straight to code, say "run discovery first".

To drive it by hand:

```
creator_list_library
creator_load_document(name="mcp-server-creator")
creator_check_environment(environment="gov_il5")
creator_start_design(purpose="...", primary_user="...", jobs_to_be_done=[...])
  -> design_id="dsn_1a2b3c4d"
creator_add_data_source(design_id="dsn_1a2b3c4d", name="contracts", ...)
creator_design_tools(design_id="dsn_1a2b3c4d")
creator_review_server(design_id="dsn_1a2b3c4d")
creator_generate_project(design_id="dsn_1a2b3c4d")
```

`creator_start_design` returns a twelve-character `design_id`, and every
later call takes that handle. MCP has no sessions, so the server holds the
document and the model repeats the handle, rather than re-emitting 17 KB of
JSON on every call.

If you would rather hold the document yourself, or the server runs several
replicas with the in-memory store, pass `spec_json` instead: every
design-consuming tool accepts either.

## 6. Get the files

`creator_generate_project` returns a file index, not every file body, because
a full project runs to hundreds of kilobytes.

- Ask for specific files with `include_contents=["src/pkg/tools/x.py"]`.
- Or have your client write the files itself from the manifest.
- Or enable writing: set `CREATOR_ALLOW_WRITE=true` and
  `CREATOR_OUTPUT_ROOT=/some/dir` in `.env`, then call
  `creator_write_project` (it dry-runs first).

## 7. Verify

```bash
uv sync --group dev
uv run pytest tests/ -v      # includes a generate-and-check round trip
uv run ruff check .
```

## 8. Deploy

```bash
cp deploy/databricks/.env.example deploy/databricks/.env
# edit APP_NAME and DATABRICKS_HOST
set -a; source deploy/databricks/.env; set +a
bash deploy/databricks/deploy-databricks-app.sh
```

See [deploy/databricks/README.md](deploy/databricks/README.md) for the full
runbook, and apply the grants: the app runs as its own service principal, not
as you.

## 9. Optional: live data profiling

With `DATABRICKS_HOST`, `DATABRICKS_WAREHOUSE_ID`, and a credential in
`.env`, `creator_profile_data_source` reads a real table's schema, row count,
and distinct values instead of parsing a pasted schema. Everything works
without it.
