---
name: structured-data-mcp
description: Turn tabular data into MCP tools. Use when the source is a Unity Catalog table, a SQL warehouse query, a Unity Catalog function, a Genie space, a REST API returning records, or a CSV or parquet snapshot, and you need safe parameterized queries, aggregate and trend tools, and honest freshness reporting.
---

# Structured data as MCP tools

## Decide the access path first

| Source | Use when | Watch for |
|---|---|---|
| Unity Catalog table via SQL warehouse | The default for tabular data | Grants belong to the app service principal, not you |
| Unity Catalog function | The logic belongs to the data team | `EXECUTE` grant; function signature is your schema |
| Genie space | Open-ended analytical questions over curated tables | Not deterministic; do not exact-match it in evals |
| REST API | The data has no warehouse home | Quotas; ingest rather than call per question |
| Local snapshot (parquet or CSV) | Small, slow-moving, or you want a keyless demo | Record the data date; refresh on a schedule |

A snapshot committed beside the server is worth more than it looks. A reviewer
clones the repository and everything runs, with no warehouse, no credentials,
and no network. Ship one as a demo mode even when the live path is the point.

## Writing the query layer safely

Two kinds of value reach a query, and they are not interchangeable.

**Identifiers** (catalog, schema, table, column) cannot be bound as
parameters, so they must be validated and quoted. Validate against a plain
identifier pattern and backtick-quote. Better: never accept a column name as
free text; offer an enum of the real columns.

**Values** (a filter, a date, a threshold) are always bound as query
parameters. Never concatenated.

```python
from databricks import sql as dbsql

SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,254}$")


def quote_ident(part: str) -> str:
    if not SAFE_IDENT.match(part):
        raise ValueError(f"{part!r} is not a plain identifier")
    return f"`{part}`"


TABLE = ".".join(quote_ident(p) for p in (CATALOG, SCHEMA, TABLE_NAME))

QUERY = f"""
SELECT office, SUM(obligated) AS obligated, COUNT(*) AS awards
FROM {TABLE}
WHERE fiscal_year = ? AND office = ?
GROUP BY office
ORDER BY obligated DESC
LIMIT ?
"""

with dbsql.connect(
    server_hostname=host,
    http_path=f"/sql/1.0/warehouses/{warehouse_id}",
    credentials_provider=provider,
) as connection:
    with connection.cursor() as cursor:
        cursor.execute(QUERY, [fiscal_year, office, limit])
        rows = cursor.fetchall()
```

Rules for every statement:

- A `LIMIT` on every query, driven by a bounded parameter.
- A timeout on every call.
- Aggregate in SQL. Pulling 100,000 rows to sum them in Python wastes the
  warehouse and the agent's context.
- One connection per call, closed. Apps run stateless across replicas.

## Authentication inside a Databricks App

Databricks injects `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`, and
`DATABRICKS_CLIENT_SECRET`. The warehouse id comes from `app.yaml` via
`valueFrom: sql_warehouse`. Use the SDK's config so the same code works
locally with a CLI profile and in the app with the service principal:

```python
from databricks.sdk.core import Config, oauth_service_principal

config = Config(
    host=os.environ["DATABRICKS_HOST"],
    client_id=os.environ.get("DATABRICKS_CLIENT_ID"),
    client_secret=os.environ.get("DATABRICKS_CLIENT_SECRET"),
)
provider = lambda: oauth_service_principal(config)
```

The service principal is a different identity from your user. Grant it
explicitly: `USE CATALOG`, `USE SCHEMA`, `SELECT` on the table, and `CAN USE`
on the warehouse. A server that works locally and returns empty rows once
deployed has almost always hit this.

## The tool patterns tabular data supports

**Lookup** — one record by identifier. Needs an identifier field. Return the
record plus the fields the answer rests on. Return a result with a hint, not
an exception, when nothing matches.

**Search** — keyword filter over text columns. Cheap, exact, no index needed.
Document that it matches substrings, not meaning.

**Rank** — top N by a measure within a scope. Return the top N *and* the
total, so the agent can say "the top 5 of 212, which are 61 percent of the
total".

**Aggregate** — a number plus the groups behind it. Needs a measure. Push
`GROUP BY` into SQL.

**Trend** — a measure over a date dimension, usually against the same period
in prior years. Needs a date and a measure. Return the periods as rows with a
computed change, not a sentence.

**Compare** — the same measure across two scopes or two periods, with the
difference computed server-side. The model should not be doing arithmetic.

**Describe source** — a tool or resource returning the data dictionary:
columns, roles, enum values, grain, cadence, as-of date. This is the cheapest
accuracy win available, because the agent stops guessing column semantics.

If there is no measure, there is no aggregate or trend tool. If there is no
date, there is no trend or expiring-soon tool. Say so rather than generating a
tool that returns nulls.

## Freshness, honestly

Every result carries `as_of`. State the cadence in the docstring. If the table
refreshes monthly with a one-month lag, the docstring says so, because an
agent that believes it has today's numbers will say today's numbers.

Read the as-of date from the data where you can (a max of the load timestamp)
rather than hardcoding it. A hardcoded date goes stale silently.

## Unity Catalog functions

A function is already a governed tool. Expose it when the calculation belongs
to the data team and should change without redeploying your server. Its
signature is your input schema, so the function's parameter names and types
matter to the model; name them for humans.

## Genie spaces

Good for the long tail of analytical questions over curated tables, read-only,
with a semantic layer someone maintains. Treat it as a complement to your
deterministic tools, not a replacement: it answers what you did not anticipate.
Do not score it with exact-match golden answers, and do not use it where a
number must be reproducible.

## REST sources

- Fix the origin in operator-controlled configuration. Never a tool argument.
- Pass validated relative paths only; encode path segments.
- Disable redirects; cap response size; set an explicit timeout.
- Respect the quota. An API allowing 1,000 calls a day cannot be called once
  per question: ingest to a table or snapshot on a schedule and query that.
