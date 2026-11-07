---
name: mcp-server-creator
description: Pointer to this repository's bundled MCP server creation skill library. Use when working inside this repository on the mcp-server-creator server itself, or when you need the full skills for designing, generating, evaluating, deploying, or reviewing a custom MCP server on Databricks.
---

# MCP server creator — in-repo pointer

The full skill library lives inside the package, not here, because a deployed
Databricks App only receives the synced source tree and `.dockerignore`
excludes `.agents/`. Anything the running server must serve has to live under
`src/mcp_server_creator/`.

## Where the real skills are

```
src/mcp_server_creator/skills/
├── mcp-server-creator/        # entry point: the six-phase build sequence
├── mcp-tool-design/           # naming, parameters, docstrings, returns, errors
├── structured-data-mcp/       # UC tables, SQL, UC functions, Genie, REST
├── unstructured-data-mcp/     # extraction, chunking, AI Search, citations
├── databricks-mcp-deployment/ # Apps, app.yaml, bundles, grants, registration
├── mcp-evaluation-harness/    # golden sets, answer keys, baselines, injection
└── mcp-security-review/       # the agent-specific failure modes
```

Start with `src/mcp_server_creator/skills/mcp-server-creator/SKILL.md`.

Supporting reference material is in `src/mcp_server_creator/knowledge/`:
`databricks_mcp.md`, `mcp_design_rules.md`, `data_patterns.md`, and
`judging_rubric.md`.

## Reaching them at runtime

When the server is running, the same content is served over MCP:

| Tool | Returns |
|---|---|
| `creator_list_skills` | Every skill with its description and token cost |
| `creator_load_skill` | One skill's full body and its reference index |
| `creator_load_knowledge` | One reference document |

Resources mirror this at `creator://skills/{name}` and
`creator://knowledge/{slug}`.

## Also in this repository

`.agents/skills/mcp-builder/` and `.agents/skills/mcp-eval/` are the upstream
GSA template skills, kept as-is. They cover general MCP construction and the
Phoenix harness. The bundled library above extends them with the Databricks
deployment path, the structured and unstructured data patterns, and the
generation and review tooling this server exposes.
