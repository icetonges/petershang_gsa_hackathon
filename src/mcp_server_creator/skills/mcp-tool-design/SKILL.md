---
name: mcp-tool-design
description: Design the tool surface of an MCP server so a model with no other context can answer real questions. Use when naming tools, choosing parameters and enums, writing docstrings, shaping return values, handling errors, or deciding how many tools a server should have.
---

# Designing the tool surface

## The one test that matters

A model that has never seen your data, holding only your tool names, schemas,
and docstrings, must answer a question a real user asks. Every decision below
is downstream of that test.

## Start from questions, not from data

Write the user's questions down first, verbatim. Then group them. Each group
that shares a shape becomes one tool.

```
"which of my contracts expire this quarter?"      -> contracts_expiring_soon
"which ones are past due with money still on?"    -> closeout_candidates
"how much of this account have we obligated?"     -> status_of_funds
"are we faster or slower than last year?"         -> obligation_pace
"what does the rule say about option exercise?"   -> find_policy_guidance
```

Five questions, five tools, each named in the user's language. Compare with
`query_contracts(filters, sort, limit)`, which answers all five badly because
the model has to know what "past due with money still on" means in column
terms. That knowledge is the server's job, not the model's.

## How many tools

Three to six for a focused server. Signs you have too many: two tools whose
descriptions differ only by a filter; a tool that exists because the API has
an endpoint, not because anyone asks for it. Signs you have too few: the agent
needs four chained calls to answer one question; a single tool's docstring
needs a decision tree.

Add a resolver tool whenever a real tool needs an exact internal identifier.
Humans say "Region 5"; the table says `GSA FAS AAS REGION 5`.

## Parameters

| Rule | Why |
|---|---|
| Closed set of 50 or fewer values becomes an enum | The model cannot guess a spelling it has never seen |
| Numbers get `ge` and `le` | Stops a 100,000-row request |
| Dates get a documented format in the description | ISO 8601 or state the format explicitly |
| Optional parameters get real defaults | The model should be able to call the tool with one argument |
| Free text only where the question is genuinely free text | Search queries, yes; filter values, no |
| Never a URL, raw SQL, or filesystem path | These turn a data tool into an exfiltration primitive |

Use a Pydantic model for grouped parameters (pagination, a date range) so the
schema the client sees is precise, and reuse it across tools.

```python
class Office(str, Enum):
    FAS_R5 = "GSA FAS AAS REGION 5"
    PBS_R5 = "GSA PBS REGION 5"


@mcp.tool(
    name="contract_expiring_soon",
    annotations={
        "title": "Contracts expiring soon",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def contract_expiring_soon(
    office: Annotated[Office, "Contracting office that owns the contract."],
    days_ahead: Annotated[int, Field(ge=1, le=730)] = 180,
    minimum_value: Annotated[float, Field(ge=0)] = 0.0,
    pagination: PaginationParams | None = None,
) -> dict[str, Any]:
    """Contracts for one office whose period of performance ends soon.

    One row is one contract award, not one modification. Data refreshes
    monthly and currently reflects 2026-09-30. Returns a pagination envelope
    whose items carry contract_id, vendor, end_date, days_remaining,
    obligated, and option_value_remaining.

    Does not cover: contract modifications in flight, or Defense awards
    signed in the last 90 days, which public data withholds.
    """
```

## Docstrings

The docstring is the model's only documentation. Five things, always:

1. The question it answers.
2. The grain: what one returned record represents.
3. Freshness: update cadence and the as-of date.
4. The return shape, naming the fields.
5. What it does not cover.

Point five is the one people skip and the one that prevents confident wrong
answers.

## Return values

Structured data with stable keys. The model narrates; the tool does not.

```python
{
    "as_of": "2026-09-30",
    "total": 42,
    "count": 20,
    "offset": 0,
    "has_more": True,
    "next_offset": 20,
    "items": [
        {
            "contract_id": "47QFLA20C0002",
            "vendor": "...",
            "end_date": "2026-12-31",
            "days_remaining": 54,
            "obligated": 195552467.34,
            "fields_used": ["period_of_performance.end_date", "total_obligation"],
        }
    ],
}
```

Every item carries its identifier and the fields the answer rests on, so the
agent can cite and a human can check.

For a ranking question return the top N plus the aggregate, not the
population. For an aggregate question return the number plus the group counts
behind it, not the rows.

## Errors

An error is a turn the agent has already spent. Make it buy something.

```python
{
    "error": "No office matched 'Region 5'.",
    "hint": "Call resolve_office with a partial name to see candidates.",
    "candidates": ["GSA FAS AAS REGION 5", "GSA PBS REGION 5"],
}
```

Return a result, not an exception, for expected failures: no match, empty
range, argument out of bounds. Reserve raised errors for genuine faults, and
sanitize those: no upstream body, headers, full URL, query string, stack
trace, or credential.

## Annotations

`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`. They are
hints for the client's UX, not authorization. Set them truthfully and enforce
access in code.

## Prompts and resources

- **Resource** for a data dictionary, code table, or schema: the model reads
  it without spending a tool call. URI scheme per server, e.g.
  `contracts://dictionary/fields`.
- **Prompt** for a workflow the user starts often, surfaced as a slash command.
- Many clients surface neither reliably. Anything essential also needs a tool.

## Review your own surface

- Read every tool name aloud to the person whose job it is. Would they
  recognize it?
- Could a model with only these docstrings pick the right tool for each of the
  user's questions, first try?
- Does any tool return more than a screen of data by default?
- Does every error tell the model what to do next?
- Is there a tool whose only reason to exist is that an endpoint exists?

Then run `creator_review_server`, which checks all of this mechanically.
