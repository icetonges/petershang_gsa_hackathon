# Evaluating this server

Two different things get evaluated in this repository, and they are easy to
confuse.

## 1. Servers this one generates

Every generated project ships its own evaluation suite: a Phoenix harness, an
independently computed answer key, a golden question set seeded from the
user's own questions, and the four case types that make a score mean
something. That is produced by `creator_generate_project` and documented in
the generated `eval/README.md`.

The methodology lives in the bundled `mcp-evaluation-harness` skill. Load it
with `creator_load_document(name="mcp-evaluation-harness")`.

## 2. This server itself

The creator's own quality question is different: can a model holding only
these eight tool names and docstrings take a person from "I have data and a
job I do by hand" to a working server?

### What the test suite already covers

`uv run pytest tests/ -v` checks the mechanical half:

- every tool, prompt, and resource registers;
- every tool is discoverable and documented;
- no tool accepts a URL, raw SQL, or a filesystem path;
- identifier validation rejects injection attempts;
- path containment rejects traversal;
- **a round trip**: a sample specification is generated, written to a
  temporary directory, and the generated project is asserted to parse,
  register its tools, and carry no unsafe arguments.

That round trip is the most valuable test here. It means a change to a
generator template fails CI rather than failing a user.

### What a full evaluation would add

Not yet built, and worth being honest about. It would need:

- **Golden tasks, not questions.** "Build a server over this schema for this
  user" with a rubric per task, rather than a single verifiable answer.
- **A judge over the generated artifact**, scoring the result against
  `knowledge/judging_rubric.md`: are the tools goal-oriented, are closed sets
  enums, does every docstring carry grain and freshness.
- **A no-tool baseline**: the same tasks given to a model with no server
  attached, which is the number that says what this server is worth.
- **Discovery behavior**: does the agent ask the blocking questions before
  generating, or does it guess? That is the failure mode most worth catching.

Until that exists, the claim this repository makes is the narrow one: the
generators are deterministic and tested, and the skills encode the
methodology. Whether an agent uses them well is unmeasured.

## The upstream skills

`.agents/skills/mcp-builder/` and `.agents/skills/mcp-eval/` are the GSA
template's own skills, kept as-is. They cover general MCP construction and
the Phoenix harness in depth. The bundled library extends them with the
Databricks path, the data patterns, and the generation and review tooling.
