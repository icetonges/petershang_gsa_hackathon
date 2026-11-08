"""Starter prompts shown to new users: one source of truth.

Every example names a table in the `samples` catalog, which Databricks
provides in every Unity Catalog workspace, so a first try cannot fail on a
missing table. The dates in the questions fall inside each table's data
(bakehouse: May 1-17, 2024; nyctaxi: Jan-Feb 2016; tpch: 1992-1998), so
the questions return rows. The last example is a template for the user's
own table, and it says to confirm the table exists before designing.

Used by the server instructions (app.py), the "/" landing route
(routes.py), and the README. Keep the README in step with this file.
"""

from __future__ import annotations

from typing import Final, TypedDict


class Example(TypedDict):
    persona: str
    tables: list[str]
    prompt: str


EXAMPLES: Final[list[Example]] = [
    {
        "persona": "bakery franchise manager",
        "tables": [
            "samples.bakehouse.sales_transactions",
            "samples.bakehouse.sales_franchises",
        ],
        "prompt": (
            "Use MCP A Creator to build an MCP server for a bakery franchise "
            "manager, using samples.bakehouse.sales_transactions and "
            "samples.bakehouse.sales_franchises. They ask: 'Which franchise "
            "had the most revenue from May 1 to May 17, 2024?', 'What are "
            "the top 5 products by revenue?' and 'What were daily sales for "
            "one franchise?' It must refuse to show card numbers or "
            "individual customers."
        ),
    },
    {
        "persona": "supply chain planner",
        "tables": ["samples.tpch.lineitem", "samples.tpch.orders"],
        "prompt": (
            "Use MCP A Creator to build an MCP server for a supply chain "
            "planner who tracks deadlines, using samples.tpch.lineitem and "
            "samples.tpch.orders. They ask: 'Which line items arrived after "
            "their commit date in 1997?', 'How many urgent orders are still "
            "open?' and 'Which ship mode is late most often?' It must "
            "refuse questions about individual clerks."
        ),
    },
    {
        "persona": "city transportation analyst",
        "tables": ["samples.nyctaxi.trips"],
        "prompt": (
            "Use MCP A Creator to build an MCP server for a city "
            "transportation analyst, using samples.nyctaxi.trips. They ask: "
            "'What was the average fare per mile by pickup ZIP in January "
            "2016?', 'Which hour of the day is busiest?' and 'What are the "
            "top 10 drop-off ZIPs?' It must refuse to identify individual "
            "riders or trips."
        ),
    },
    {
        "persona": "customer experience lead",
        "tables": ["samples.bakehouse.media_customer_reviews"],
        "prompt": (
            "Use MCP A Creator to build an MCP server for a customer "
            "experience lead, using the review text in "
            "samples.bakehouse.media_customer_reviews. They ask: 'What do "
            "customers complain about most?', 'Show reviews that mention "
            "wait times' and 'Which franchise has the most negative "
            "reviews?' Every answer must cite the review date and "
            "franchise, and it must refuse to make up reviews."
        ),
    },
    {
        "persona": "contracting officer (your own table)",
        "tables": [],
        "prompt": (
            "Use MCP A Creator to build an MCP server for a contracting "
            "officer, using <catalog.schema.awards>, an awards table you "
            "can read. First confirm the table exists and find its end-date "
            "column. They ask: 'Which awards expire in the next 90 days?' "
            "and 'What is the total value by vendor?' It must refuse "
            "questions about individual employees."
        ),
    },
]


def prompts() -> list[str]:
    """The example prompts, in display order."""
    return [e["prompt"] for e in EXAMPLES]


def sample_tables() -> list[str]:
    """Every concrete table the examples name."""
    return [t for e in EXAMPLES for t in e["tables"]]


def instructions_text() -> str:
    """The example paragraph for the server instructions."""
    lines = [f"- {e['persona']}: {e['prompt']}" for e in EXAMPLES]
    return (
        "If they are unsure, offer one of these examples. The samples "
        "tables exist in every Unity Catalog workspace:\n" + "\n".join(lines)
    )
