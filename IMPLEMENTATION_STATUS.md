# Court-Monitor Implementation Status

## Completed Components ✅

### 1. Source Layer (Steps 1-9)
- ✅ Multi-source ingestion (ОВД-Инфо, SOTA)
- ✅ Article parsing and persistence
- ✅ PostgreSQL lexical search
- ✅ Search evaluation framework
- ✅ 142 existing tests passing

### 2. Extraction Pipeline (Step 10)
- ✅ Deterministic rule-based entity extraction
- ✅ Person, organization, court, location, legal reference extraction
- ✅ Event extraction (detention, arrest, charge, sentence, etc.)
- ✅ Normalization with typed data models
- ✅ Offset validation and provenance tracking
- ✅ Extraction persistence with idempotency
- ✅ Extraction evaluation with golden corpus
- ✅ 23 extraction tests passing

### 3. Canonical Person Model (Foundation for Steps 11+)
- ✅ Person domain model with canonical_name, normalized_name, matching_key
- ✅ PersonAlias model with origin tracking (extraction/manual/resolution/merge)
- ✅ ResolutionResult with status tracking
- ✅ MergeRecord and ReviewRecord for audit trail
- ✅ ORM models: PersonRecord, PersonAliasRecord, PersonMergeRecord, ReviewRecordModel
- ✅ Alembic migration with proper indexes
- ✅ SqlAlchemyPersonPersistence: CRUD operations, merge functionality
- ✅ RuleBasedPersonResolver: matching_key-based resolution
- ✅ 23 new tests for person models, persistence, and resolution

**Total Tests: 165 passing**

## Remaining Components 🔧

### High Priority
1. **Integration Layer**
   - Connect extraction pipeline to person resolution
   - Link person mentions to canonical persons automatically
   - Link events to resolved persons

2. **Political Persecution Classification**
   - PersecutionClassifier Protocol
   - Rule-based baseline implementation
   - Classification evaluation framework

3. **Rosfinmonitoring Integration**
   - Snapshot ingestion pipeline
   - Entry normalization
   - Person ↔ Rosfinmonitoring matching
   - Matching evaluation

4. **Main Product Query**
   - Query: "political but absent from RF"
   - Result aggregation with provenance

### Medium Priority
5. **API Layer**
   - FastAPI read-only endpoints
   - Person, event, candidate queries

6. **CLI Extensions**
   - resolve-people
   - ingest-rosfinmonitoring
   - match-rosfinmonitoring
   - classify-persecution
   - list-candidates
   - show-person

7. **Evaluation Frameworks**
   - Entity resolution evaluation (pairwise F1)
   - Rosfinmonitoring matching evaluation
   - Political classification evaluation

8. **Documentation**
   - Wiki pages for new components
   - ADRs for key decisions
   - Updated README with full pipeline

## Architecture Decisions Made

1. **Canonical Person Model**
   - matching_key for fast lookups (normalized, lowercased, no spaces/punctuation)
   - Multiple aliases per person with origin tracking
   - Status: active/merged/needs_review

2. **Entity Resolution Strategy**
   - Deterministic baseline using matching_key
   - Extensible via PersonResolver Protocol
   - Future: can add ML-based resolution without changing interface

3. **Merge Strategy**
   - Transactional merge with audit trail
   - Preserves all aliases and mentions
   - Tracks merge history

## Next Steps (Priority Order)

1. Create PersonResolutionService that integrates extraction + resolution
2. Implement political persecution classifier
3. Add Rosfinmonitoring snapshot ingestion
4. Implement Person ↔ RF matching
5. Build main product query
6. Add FastAPI endpoints
7. Extend CLI
8. Create evaluation frameworks
9. End-to-end golden test
10. Documentation

## Known Limitations

- Rule-based entity resolution is simple (matching_key only)
- No fuzzy matching for names yet
- Political classification not yet implemented
- Rosfinmonitoring integration not yet started
- No API layer yet (CLI only)

## Technical Debt

- Need to integrate person resolution into extraction pipeline
- Need to add person_id to extraction results
- Need to link events to persons
