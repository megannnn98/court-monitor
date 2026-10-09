# ADR 0023: A reusable core for monitoring addressable text sources

## Status

Accepted, 2026-10-09. Implemented in PR #42 (`refactor/monitor-core-foundation`).

## Context

Fetching, parsing and persisting publications, talking to OpenAI-compatible models
and retrying transient failures were generic mechanics, but they lived inside
application packages next to court-specific code. The CLI and `MonitoringService`
each had their own fetch → parse → persist path, two clients built the same JSON-chat
request by hand, and two retry loops were written out separately. Nothing stopped
domain types from leaking into the generic parts, and there was no way to tell
whether the mechanics could serve a second monitor.

## Decision

Extract `src/monitor_core`: a content-monitoring core, not a general framework. It
contains:

- ingestion models, ports and orchestration: `SourceReference`, `RawDocument`,
  `ParsedArticle`, `DocumentFetcher`, `SourceAdapter`, `ArticleParser`,
  `IngestionPersistence`, `IngestionPipeline`, `SourceIngestion`,
  `RetryingDocumentFetcher` and the ingestion errors;
- the OpenAI-compatible JSON-chat wire protocol (`post_json_chat`,
  `read_chat_completion`), without any provider policy;
- generic sync and async retry, where the caller decides what to retry, how long to
  wait and how to sleep.

The rules:

- The core never imports an application package. Code outside the core imports it
  only through its public packages (`model`, `ports`, `ingestion`, `llm`, `retry`,
  `errors`), each with an explicit `__all__`. Architecture tests enforce both rules.
- The core does not know what a store returns. Persistence is generic over its
  result: `IngestionPersistence[R]`, `IngestionPipeline[R]`, `IngestionResult[R]`.
  Court Monitor returns `SqlAlchemyPersistenceResult(document_id, article_id)`.
- Policy stays with the caller. `MonitoringService` uses `IngestionPipeline.read()`
  and `save()` and applies its own date window, skip and failure rules between them.
  Each LLM client keeps its own error types, messages, accepted `finish_reason`s,
  usage and cost handling.
- Behaviour must not change. Every move was preceded by characterization tests,
  checked by mutation.

Court-specific code remains outside the core: extraction prompts and schemas, entity
resolution, Rosfinmonitoring, legal taxonomy, search, the database schema, Dagster and
application orchestration.

## Consequences

- The CLI and automated monitoring share one ingestion path. A new source implements
  the ports and changes nothing in the core.
- A test-only second consumer (company news, no Court Monitor imports) is built on the
  public API. It found that the core required Court Monitor's two IDs, which led to the
  generic persistence result.
- Some code was deliberately not generalized, because it did not fit the abstraction
  without bending it: the `decision_screen` retry loop (adapting its exception
  chaining took more code than the loop itself), discovery pagination retry (statuses
  as values, a special 404), the Anthropic SDK path and the CLI reviewer.
- Known limits, deferred: sources must be addressable and textual; `ParsedArticle` is
  narrower than a generic document; the LLM transport is synchronous; `temperature=0`
  is fixed in the transport unless overridden; `CHAT_ENVELOPE_ERRORS` exposes
  built-in exceptions.

Practical guide: [docs/wiki/Monitor-Core.md](../wiki/Monitor-Core.md).
