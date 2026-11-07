# From data to tools: the pattern catalogue

Every source resolves to a small number of question shapes. Pick the patterns
the data can actually support, and say plainly which ones it cannot.

## What the field roles unlock

| You have | You can build | You cannot build |
|---|---|---|
| An identifier | `lookup` a single record | |
| A dimension with low cardinality | enum filters, `compare` across groups | |
| A measure | `aggregate`, `rank` | |
| A date plus a measure | `trend`, pace against prior periods | |
| A date alone | "expiring soon", aging, backlog age | |
| A text field | keyword `search` | ranked relevance |
| An AI Search index | `semantic_search` with citations | exact aggregates |
| A Genie space | `nl_query` over curated tables | guaranteed determinism |

No measure means no aggregate tool. No date means no trend tool. Say so in the
design rather than generating a tool that returns nulls.

## Structured sources

### Unity Catalog table through a SQL warehouse

The default for tabular data. The server holds parameterized SQL; the tool
arguments fill in only validated values, never SQL fragments.

- Validate every catalog, schema, table, and column name against a plain
  identifier pattern, then backtick-quote it. Column names chosen from an
  enum of real columns is safer than a free-text column argument.
- Bind values as query parameters. Never concatenate a caller value into SQL.
- Put a `LIMIT` on every statement, and a timeout on every call.
- Push aggregation into SQL. Returning 100,000 rows to sum them in Python
  wastes the warehouse and the agent's context.
- Report the as-of date with every result; a table refreshed monthly should
  not imply today's numbers.

### Unity Catalog function

Already governed and already a tool: expose it when the logic belongs to the
data team rather than the server. `EXECUTE` is the grant.

### Genie space

Natural-language analytics over curated tables, read-only, with a semantic
layer someone maintains. Good for open-ended analytical questions. Not
deterministic, so do not use it where an exact number must be reproducible,
and do not evaluate it with exact-match golden answers.

### Local snapshot shipped with the server

An underrated choice. A parquet or CSV file packaged in the repository makes
the server work with no credentials, no warehouse, and no network: a reviewer
can clone and run it, and an evaluation is reproducible. Use it for reference
data, for a demo mode beside the live path, and whenever the data is small and
slow-moving. Record the data date in the file name or a sidecar.

### REST API

Fix the origin in operator-controlled configuration. Pass only validated
relative paths. Disable redirects. Cap the response size. Set a timeout.
Respect the quota: if the API allows 1,000 calls a day, the server cannot call
it once per question, so ingest into a table or a snapshot on a schedule.

## Unstructured sources

### The pipeline

```
files in a volume -> extract text -> chunk -> table with CDF enabled
  -> AI Search Delta Sync index -> semantic_search tool with citations
```

Decisions that change answer quality more than the model does:

- **Chunk on structure, not length, when structure exists.** Sections and
  headings beat a fixed 512-token window for policy documents, because an
  answer that spans a section boundary is an answer the agent cannot find.
- **Keep tables out of prose chunks.** Extract them as markdown into their own
  column or their own chunks. A chunked table is a lost number.
- **Carry citation columns on every chunk**: document title, page or section,
  source path or URL, and effective date. A retrieval tool that cannot cite
  cannot be audited.
- **Keep the effective date.** Superseded policy text retrieved confidently is
  worse than no answer. Filter by date when the corpus has versions.

### The retrieval tool

Return the top chunks with their citation fields and scores, not a synthesized
answer. Let the agent compose and attribute. Expose a filter on the metadata
the user actually narrows by (program, year, document type) as enums.

State the limits in the docstring: retrieval finds passages, it does not count
documents, and similarity scores are not confidence.

### Keyword and hybrid

For identifier-like queries (a clause number, a contract id, a statute cite),
keyword beats semantic search outright. Offer both when the corpus has both
kinds of question, and say in each docstring which to reach for.

Two implementations, and only one is universal. A storage-optimized endpoint
can carry a BM25 keyword index with no embeddings, but neither
storage-optimized endpoints nor full-text search exist in IL5 GovCloud DoD.
The portable implementation is an exact match or a `LIKE` against the chunk
table that backs the index. It needs no extra feature, it is deterministic,
and for an identifier it is more accurate anyway. Build that first.

## Mixed sources: the common real case

Most useful servers read one structured source and one unstructured source:
the numbers and the rules behind them. Keep them in separate tools with
separate names and let the agent join them in its answer.

- `status_of_funds(account, fiscal_year)` returns the numbers.
- `find_policy_guidance(question, topic)` returns the passages.
- Do not build one tool that does both. The agent composes better than a
  hard-coded join, and each tool stays testable.

## Writes

Default to read-only. If a tool must write:

- Make the write a separate, clearly named tool, never a flag on a read tool.
- Set `readOnlyHint: false` and `destructiveHint` honestly.
- Require an explicit confirmation argument that the model cannot default.
- Write to your own store, not to the system of record, unless the system of
  record is the point.
- Log every write with the caller identity and the arguments.
