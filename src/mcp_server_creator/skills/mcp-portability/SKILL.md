---
name: mcp-portability
description: Make an MCP server usable by any model and deployable in any Databricks environment. Use before designing, when a tool schema grows, when state has to survive between calls, when choosing how anything reaches a model, or when the target is a FedRAMP High or IL5 workspace.
---

# Portability: every model, every environment

Two separate problems, routinely confused. A server can be perfectly
model-agnostic and still fail to deploy, and it can deploy cleanly and still
be undrivable by half the models that meet it.

## Before designing: ask which environment

Call `creator_check_environment`. If the user does not know, ask them one
question: "commercial Databricks, GovCloud, or GovCloud DoD?" It rules out
whole branches.

The gaps are published, not guessable. In IL5 (`us-gov-west-1`): Apps,
Genie and Vector Search Standard exist; **Vector Search Storage Optimized,
Vector Search Full-Text Search and Lakebase Search do not**; public model
providers are unreachable; and Apps scale horizontally.

Default to `unknown` when you cannot find out. It recommends the two source
kinds that need nothing special, a packaged snapshot and a REST origin, and
widening from there beats designing on a feature that turns out to be absent.

## While designing tools: four rules that cost nothing

Every tool schema is sent on **every request** and read by a model with its
own limits. Providers honor different subsets of JSON Schema, and three
constructs cause most real failures: a nested object parameter, `$ref` or
`$defs`, and `anyOf: [T, null]` for an optional.

This is documented. MLflow's AI Gateway forwards function schemas to Gemini
verbatim; Gemini's validator does not act on those constructs, and **nested
object parameters come back empty**, with no error anywhere. Databricks' AI
Gateway is MLflow-based.

1. **Scalars, enums and lists of strings only.** No nested objects, no lists
   of objects.
2. **Optional means a concrete default**, not `None`. `str = ""` is a plain
   string schema; `str | None = None` is a nullable union.
3. **A document travels as a JSON string** when a tool truly needs one, or
   better, behind a short handle.
4. **Budget the listing.** Over about 4,000 tokens deserves a hard look;
   over 10,000 is a defect. Measure it, do not estimate it.

## When a workflow needs state

MCP 2026-07-28 removes sessions, so each request stands alone. Return an
explicit handle the model passes back. On Databricks that needs one more
thought: Apps scale horizontally, so a handle resolved against in-process
memory can miss when the next request lands elsewhere.

Offer both, and say which is active: a handle backed by a store every replica
can read, and the document inline as a fallback for a stateless deployment.
Never ask a model to re-emit a large nested document each turn; the weaker
the model, the worse that goes.

## When anything needs a model

The server you generate should not call one. Tools return data; the client's
model reasons. A server with no model dependency is portable across every
provider by construction.

For the parts that genuinely need a model, such as an evaluation harness,
there is one pattern that works everywhere:

```python
client = OpenAI(
    api_key=databricks_token,
    base_url=f"{workspace_host}/serving-endpoints",
)
client.chat.completions.create(model="<serving-endpoint-name>", messages=[...])
```

The `model` parameter is an endpoint name, not a vendor model id. Databricks
Model Serving presents Foundation Model APIs, external models and
customer-hosted models through that one OpenAI-compatible interface, with
OpenAI-compatible function calling. So open weights, a Databricks-hosted
model, a routed commercial model and a self-served model all arrive the same
way, and the workspace administrator decides which exist.

Never default to a public provider endpoint. In an accredited boundary it
fails closed, and the attempt is itself a finding.

## When choosing a library version

Use the call that every version accepts. The streamable-HTTP transport is
named `streamable-http` in every FastMCP release that has it; `http` is a
newer alias an older pinned version will reject. An environment behind an
internal mirror may be several releases behind, and that is not a bug to
argue with.

## Retrieval, specifically

With no full-text index there is no BM25 path. The portable keyword
implementation is an exact match or a `LIKE` against the chunk table behind
the index. It needs no extra feature, it is deterministic, and for an
identifier it is more accurate than BM25 anyway. Build it first everywhere
and treat the index as an optimization you may never be permitted to make.

## Prove it, do not claim it

Every rule above belongs in a test, because a refactor will otherwise undo
it quietly. Generated projects ship `tests/test_portability.py` asserting the
schema budget, the absence of nested object parameters and risky keywords,
and the schema depth. Keep it.

Full detail, including the environment feature table, is in the
`environment-portability` knowledge document.
