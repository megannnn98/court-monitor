# ADR 0009: LangGraph Research Orchestration

## Status

Accepted. The final `assemble_response` step is replaced by planning, result
evaluation, report and human review gate nodes in
[ADR 0010](0010-research-report-review-routing.md).

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
                                                  ├─ several explicit snapshots → clarification
                                                  └─ validate_request ─┬─ LLM broke schema → workflow_failed
                                                                       ├─ unknown field / domain rule / bounds → clarification
                                                                       └─ research ─┬─ unknown snapshot → clarification
                                                                                    └─ assemble_response → END

Any unexpected exception → ResearchQueryResult(failed, workflow_unexpected_error).
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
3. `resolve_snapshot` decides `snapshot_id` deterministically; the LLM value is
   never trusted on its own. `extract_explicit_snapshot_ids()` finds only numbers
   attached to snapshot/snapshot_id/снапшот (`snapshot 3`, `snapshot #3`,
   `snapshot_id=3`, `снапшот №3`); a bare number ("3 человека") is not a
   reference. One explicit reference is used (warning if the LLM differs);
   several different references ask for clarification; none means the LLM value
   is dropped (warning) and, if a Rosfinmonitoring status is requested, the
   latest snapshot that has imported entries is used, with a warning naming its
   id and date;
4. `ResearchRequest.model_validate` applies the part-1 rules and errors are
   classified by Pydantic error type: an unknown field (`extra_forbidden`)
   becomes an unsupported criterion and a clarification; our domain rules
   (`value_error`) and bounds/length errors (`limit` > 1000, empty
   `event_types`) become a clarification; any other type (enum, literal,
   parsing, type, missing) means the LLM broke the contract and the workflow
   fails — this wins over fixable errors.

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
Any other exception during the workflow (e.g. a database error) is returned as
`failed` / `workflow_unexpected_error` (HTTP 500) rather than crashing; its log
line carries only the exception type, because exception text such as
SQLAlchemy `[parameters: ...]` can contain user criteria. The traceback is logged
separately at DEBUG. "0 matches" is `completed` with `total_matched = 0`.

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
