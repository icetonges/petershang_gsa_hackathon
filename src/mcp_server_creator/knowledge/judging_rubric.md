# Quality rubric

Two layers: what makes a server good, and what the GSA MCP hackathon scores.
They overlap almost entirely, which is the point.

## GSA judging criteria

| Criterion | What it rewards | How a server earns it |
|---|---|---|
| Technical quality | Readable code; your own evaluation setup and results; MCP design principles: typed inputs, unambiguous tool names, tools built around user goals rather than thin REST wrappers | One tool per file, Pydantic inputs with enums, an evaluation with a measured baseline, tests that run in CI |
| Innovation | A tool surface or workflow nobody has shipped yet | Goal-level tools, an agentic workflow with a verifier, composition with managed servers |
| Mission alignment | A real government job gets easier | Name the user, the task they do by hand today, and the time it takes |
| Presentation | A clear story and a demo that works | Problem, solution, decisions, demo, evaluation, security, lessons |

No weights are published. Technical quality is the only criterion with a
checklist in the rules, so it is the one to over-deliver on.

## Deliverables

- A repository created from the GSA template.
- A slide deck: problem, solution, approach and decisions, demo.
- An evaluation document: methodology, metrics, security, lessons.

## Scoring a design review

Each dimension scores 0 to 5. Below 3 on any dimension is a finding.

| Dimension | 5 looks like |
|---|---|
| Goal orientation | Every tool is named after a question a real user asks. |
| Input design | Every closed set is an enum; identifiers have a resolver tool. |
| Documentation | Every docstring gives grain, cadence, as-of date, return shape, and limits. |
| Context economy | Paginated, capped, aggregated server-side; no tool dumps raw rows. |
| Error quality | Every failure returns an actionable hint and leaks nothing. |
| Security posture | Read-only by default; no URL, SQL, or path arguments; identifiers validated; retrieved content treated as data. |
| Evaluation | Held-out answer key, no-tool baseline, decoys, no-action cases, injection case, repeats. |
| Reproducibility | A reviewer can clone and run it with no credentials. |
| Deployment | Named and configured so the platform recognizes it; grants documented for the service principal. |
| Honesty | Limitations section names what the data cannot answer. |

## What distinguishes winning entries

Patterns that recur across MCP and agent hackathon winners:

1. The model is used only where judgment is needed. Arithmetic, filtering, and
   document assembly are deterministic code behind the tools.
2. One small custom server holds the domain engine, three to six tools, and
   agents call it at runtime rather than importing it.
3. Existing servers are consumed alongside the custom one instead of rebuilt.
4. A human approval gate is enforced in code, not in a prompt.
5. The evaluation uses an answer key the agent cannot see, plus decoys and
   cases where the correct action is to do nothing.
6. The workflow starts from a trigger: a schedule, a webhook, a detection
   query. Not a person typing.
7. Memory records what was already handled so a second run does not repeat the
   first.
8. A keyless mock or snapshot mode lets a judge run everything for free.
9. Retrieved data is treated as evidence, never as instructions.
10. The write-up names a user, a dollar or hour value, and an honest
    limitations section.

Two cautions from the same sources: collapse multiple agents into one
orchestrator with named phases when handoffs start breaking, and check that
your headline metric can actually go down, or it teaches the agent nothing.
