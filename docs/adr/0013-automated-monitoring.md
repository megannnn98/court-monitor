# ADR 0013: Automated monitoring

## Status

Accepted, 2026-09-14. Builds on ADR 0004–0012; changes no domain decision.

## Context

Every part of the pipeline exists as a service and a CLI command (sources,
extraction, ER v2, persecution classification, Rosfinmonitoring matching,
semantic indexing, candidate query), but it only runs when a person types the
commands in the right order. The product question — "did a new person appear
who is politically persecuted and absent from the Rosfinmonitoring list?" —
needs the pipeline to run on a schedule, repeatably and observably, without a
human operator and without duplicating or losing domain data when a run is
interrupted or executed twice.

## Decision

### Orchestrator coordinates, domain services decide

A monitoring layer (`src/monitoring/`) sequences the existing services. It
makes no identity, persecution, RF or relevance decision itself:

```text
Dagster schedule / manual CLI
        │
        ▼
MonitoringService (src/monitoring/service.py)      ← same code for CLI and Dagster
  discover → ingest → extract → resolve (ER v2)    ← source stages, per source
  classify → RF match → semantic index → findings  ← derived stages, global
        │
        ▼
existing services: SourceAdapter, IngestionPipeline, ExtractionPipeline,
ExtractionResolutionService, PersecutionClassificationService,
RuleBasedRosfinmonitoringMatcher, SemanticIndexer, CandidateQueryService
```

Dagster (`src/monitoring/dagster/`) is a thin shell: one asset per stage, each
calling one `MonitoringService` method and publishing its counters as asset
metadata. The CLI (`monitor`, `monitor-derived`, `monitoring-status`,
`monitoring-findings`) calls the same service, so there is exactly one
monitoring implementation. The object graph is built in one composition root,
`application.build_application_services()`, used by the CLI monitoring
commands, the monitoring API endpoints and the Dagster resource.

### Why Dagster

- Asset graph, run history, per-asset metadata and schedules out of the box;
  the UI answers "what ran, what did it produce, what failed" without custom
  dashboards.
- In-process test execution (`execute_in_process`) — no daemon in tests.
- Retry policies and run-queue tag concurrency are declarative.
- Runs on the existing PostgreSQL; no new broker.

Alternatives: cron + CLI (no history, no metadata, overlap handling by hand),
Celery (a broker and workers for what is a sequential batch), Airflow (heavier
deployment, task-centric rather than data-centric).

### PostgreSQL is the source of truth; stages select their own work

No stage trusts IDs handed over in memory by a previous stage. Each stage
selects the work that is still missing from PostgreSQL
(`src/monitoring/selection.py`):

| Stage | Work selected |
|---|---|
| discovery / ingestion | discovered references whose `(source, external_id)` is not stored |
| extraction | articles of the source without an extraction run for the current extractor/normalizer versions |
| resolution | succeeded runs with an unlinked person mention lacking an ER v2 decision |
| classification | active persons without a classification for the current classifier version, or whose evidence changed after it |
| RF matching | active persons without a match for the latest imported snapshot, or whose evidence changed after it |
| semantic indexing | persons/events with a missing, unindexed or outdated semantic document; documents of vanished/inactive entities |

"Evidence changed" is the newest of: person created/updated, event linked,
alias added, ER decision recorded or reviewed for the person. So a person
created by a human ER review is classified, matched and indexed by the next
run without re-ingesting anything.

Evidence timestamps are transaction start times or application clocks, not
commit times: a classification computed while another run's ER transaction is
still open can be written after that transaction's `now()` without seeing its
rows. A result therefore counts as fresh only when written at least
`EVIDENCE_SETTLE_INTERVAL` (10 minutes, longer than any ER/review transaction
plus clock skew) after the last evidence change; recently changed persons are
recomputed once more, idempotently, by a later run.

This is what makes monitoring **at-least-once safe**: any asset, job or whole
run can execute again; finished work is not selected, unfinished work is, and
every domain write is already idempotent (unique constraints and upserts from
ADR 0004–0012). A crash between stages needs no manual cleanup.

### Transactions

Each unit of work keeps the owning service's own transaction (one document,
one extraction run, one person). Monitoring bookkeeping (counters, items,
stage metrics) are separate short transactions. No transaction spans a stage
or a run, so one failure never rolls back hours of work.

### Runs, items, checkpoints

- `monitoring_runs`: status (`running`, `completed`, `completed_with_errors`,
  `failed`, `aborted`), trigger, parameters, counters, per-stage metrics JSON,
  `rf_snapshot_id`, `heartbeat_at`.
- `monitoring_run_items`: one row per failed object — stage, entity type/id or
  external reference, error type, message (SQL parameters stripped),
  `failure_kind`.
- `source_monitoring_state`: per-source checkpoint (`last_successful_run_*`,
  `last_discovered_*`). HTML listings have no reliable upstream cursor, so
  `last_external_marker` stays empty and deduplication is by source document
  identity, not by wall clock. Only regular (scheduled/manual) runs that did not
  fail move the checkpoint; backfill, derived and dry runs never do.

### Concurrency

- **Same source:** a partial unique index allows one `running` row per scope
  (`source:<name>`, `derived`). A second start raises
  `MonitoringAlreadyRunningError`; the CLI reports `already_running`, the Dagster
  job skips its stages. This works across processes and hosts. Dagster's queued
  run coordinator additionally limits one run per `monitoring/source` tag.
- **Different sources** run in parallel. Their source stages touch disjoint
  documents; ER v2 identity-block locks already serialize person creation.
- **Derived stages** are global; each holds a session-level advisory lock
  (`monitoring:derived:<stage>`) so two runs never race on the same upserts.
- **Stale runs:** each counter/item write refreshes `heartbeat_at`. A new run
  first aborts runs without a heartbeat for `MONITORING_STALE_RUN_AFTER_MINUTES`
  (default 120), which releases the scope after a crash.

### Failures and retries

`classify_failure` decides retryability by exception type only:
transient fetch/discovery errors, persistence and connection errors, and
`RetrievalUnavailableError` are `retryable`; everything else (parse errors,
permanent HTTP errors, an embedding-model mismatch, extraction validation
failures) is `non_retryable`.

- An item failure is recorded and the stage continues → `completed_with_errors`.
- A stage-level failure (e.g. discovery after the source layer's own retries)
  fails the run.
- Domain outcomes are not failures: ER `REVIEW` is counted
  (`person_reviews_created`), a missing RF snapshot is reported
  (`no_rf_snapshot`), a deterministic extraction failure is stored once and not
  retried every run.
- Retries: the source layer keeps its own HTTP retries; the source job has no
  Dagster retry policy on top (no retry explosion). Only `monitoring_derived_job`
  has a Dagster `RetryPolicy` (3 retries, exponential backoff), raised when a
  derived run ends with retryable item failures — safe because it re-selects
  only still-stale work.

### Qdrant is a derived index

Semantic indexing runs after all PostgreSQL stages. If Qdrant is unavailable,
the stage records a retryable item failure and stops; the run ends
`completed_with_errors` and nothing in PostgreSQL is rolled back. The documents
stay unindexed and are selected again by the next run or by
`monitor-derived` / `monitoring_derived_job`. Without `QDRANT_URL` the stage
reports `not_configured`.

### Rosfinmonitoring

Matching always uses the latest imported snapshot
(`SqlAlchemyRosfinmonitoringSnapshotLookup`, same semantics as research). No
snapshot → no matching, no `not_matched` row, findings evaluation skipped with
`no_rf_snapshot`. Monitoring does not download RF lists.

### Findings

`monitoring_findings` records that a person satisfied a monitoring criterion —
an actionable monitoring result, not a Person status. Criteria come from a
`MonitoringQueryProvider`; the MVP provider has one criterion,
`political_persecution_not_in_rf` / `enbv-v1`: latest classification
`political` (confidence ≥ 0.7) and RF status `not_matched` for the latest
snapshot. Candidates are selected by the existing `CandidateQueryService`, so
`ambiguous`, `needs_review`, `insufficient_data` and never-matched persons are
never reported as absent.

- Dedup: unique `(finding_type, person_id, criteria_version)`; a repeated run
  only updates `last_seen_*`.
- History: `first_seen_run_id`/`first_seen_at` never change. A person who no
  longer satisfies the criterion is set `active = false` with `inactive_since`;
  nothing is deleted. A criterion change is a new `criteria_version`.
- Provenance: `persecution_classification_id`, `rosfin_match_id`,
  `snapshot_id`, runs; evidence and source documents are reachable through the
  person (research API).
- Lifecycle column `status` (`open`/`acknowledged`/`resolved`) exists; this stage
  only creates `open` findings. External notifications are out of scope.

### Scheduling

One Dagster schedule per `MONITORING_ENABLED_SOURCES` entry with
`MONITORING_CRON` (default hourly), starting `STOPPED` so a local
`dagster dev` never scrapes on its own. Regular runs discover at most
`MONITORING_DISCOVERY_LIMIT` (default 50) references; full history is an
explicit `monitor --backfill --source … --limit …`.

### Deployment

Compose profile `monitoring`: `dagster-webserver`, `dagster-daemon` and a
one-shot `dagster-db-init`. Dagster's own tables live in a separate database
(`DAGSTER_PG_DB`, default `court_monitor_dagster`) in the same PostgreSQL
server — never in the domain database. The monitoring job uses the in-process
executor with an in-memory IO manager: stage outputs are per-run handles that
must never be shared between concurrent runs of different sources.

## Consequences

- One command (`monitor`) or one schedule runs the whole pipeline; reruns are
  harmless; every run is inspectable by CLI, API and Dagster UI.
- Selecting materializations of single assets in the Dagster UI is not
  supported (in-memory IO); stage isolation is provided by
  `monitoring_derived_job` / `monitor-derived` instead.
- Known limitations:
  - Updated upstream documents are not detected by regular runs (a known
    `external_id` is not fetched again). `--backfill --refetch-known` refetches;
    extraction then creates a run for the new content hash next to the old one
    (no document versioning in this stage).
  - Person mentions whose name cannot be turned into an ER identity have no
    decision and are re-checked (cheaply, without effect) by each resolution
    stage.
  - `person_reviews_created` can over-count when a run re-resolves an
    extraction run that mixes an already pending review with an undecided
    mention (the pending one is counted again).
  - A person whose evidence changed within the last 10 minutes is
    reclassified/re-matched/re-rendered by the next run(s) even without new
    evidence (settle interval); no rows are duplicated, the counters show it.
  - An RF match that failed for a person makes the candidate query skip that
    person, which deactivates an existing finding until a later run matches
    them again (history is kept).
  - The first run on an existing database classifies/matches/indexes every
    person without a current result (one-off catch-up).
  - Classification and candidate queries are per person (existing N+1); fine
    for hourly batches of this size.
