# Portability: every model, every environment

Two independent portability problems get confused with each other. A server
can be perfectly model-agnostic and still fail to deploy, and it can deploy
cleanly and still be undrivable by half the models that will meet it.

## Problem 1: will any model drive it?

MCP is model-agnostic by protocol. A server is not automatically
model-agnostic in practice, because every tool schema is sent on every
request and read by a model with its own limits.

### Schema weight is a per-call cost

A tool listing is not a one-time setup cost. It is in the prompt on every
request, before any work happens. A heavy listing means every call is slower
and dearer, and some clients cap how much schema they will send at all.

Budget it. A listing over roughly 4,000 tokens deserves a hard look; one over
10,000 is a defect. Measure it rather than guessing:

```python
total = sum(
    len(json.dumps(tool.parameters, separators=(",", ":")))
    for tool in await mcp.list_tools()
)
```

### Schema shape decides which models can call it at all

Providers honor different subsets of JSON Schema. Three constructs cause
most real failures:

| Construct | What happens |
|---|---|
| A nested object parameter | Some providers return empty arguments for it |
| `$ref` / `$defs` | Rejected outright by some validators, ignored by others |
| `anyOf: [T, null]` for an optional | Ignored, so the field is misread |

This is documented, not theoretical. MLflow's AI Gateway forwards an
OpenAI-shaped function schema to Gemini verbatim; Gemini's function-calling
validator does not act on `$defs`, `$ref` or nullable unions, and **nested
object parameters come back empty**. Databricks' AI Gateway is MLflow-based,
so an agent reaching your server through it hits exactly that path. Gemini's
own documentation adds that it may reject very large or deeply nested
schemas, limits nesting to 32 levels, and in one API spells the reference
keywords without the `$`.

The rules that follow, and they cost nothing:

1. **Every parameter is a scalar, an enum, or a list of strings.** Never a
   nested object, never a list of objects.
2. **Optional parameters get a concrete default**, not `None`. `str = ""`
   produces a plain string schema; `str | None = None` produces a nullable
   union.
3. **Pass a document as a JSON string**, not as a typed nested object, when
   a tool genuinely needs one. A string parameter is one line of schema.
4. **Keep state behind a short handle.** If a workflow needs a 17 KB
   document between calls, the server holds it and the model repeats a
   twelve-character handle. Asking a model to re-emit a large nested object
   on every call is both a token cost and an accuracy tax, and the weaker
   the model the worse it gets.
5. **Measure it in a test**, so a refactor cannot quietly undo any of this.

### State without sessions, and without stickiness

MCP 2026-07-28 removes sessions, so each request stands alone. The pattern is
an explicit handle the model passes back. That needs one more thought on
Databricks: Apps support horizontal scaling, so a handle resolved against
in-process memory can miss when the next request lands on another replica.

Offer both, and say which is active: a handle backed by a store every replica
can read, and the document inline as a fallback for a stateless deployment.

## Problem 2: will it run where it is going?

Databricks is not one platform. Verified from Databricks' published feature
availability, 2026-11-07:

| Feature | Commercial | FedRAMP High | IL5 (us-gov-west-1) |
|---|---|---|---|
| Databricks Apps | yes | yes | yes, with horizontal scaling |
| Genie Spaces / Genie One | yes | yes | yes |
| Vector Search Standard | yes | yes | yes |
| Vector Search Storage Optimized | yes | confirm | **no** |
| Vector Search Full-Text Search | yes | confirm | **no** |
| Lakebase Search | yes | confirm | **no** |
| Public model providers | per workspace | no | no |

What that changes in a design:

- **Keyword retrieval.** With no full-text index there is no BM25 path. The
  portable implementation is an exact match or a `LIKE` against the chunk
  table behind the index. It needs no extra feature and, for an identifier,
  is more accurate anyway. Build it first everywhere.
- **State.** Horizontal scaling means in-process state is unreliable.
- **Model access.** Public providers are unreachable. Anything that needs a
  model calls Databricks Model Serving inside the boundary. Defaulting to a
  public endpoint does not merely fail: in an accredited environment the
  attempt is itself a finding.
- **Packages.** Do not assume the app build can reach a public index. Keep
  the dependency list short and installable from a mirror.
- **The newest surfaces.** Whether the managed MCP endpoints and the Unity
  Gateway MCPs UI exist in a government environment is not published. Do not
  make either a dependency: deploy the app, grant `CAN USE`, connect by URL
  with an OAuth token, and treat the UI as a convenience.

Anything not published for an environment is unknown, not absent. Report it
that way and let a probe or the user resolve it; a confident guess about a
restricted environment is worse than an admitted gap.

## One model-access pattern for every environment

Databricks Model Serving presents Foundation Model APIs, external models
(OpenAI, Anthropic, Gemini and others) and customer-hosted models through one
OpenAI-compatible interface, with OpenAI-compatible function calling.

```python
client = OpenAI(
    api_key=databricks_token,
    base_url=f"{workspace_host}/serving-endpoints",
)
client.chat.completions.create(model="<serving-endpoint-name>", messages=[...])
```

The `model` parameter is an endpoint name, not a vendor model id. That one
indirection is what makes identical code work in a commercial workspace and
in an enclave: the workspace administrator decides which models exist, and
the code does not change. Open weights, a Databricks-hosted foundation
model, a routed commercial model, or a model someone serves themselves all
arrive the same way.

A corollary worth stating: the server you generate should not call a model at
all. Tools return data; the client's model does the reasoning. A server with
no model dependency is portable across every provider by construction, and
the only thing that needs this pattern is the evaluation harness.

## The portability checklist

- [ ] Tool listing measured, and under budget.
- [ ] No nested object parameters anywhere.
- [ ] No `$ref`, `$defs` or nullable `anyOf` in any schema.
- [ ] Optional parameters have concrete defaults.
- [ ] Multi-step state is a short handle, with an inline fallback.
- [ ] The server itself calls no model.
- [ ] The target environment is declared, and every source kind checked
      against its published feature availability.
- [ ] The keyword retrieval path works without a full-text index.
- [ ] Anything needing a model points at Databricks Model Serving.
- [ ] A credential-free path exists, so the server can be demonstrated with
      no warehouse and no network.
- [ ] The transport name used is the one every library version accepts.
- [ ] All of the above asserted in a test, not just documented.
