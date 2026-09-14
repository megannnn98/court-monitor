# Court-Monitor Implementation Status

Full pipeline — sources → ingestion → extraction → person resolution →
persecution classification → Rosfinmonitoring matching → candidate query —
plus a deterministic research layer, a LangGraph natural-language research
workflow and deterministic research reports with human review policy and
source routing is implemented end-to-end (API, CLI).

## Last verified

Branch `feature-semantic-hybrid-retrieval` (part 4), 2026-09-14, Python 3.13.

| Check | Command | Result |
|---|---|---|
| Ruff | `uv run ruff check src tests` / `uv run ruff format --check src tests` | clean |
| mypy | `uv run mypy --strict src tests` (without the `semantic` group) | no issues (179 files) |
| Tests without services | `uv sync --frozen && uv run pytest` | 513 passed, 149 skipped |
| PostgreSQL + Qdrant | `TEST_DATABASE_URL=…/court_monitor_test QDRANT_TEST_URL=http://127.0.0.1:6333 uv run pytest` | 657 passed, 5 skipped |
| + real models | `uv sync --frozen --group semantic`, `SEMANTIC_MODEL_TESTS=1` (CUDA) | 659 passed, 3 skipped |

Skipped in the last run: the three opt-in live Together AI tests
(`TOGETHER_LIVE_TESTS=1`), not executed. CI (`.github/workflows/ci.yml`) runs
the commands without the `semantic` group and without model tests; it has not
run on GitHub for this branch (no push).

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
- `RuleBasedPersonResolver`: **exact deterministic `matching_key` baseline**
  — not fuzzy, not ML (see `docs/adr/0005-entity-resolution-strategy.md`)
- `uq_persons_matching_key_active` (partial unique index) prevents duplicate
  canonical persons from a concurrent-resolution race; `resolve_and_create`
  backs off to the winner on conflict instead of raising or duplicating
- Person ↔ mention/event linking integrated into the extraction pipeline
  (`extraction_resolution_service.py`)
- Merge with audit trail (`PersonMergeRecord`)

### 4. Political Persecution Classification
- `RuleBasedPersecutionClassifier`: rule-based baseline (legal article
  codes, keyword signals for human-rights/journalism/anti-war/religious/
  LGBT persecution)
- Evidence is **scoped to the specific person**: only their own
  mentions/events plus a window around each (`EVIDENCE_WINDOW_CHARS`,
  `persecution_classification_service.py`) — not the whole article
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
- Matching evaluation framework (`rosfin_match_evaluation.py`)

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
- Database-first `ResearchPlanner` over `source_registry` capabilities; refresh
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
- Retrieval score is never a domain confidence; report shows mode and rank only
- Evaluation (18 persons, 11 cases, 5 semantic-only), k=5: lexical nDCG 0.408,
  dense 0.823, hybrid 0.851, hybrid_reranked 0.650 — reranker off by default
- CLI: `rebuild-semantic-index`, `semantic-search`, `evaluate-retrieval`

### 11. API Layer
- FastAPI endpoints: persons, aliases, persecution, candidates,
  Rosfinmonitoring snapshots/entries, reviews, `POST /research`,
  `POST /research/query` (with `plan` and `report`), `POST /research/reviews`,
  health check

### 12. CLI
- `ingest`, `discover-and-ingest`, `search`, `evaluate-search`,
  `extract-entities`, `evaluate-extraction`, `resolve-people`,
  `classify-persecution`, `match-rosfinmonitoring`, `list-candidates`,
  `research`, `ask` (report by default, `--show-request`, `--show-plan`, `--raw`),
  `rebuild-semantic-index`, `semantic-search`, `evaluate-retrieval`

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

- Entity resolution is exact `matching_key` matching only — no fuzzy
  matching (typos, transliteration), no ML (see ADR 0005).
- `PersonRecord` has no birth date field; the matcher's birth date signal is
  always `None` in real pipeline runs today.
- Persecution classification is keyword/rule-based, not NLP.
- Rosfinmonitoring name-word retrieval uses `ILIKE` substring search per word,
  not an index.
- API has no authentication/authorization and no rate limiting.
- No automatic re-classification/re-matching when new articles arrive.
- Research: no region/city, court, organization or occupation filters (not
  linked to persons); only active persons; no consistent read across the
  repository calls of one request (TODO in ADR 0008).
- Semantic retrieval: no relevance threshold (pool = top-N nearest persons);
  index is refreshed manually (`rebuild-semantic-index --incremental`); the
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

- Fuzzy/ML entity resolution (similarity is never used to merge persons);
  article/chunk-level embeddings
- LLM-based classification or LLM-written facts/reports; an autonomous agent
  loop (the LLM is limited to request intake)
- Automatic ingestion from source routing, automated monitoring
- New UI, new ingestion sources
- Full Clean Architecture restructuring of `src/`
