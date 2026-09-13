# Court-Monitor Implementation Status

Last verified against code: full pipeline (steps 1-10 + person resolution,
persecution classification, Rosfinmonitoring matching, main product query,
API, CLI) is implemented end-to-end. 257 tests passing (`uv run pytest -q`).

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
  `persecution_classification_service.py`) — not the whole article, so a
  different person's context in the same article doesn't leak onto them
- Legal-reference mentions near a person's event are wired into that
  event's `charge` evidence
- `PersecutionClassificationStatus.UNCERTAIN` is reachable: a single weak
  keyword-only signal (no political charge, no second corroborating
  signal) is UNCERTAIN, not an automatic POLITICAL

### 5. Rosfinmonitoring Integration
- Snapshot ingestion pipeline + entry normalization
- `RuleBasedRosfinmonitoringMatcher`: matching_key/name-word retrieval +
  Jaccard name similarity, plus an optional (currently always-`None`,
  since `PersonRecord` has no birth date yet) `person_birth_date` signal
  with conservative rules (matching known birth date → small boost;
  differing known birth date → capped well below auto-MATCHED)
- Match statuses: `MATCHED`, `NOT_MATCHED`, `AMBIGUOUS`, `NEEDS_REVIEW`,
  `INSUFFICIENT_DATA` (name too thin to search reliably — a bare surname
  finding zero candidates is not a confident NOT_MATCHED)
- `NOT_MATCHED` reports `NOT_MATCHED_CONFIDENCE` (0.8), not 1.0 — absence
  of evidence in a snapshot is not certainty of absence
- Matching evaluation framework (`rosfin_match_evaluation.py`)

### 6. Main Product Query
- `CandidateQueryService.get_candidates`: political persecution candidates
  **confirmed absent** from a Rosfinmonitoring snapshot
- By default only `NOT_MATCHED` counts as "absent" — `NO_MATCH_RECORD`
  (matching never run), `AMBIGUOUS`, `NEEDS_REVIEW` and
  `INSUFFICIENT_DATA` are excluded, since none of them are a confirmed
  absence. `include_rf_statuses` lets a caller opt into a broader
  manual-review view explicitly.

### 7. API Layer
- FastAPI read-only endpoints: persons, aliases, persecution, candidates,
  Rosfinmonitoring snapshots/entries, reviews, health check

### 8. CLI
- `extract-entities`, `resolve-people`, `classify-persecution`,
  `import-rosfinmonitoring`, `match-rosfinmonitoring`, `list-candidates`,
  `evaluate-extraction`, `evaluate-er`, `evaluate-persecution`,
  `evaluate-rosfin-match`

### 9. Manual Review Infrastructure
- Generic `review_records` table + `ManualReviewService` for ambiguous
  merges/matches/classifications pending human decision

## Known Limitations

- Entity resolution is exact `matching_key` matching only — no fuzzy
  matching (typos, transliteration), no ML. This is a deliberate baseline,
  not a gap to silently work around (see ADR 0005).
- `PersonRecord` has no birth date field — nothing in extraction/
  normalization currently produces one, so the matcher's
  `person_birth_date` signal is always `None` in real pipeline runs today.
  The parameter and its conservative rules exist so this is a one-line
  wiring change, not a redesign, once a birth-date source exists.
- Persecution classification is keyword/rule-based, not NLP — it can
  still miss phrasing or misfire on incidental keyword matches inside a
  person's own evidence window.
- Rosfinmonitoring name-word retrieval (for Jaccard similarity) uses
  `ILIKE` substring search per word, not an index — fine at current
  snapshot sizes, would need revisiting at much larger scale.
- API has no authentication/authorization and no rate limiting (read-only
  by design for now).
- No automatic re-classification/re-matching when new articles arrive for
  an already-classified person — it's an explicit CLI/API step.

## Explicitly Out of Scope (per project constraints)

- Fuzzy/ML entity resolution, embeddings, cross-encoder re-ranking
- LLM-based classification, NER models, GraphRAG
- New UI, new ingestion sources
- Full Clean Architecture restructuring of `src/`
