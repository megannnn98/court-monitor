# ADR 0009: LangGraph Research Orchestration

## Status

Accepted

## Context

ADR 0008 introduced `ResearchRequest` → `ResearchService` → `ResearchResponse`:
a deterministic, typed research backend over canonical persons. Users still have
to know the request schema (enum values, snapshot ids). The next step is to
accept a natural-language question such as

> Найди людей, которых преследовали за антивоенную деятельность и которых нет в
> перечне Росфинмониторинга.

and answer it without letting a language model become a source of facts about
real people.

## Decision

**LLM interprets intent; domain services determine facts.**

```text
Natural Language
       ↓
      LLM (Together AI, request intake only)
       ↓
ResearchRequest (candidate) ─→ snapshot resolution ─→ Pydantic validation
       ↓
   LangGraph (orchestration, deterministic routing)
       ↓
ResearchService (ADR 0008)
       ↓
PostgreSQL
       ↓
deterministic assembly ─→ ResearchQueryResult
```

Graph topology (`src/research_workflow/graph.py`):

```text
START → request_intake ─┬─ provider error ───────────────→ workflow_failed → END
                        ├─ unsupported / ambiguous ──────→ clarification → END
                        └─ ok → resolve_snapshot ─┬─ no snapshot → workflow_failed
                                                  └─ validate_request ─┬─ LLM broke schema → workflow_failed
                                                                       ├─ domain rule violated → clarification
                                                                       └─ research ─┬─ unknown snapshot → clarification
                                                                                    └─ assemble_response → END
```

Components:

- `research_workflow/intake.py` — `ResearchRequestParser` protocol and
  `LlmResearchRequestParser`: system prompt (`prompts/request_intake.md`, enum
  values rendered from code) + JSON schema of the intake envelope, which embeds
  the real `ResearchRequest` schema.
- `research_workflow/llm.py` — `StructuredLlmClient` protocol and typed provider
  errors (timeout, unavailable, authentication, rate limit, request rejected,
  invalid response, not configured).
- `together_llm_client.py` — the only Together-specific code: `httpx` call to
  `/v1/chat/completions` with `response_format: json_schema`, `temperature=0`,
  configuration from `TOGETHER_API_KEY`, `TOGETHER_MODEL`,
  `TOGETHER_TIMEOUT_SECONDS`.
- `research_workflow/graph.py`, `state.py`, `assembly.py`, `models.py` — graph,
  typed state (`ResearchGraphState`), deterministic result assembly
  (`ResearchQueryResult`).
- `rosfinmonitoring_snapshot_lookup.py` — latest imported snapshot (has entries),
  used by `resolve_snapshot`.
- `research_workflow_factory.py` — composition root used by the CLI (`ask`) and
  FastAPI (`POST /research/query`).

### Why LangGraph does not execute SQL

The graph only orchestrates. The `research` node calls
`ResearchService.execute(request)` and nothing else; persecution and
Rosfinmonitoring semantics stay in one place (ADR 0008, `CandidateQueryService`).
The one additional database read — the latest imported snapshot — sits behind the
`RosfinmonitoringSnapshotLookup` port with a PostgreSQL implementation outside
the graph module.

### Why Together AI is hidden behind an abstraction

Nodes depend on `ResearchRequestParser`; the parser depends on
`StructuredLlmClient`. Tests use fakes and never need network, credentials or a
database (except explicit integration tests). The provider or model can change
through configuration or a new client class without touching the graph, and the
business logic never refers to a model name.

### Why ResearchService does not depend on the LLM

`ResearchService` receives a validated `ResearchRequest` exactly as the CLI
`research` command and `POST /research` do. The LLM output is never trusted
directly:

1. the provider response must be a JSON object (else `llm_invalid_output`);
2. it must match the intake envelope (`ResearchIntake`);
3. `resolve_snapshot` deterministically fills `snapshot_id` when a
   Rosfinmonitoring status is requested without one — the latest snapshot that
   has imported entries — and records a warning with its id and date. A
   `snapshot_id` that does not literally appear in the user's query is treated as
   invented and ignored (warning);
4. `ResearchRequest.model_validate` applies the part-1 rules. Violations of our
   own domain rules (`value_error`, e.g. `date_from > date_to`) become a
   clarification; any other schema error (unknown field, wrong enum) means the
   LLM broke the contract and the workflow fails.

### Why facts and provenance are not created by the LLM

The LLM sees only the user's query, never data. `assemble_response` copies
`PersonResearchResult` objects from `ResearchResponse` unchanged — persecution
status, Rosfinmonitoring status, events, evidence spans, sources, per-person
warnings and `review_required`. There is no LLM-written summary in this part, so
AMBIGUOUS cannot become "absent from the list" and UNCERTAIN cannot become
"politically persecuted".

### Unsupported criteria and ambiguity

Criteria that `ResearchRequest` cannot express (occupation, age, region, …) are
returned by intake as `unsupported_criteria`. The workflow then asks for
clarification and does **not** run a partial search: a broader list could be
mistaken for the answer to the original question. Incomplete names ("найди
Иванова") and broad queries are executed; clarification is reserved for queries
that cannot be mapped or are contradictory.

### Failures are not empty results

`ResearchQueryResult.status` is `completed`, `clarification_required` or
`failed`. Provider failures carry `error.code`; the API maps them to 502/503/504
(409 when no snapshot is imported), the CLI to exit code 2 (clarification: 3).
"0 matches" is `completed` with `total_matched = 0`.

### Why no autonomous agent loop yet

The task is a single translation followed by deterministic execution. A planner
or reflection loop would add non-determinism, cost and failure modes without
improving the answer, and would blur where facts come from. The graph is a fixed
DAG with one LLM call.

## Consequences

- New dependency: `langgraph` (brings `langchain-core`). No vector store,
  embeddings, Dagster or MCP.
- Structured logging (logger `research_workflow`): `workflow_started`
  (query length only), `request_parsed`, `llm_intake_completed` (token usage),
  `request_validation`, `snapshot_resolved`, `research_executed`,
  `clarification_required`, `workflow_failed`, `workflow_finished`. No query
  text, prompts or credentials.
- Single-turn only: clarification is returned, not persisted as a conversation.
- Intake quality depends on the configured model and its JSON-schema support;
  it is verified only by the opt-in live test (`TOGETHER_LIVE_TESTS=1`).
- A report agent (LLM-written, provenance-aware narrative) can be added later as
  a node after `assemble_response` that consumes `ResearchQueryResult` without
  changing the result it cites.
