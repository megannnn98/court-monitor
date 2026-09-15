# Court-Monitor Implementation Status

Full pipeline — sources → ingestion → extraction → person resolution →
persecution classification → Rosfinmonitoring matching → candidate query —
plus a deterministic research layer, a LangGraph natural-language research
workflow and deterministic research reports with human review policy and
source routing is implemented end-to-end (API, CLI).

## Last verified

Branch `feature-automated-monitoring`, 2026-09-14, Python 3.13.

| Check | Command | Result |
|---|---|---|
| Dependencies | `uv sync --frozen` | ok (Dagster 1.13.22, dagster-postgres, dagster-webserver) |
| Ruff | `uv run ruff check src tests` / `uv run ruff format --check src tests` | clean |
| mypy | `uv run mypy --strict src tests` | no issues (239 files) |
| Tests without services | `uv run pytest` | 630 passed, 243 skipped |
| PostgreSQL + Qdrant | `TEST_DATABASE_URL=…/court_monitor_test QDRANT_TEST_URL=http://127.0.0.1:6333 uv run pytest` | 867 passed, 6 skipped |
| Migration | `alembic upgrade head` / `downgrade o9p0q1r2s3t4` / `upgrade head` on `court_monitor_test` | ok (also covered by `tests/db/test_monitoring_tables_migration.py`) |
| Dagster | `dagster definitions validate -m monitoring.dagster.definitions`; in-process job tests | ok |
| Compose `monitoring` profile | `docker compose --profile monitoring up -d --build`; `dagster job launch -j monitoring_derived_job` | webserver + daemon up, Dagster run SUCCESS, monitoring run `completed`, Dagster tables only in `court_monitor_dagster` |
| Live smoke | `monitor --source ovd-info --dry-run --limit 2` | 2 discovered, 1 new, nothing written |

The dev database `court_monitor` was migrated to `p0q1r2s3t4u5` for the compose
check. No live (non-dry-run) ingestion was executed. The real-model semantic
test group (`SEMANTIC_MODEL_TESTS=1`) and the opt-in live Together AI tests were
not run. CI has not run on GitHub for this branch (no push).

Source layout: `src/` is split into packages (`db`, `sources`, `extraction`,
`persons`, `persecution`, `rosfinmonitoring`, `candidates`, `search`, `llm`,
`research`, `semantic_retrieval`); `main.py` and `api.py` stay in `src/`.
`tests/` mirrors it, with shared fakes in `tests/support/`.

## Completed Components ✅

### 1. Source Layer (Steps 1-9)
- Multi-source ingestion (ОВД-Инфо, SOTA)
- Article parsing and persistence
- PostgreSQL lexical search
- Search evaluation framework

### 2. Extraction Pipeline (Step 10)
- Deterministic rule-based entity extraction: person, organization, court,
  location, legal reference
- Event extraction (detention, arrest, charge, sentence, etc.)
- Normalization with typed data models
- Offset validation and provenance tracking
- Extraction persistence: transactional, idempotent per
  `(article_id, content_hash, extractor_version, normalizer_version)`
- Extraction evaluation with golden corpus (see `docs/wiki/Extraction.md`)

### 3. Canonical Person Model + Entity Resolution
- Person domain model with `canonical_name`, `normalized_name`, `matching_key`
- PersonAlias with origin tracking (extraction/manual/resolution/merge)
- Entity Resolution v2 (ADR 0012, `src/persons/resolution/`):
  `PersonNameNormalizer` (all admissible ФИО orders, initials, `ё/е`; suffixes
  are hints only), candidate generation (exact `matching_key` returning every
  active namesake, alias key, pg_trgm GIN, opt-in
  semantic with its own threshold), per-component features with explicit
  conflicts (RapidFuzz), rule-based `resolution_score` (not a probability),
  decision policy AUTO_LINK / REVIEW / CREATE_NEW with top1−top2 margin;
  never merges existing persons automatically
- `matching_key` is a candidate lookup key, not an identity key (ADR 0012
  amendment, migration `o9p0q1r2s3t4`): `uq_persons_matching_key_active` replaced
  by the non-unique partial `ix_persons_matching_key_active`; no exact fast path
  and no `RuleBasedPersonResolver`; several active persons with the incoming key
  → REVIEW (`multiple_exact_name_matches`); reviewers can create a same-name
  person and record keep-separate (`distinct_from_person_id`)
- Provenance in `person_resolution_decisions` (`resolver_version = er-v2`);
  REVIEW keeps the mention unlinked with a pending `person_resolution` review;
  reviewer actions link / create (namesakes allowed) / merge (audited, row
  locks) / keep separate (CLI + API);
  alias promotion only for clean full forms
- Advisory locks on order-independent identity blocks, taken before candidates
  are read (the only guard against concurrent duplicates); idempotent re-runs
- `evaluate-er`: 59-case corpus with namesake cases, candidate recall@k per
  generator, auto-link precision 1.00 / recall 0.58, 0 false links (0 on
  namesakes), 1 indistinguishable namesake link reported apart, 0 false
  create-new, review rate 0.43 at the calibrated defaults
- Person ↔ mention/event linking integrated into the extraction pipeline
  (`extraction/resolution_service.py`)
- Merge with audit trail (`PersonMergeRecord`)

### 4. Political Persecution Classification
- `RuleBasedPersecutionClassifier`: rule-based baseline (legal article
  codes, keyword signals for human-rights/journalism/anti-war/religious/
  LGBT persecution)
- Evidence is **scoped to the specific person**: only their own
  mentions/events plus a window around each (`EVIDENCE_WINDOW_CHARS`,
  `persecution/classification_service.py`) — not the whole article
- `PersecutionClassificationStatus.UNCERTAIN` is reachable: a single weak
  keyword-only signal is UNCERTAIN, not an automatic POLITICAL
- A person's classification is the **latest** one (`classified_at` desc, then
  `id` desc; `persecution_queries.latest_persecution_classification_ids()`),
  used by the candidate query, research and `GET /persons/{id}/persecution`

### 5. Rosfinmonitoring Integration
- Snapshot ingestion pipeline + entry normalization
- `RuleBasedRosfinmonitoringMatcher`: matching_key/name-word retrieval +
  Jaccard name similarity, plus an optional (currently always-`None`) birth
  date signal with conservative rules
- Match statuses: `MATCHED`, `NOT_MATCHED`, `AMBIGUOUS`, `NEEDS_REVIEW`,
  `INSUFFICIENT_DATA`; no match record for a snapshot is reported as
  `NO_MATCH_RECORD`
- `NOT_MATCHED` reports `NOT_MATCHED_CONFIDENCE` (0.8), not 1.0
- Matching evaluation framework (`rosfinmonitoring/match_evaluation.py`)

### 6. Main Product Query
- `CandidateQueryService.get_candidates`: persons whose latest classification
  is POLITICAL (≥ `DEFAULT_MIN_PERSECUTION_CONFIDENCE` = 0.7) and who are
  **confirmed absent** from a Rosfinmonitoring snapshot
- Only `NOT_MATCHED` counts as "absent" — `NO_MATCH_RECORD`, `AMBIGUOUS`,
  `NEEDS_REVIEW` and `INSUFFICIENT_DATA` are never a confirmed absence.
  `include_rf_statuses` lets a caller opt into a broader view explicitly.

### 7. Research Domain (ADR 0008)
Deterministic, typed research over canonical persons:

```text
ResearchRequest
PersonResearchCriteria
ResearchService
ResearchResponse
PersonResearchResult
evidence/provenance
```

- `ResearchRequest` (`object_type`, `criteria`, `limit`) with strict
  `PersonResearchCriteria` (person_id, name, persecution status/threshold,
  Rosfinmonitoring status + snapshot, event types, date range, source);
  unknown criteria are rejected
- `ResearchService.execute()` reuses `CandidateQueryService` for POLITICAL +
  Rosfinmonitoring status; no second definition of "not in the list"
- `PersonResearchResult`: person, aliases, latest persecution classification,
  Rosfinmonitoring status for the snapshot, linked events, person-scoped
  evidence spans (offsets + span text), sources, warnings, computed
  `review_required`
- Adapters: CLI `research`, `POST /research`

### 8. LangGraph Research Workflow (ADR 0009)

```text
Natural language
→ Together AI
→ ResearchRequest
→ snapshot resolution
→ validation
→ ResearchService
→ ResearchQueryResult
```

```text
LLM interprets intent.
Domain services determine facts.
```

- LLM (Together AI over `httpx`, JSON schema structured output) is used
  **only** for request intake; it never sees data and never writes results
- Deterministic snapshot resolution: only explicit references
  (`snapshot #3`, `снапшот №3`) count; otherwise the latest imported snapshot
  with a warning; several references → clarification
- Validation via `ResearchRequest.model_validate`: unsupported fields and
  user-fixable values → `clarification_required` (no partial search); broken
  LLM contract → `failed` / `llm_invalid_output`
- Provider errors and unexpected exceptions are structured failures
  (`status=failed`, error code), never an empty result
- `ResearchQueryResult` carries `PersonResearchResult` objects unchanged
- Adapters: CLI `ask` (`--show-request`, exit 2 failed / 3 clarification),
  `POST /research/query`
- Configuration: `TOGETHER_API_KEY`, `TOGETHER_MODEL`,
  `TOGETHER_TIMEOUT_SECONDS`

### 9. Research Reports, Review Policy, Source Routing (ADR 0010)

```text
ResearchRequest → ResearchPlanner.plan → ResearchService → ResearchResponse
→ ResearchResultEvaluator (ResearchReviewPolicy + routing)
→ ResearchReportBuilder → human_review_gate → ResearchReport
```

- Graph: `… validate_request → build_research_plan → research →
  evaluate_result → build_report → human_review_gate`
- `ResearchReport` next to the raw `results` (not instead of them): items with
  copied statuses/confidences, `why_matched` from applied criteria only, claims
  (`identity`, `persecution_classification`, `rosfinmonitoring_status`,
  `event`) with citations built from existing `ResearchEvidence` +
  `ResearchSource`; report status `complete` / `partial` / `review_required` /
  `no_matches` / `insufficient_data`
- Only `NOT_MATCHED` is worded as absence; `NO_MATCH_RECORD` and
  `INSUFFICIENT_DATA` explicitly say absence is not confirmed
- `ResearchReviewPolicy` (single place): `persecution_uncertain`,
  `persecution_needs_review`, `rosfin_ambiguous`, `rosfin_needs_review`,
  `rosfin_insufficient_data`, `missing_evidence`, `low_confidence`
- Review record only by explicit `POST /research/reviews`: re-checks the
  condition, reuses `review_records`, idempotent via partial unique index
  `uq_review_records_pending_subject`
- Database-first `ResearchPlanner` over `sources.source_registry` capabilities; refresh
  is a recommendation after an empty result, ingestion is never started
- No LLM after intake

### 10. Semantic Hybrid Entity Retrieval (ADR 0011)

```text
semantic_query → ResearchPlanner (hybrid) → retrieve_candidates
→ lexical (semantic_documents tsvector) + dense (E5 → Qdrant) → RRF [→ cross-encoder]
→ candidate person ids → ResearchService (all criteria from PostgreSQL) → report
```

- Deterministic `PersonSemanticDocumentBuilder` / `EventSemanticDocumentBuilder`
  (names, aliases, classification, linked events with own spans; no articles)
- `semantic_documents` table (derived, content hash, representation version,
  indexed_at, GIN tsvector); Qdrant `persons_semantic`, `events_semantic` with
  uuid5 point ids and minimal payload; incremental `SemanticIndexer`
- `EntityRetriever` port: lexical, dense, hybrid (RRF k=60), reranked
  (opt-in `SEMANTIC_RERANK=1`)
- Structured requests never touch Qdrant; semantic failures are workflow
  failures (`semantic_retrieval_unavailable` / `_not_configured`, HTTP 503)
- Retrieval ranking and semantic relevance acceptance are separate:
  `accept_candidates` (`DenseSimilarityRelevancePolicy`) keeps only candidates
  with dense cosine ≥ `SEMANTIC_DENSE_MIN_SCORE` (0.80, calibrated for
  multilingual-e5-base; required for other models); RRF/lexical/reranker scores
  never accept; all rejected → completed with 0 results (`candidate_person_ids=[]`,
  never an unrestricted search); Qdrant points carry `embedding_model_id`
- Retrieval score is never a domain confidence; report shows mode and rank only
- Evaluation (18 persons, 11 cases, 5 semantic-only), k=5: lexical nDCG 0.408,
  dense 0.823, hybrid 0.851, hybrid_reranked 0.650 — reranker off by default; acceptance
  sweep over 15 negative cases (0.80: negative rejection 14/15, recall 0.40)
- CLI: `rebuild-semantic-index`, `semantic-search`, `evaluate-retrieval`

### 15. Automated Monitoring (ADR 0013)
- `src/monitoring/`: `MonitoringService` stages over the existing services
  (discover → ingest → extract → ER v2 → classify → RF → semantic → findings);
  work selected from PostgreSQL (`selection.py`), so reruns and crash recovery
  never duplicate or skip domain rows
- Tables (migration `p0q1r2s3t4u5`): `monitoring_runs` (status, trigger,
  counters, stage metrics, heartbeat; one `running` per scope),
  `monitoring_run_items` (failed objects with `failure_kind`),
  `source_monitoring_state` (per-source checkpoint), `monitoring_findings`
  (dedup per type/person/criteria version, `first_seen_run_id`, `active`)
- Findings via `CandidateQueryService`: `political_persecution_not_in_rf` /
  `enbv-v1`; no RF snapshot → skipped; RF ambiguous is not absent
- Qdrant outage → retryable item, run `completed_with_errors`, PostgreSQL kept;
  `monitor-derived` / `monitoring_derived_job` (Dagster RetryPolicy) retries
- Dagster: asset graph, `monitoring_job`, `monitoring_derived_job`, one
  schedule per enabled source (`MONITORING_CRON`, starts STOPPED); compose
  profile `monitoring` with separate `court_monitor_dagster` database
- Composition root `application.build_application_services()`

### 16. Production-like Deployment (ADR 0014)
- One image `court-monitor:local` for API (uvicorn), Dagster and migrations;
  compose profiles `api`, `production` (PostgreSQL, Qdrant, API, Dagster) and
  one-shot `migrate` (`alembic upgrade head`, never run by API/Dagster)
- Container healthchecks: API `/health/live`, Dagster `/server_info`, Qdrant
  `/readyz`; `/health/ready` for dependency state (503 when DB down or schema
  not at head)
- Per-process pool settings passed to API and Dagster; `init`, graceful
  shutdown periods; all ports bound to `127.0.0.1`

### 11. API Layer
- FastAPI endpoints: persons, aliases, persecution, candidates,
  Rosfinmonitoring snapshots/entries, reviews, `POST /research`,
  `POST /research/query` (with `plan` and `report`), `POST /research/reviews`,
  `/person-resolution/reviews` (list, show, apply decision), read-only
  `/monitoring/status`, `/monitoring/runs`, `/monitoring/runs/{id}`,
  `/monitoring/findings`, health check

### 12. CLI
- `ingest`, `discover-and-ingest`, `search`, `evaluate-search`,
  `extract-entities`, `evaluate-extraction`, `resolve-people`,
  `classify-persecution`, `match-rosfinmonitoring`, `list-candidates`,
  `research`, `ask` (report by default, `--show-request`, `--show-plan`, `--raw`),
  `rebuild-semantic-index`, `semantic-search`, `evaluate-retrieval`,
  `resolve-person` (dry-run), `person-resolution-reviews`, `evaluate-er`,
  `monitor` (`--source`, `--dry-run`, `--backfill`), `monitor-derived`,
  `monitoring-status`, `monitoring-findings`

### 13. Manual Review Infrastructure
- Generic `review_records` table + `ManualReviewService` for ambiguous
  merges/matches/classifications pending human decision
- `get_or_create_pending_review`: at most one pending review per subject

### 14. CI
- GitHub Actions: `quality` (ruff, format, mypy), `tests` (pytest without
  database), `integration` (PostgreSQL 18 and Qdrant services, `alembic upgrade
  head`, pytest with `TEST_DATABASE_URL` and `QDRANT_TEST_URL`). Together AI is
  never called and no embedding model is downloaded in CI.

## Known Limitations

- Entity resolution v2: thresholds tuned on a small synthetic corpus; no
  transliteration, diminutives without an alias, phonetic or context features;
  the extraction normalizer mangles names (`Анна Новикова` → `Анн Новиков`) and
  ER compares those forms; a single existing person with the incoming full name
  is linked even if the mention is a different namesake (no context features);
  without a unique key, names sharing no full token can race into two persons;
  advisory locks include given-name tokens and are held for a
  whole extraction run; re-resolution under a new resolver version is not
  implemented; semantic index refresh after link/create is manual.
- `PersonRecord` has no birth date field; the matcher's birth date signal is
  always `None` in real pipeline runs today.
- Persecution classification is keyword/rule-based, not NLP.
- Rosfinmonitoring name-word retrieval uses `ILIKE` substring search per word,
  not an index.
- API has no authentication/authorization and no rate limiting; the
  production compose profile binds it to localhost only (ADR 0014). No TLS,
  secrets management, backups or multi-host deployment.
- Automated monitoring (ADR 0013): updated upstream documents are not detected
  by regular runs (`--backfill --refetch-known` refetches; no document
  versioning); single Dagster assets cannot be materialized alone (in-memory IO,
  use `monitoring_derived_job`); `person_reviews_created` can over-count a
  pending review on re-resolution; no external notifications.
- Research: no region/city, court, organization or occupation filters (not
  linked to persons); only active persons; no consistent read across the
  repository calls of one request (TODO in ADR 0008).
- Semantic relevance threshold calibrated on a small synthetic corpus: at 0.80
  relevant recall is 0.40 and a word-play negative («задержание кометы»)
  still leaks; semantic retrieval:
  index is refreshed incrementally by monitoring runs or manually
  (`rebuild-semantic-index --incremental`); the
  evaluation corpus is small and synthetic; reranker hurt quality on it.
- Reports: persecution reasons have no per-reason evidence spans (the
  classification claim cites all of the person's spans); source routing has no
  freshness policy and never runs ingestion; review tasks cannot be created for
  `missing_evidence` (no stored subject).
- Natural-language intake quality depends on the configured Together model;
  JSON-schema compatibility is only checked by the opt-in live test, which has
  not been run. Single-turn only (clarification is not a conversation).
- `.env.example` must list `POSTGRES_*`, `DATABASE_URL` and `TOGETHER_*` (see
  `docs/wiki/Setup.md`).

## Explicitly Out of Scope (for now)

- ML entity resolution; automatic merge of existing persons (reviewer action
  only); article/chunk-level embeddings
- LLM-based classification or LLM-written facts/reports; an autonomous agent
  loop (the LLM is limited to request intake)
- Automatic ingestion from source routing, automated monitoring
- New UI, new ingestion sources
- Full Clean Architecture restructuring of `src/`
