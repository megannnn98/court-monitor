# Court-Monitor: Implementation Report

## Executive Summary

This report documents the implementation of the foundational infrastructure for the court-monitor project's canonical person tracking, entity resolution, and person lifecycle management. The work establishes the critical foundation for the remaining pipeline components (political classification, Rosfinmonitoring integration, API layer).

## Work Completed

### 1. Domain Models (`src/person_models.py`)
- **Person**: Canonical person entity with `canonical_name`, `normalized_name`, `matching_key`
- **PersonAlias**: Multiple name variants per person with origin tracking (extraction/manual/resolution/merge)
- **ResolutionResult**: Entity resolution output with status (matched/new_person/ambiguous/rejected/needs_review)
- **ResolutionContext**: Contextual information for resolution (article_id, city, court, organization, event_types)
- **MergeRecord**: Audit trail for person merges with status tracking
- **ReviewRecord**: Manual review infrastructure for ambiguous cases
- All models use Pydantic with strict validation and enum-based statuses

### 2. ORM Models (`src/orm_models.py`)
Extended with 4 new tables:
- **PersonRecord**: Canonical persons with self-referential `merged_into_id` FK
- **PersonAliasRecord**: Aliases linked to persons and optionally to source mentions
- **PersonMergeRecord**: Merge history with source/target persons
- **ReviewRecordModel**: Generic review records for any subject type

All tables include proper indexes:
- `ix_persons_matching_key` for fast person lookups
- `ix_persons_status` for filtering active/merged persons
- `ix_person_aliases_person_id` and `ix_person_aliases_matching_key` for alias queries
- `ix_person_merges_target` for merge history
- `ix_review_records_subject` for review lookups

### 3. Database Migration (`migrations/versions/g1h2i3j4k5l6_add_person_and_resolution_tables.py`)
- Creates all 4 tables with proper constraints
- Foreign keys with CASCADE/SET NULL as appropriate
- Unique constraints to prevent duplicate aliases per person
- Indexes for performance
- Clean downgrade path

### 4. Persistence Layer (`src/person_persistence.py`)
`SqlAlchemyPersonPersistence` provides:
- `create_person()`: Create new canonical person
- `create_alias()`: Add alias to person (with duplicate prevention)
- `find_person_by_matching_key()`: Fast lookup by normalized key
- `merge_persons()`: Transactional merge with audit trail
- `list_aliases_for_person()`: Get all aliases for a person
- `get_person()`: Retrieve person by ID
- `list_active_persons()`: List non-merged persons

All operations are transactional using `session_factory.begin()`.

### 5. Entity Resolution (`src/person_resolver.py`)
`RuleBasedPersonResolver` implements:
- `resolve()`: Check if person exists by matching_key
- `resolve_and_create()`: Resolve or create new person with alias
- Matching key-based resolution (deterministic baseline)
- Automatic alias creation on match
- Duplicate alias prevention via IntegrityError handling
- Extensible via Protocol for future ML-based resolution

### 6. Test Coverage
**23 new tests** across 3 test files:

`tests/test_person_models.py` (10 tests):
- Person validation (non-empty names)
- PersonAlias validation (confidence bounds)
- ResolutionResult states (matched/ambiguous)
- ResolutionContext optional fields
- MergeRecord tracking
- ReviewRecord decisions

`tests/test_person_persistence.py` (7 tests):
- Person creation and retrieval
- Alias creation and linking
- Matching key lookup (hit/miss)
- Person merge with status update
- Alias listing
- Duplicate alias prevention

`tests/test_person_resolver.py` (6 tests):
- New person creation
- Exact matching key match
- Alias match
- Auto-creation with alias
- Alias addition to existing person
- Duplicate alias idempotency

## Architecture Decisions

### 1. Matching Key Strategy
**Decision**: Use `matching_key` (normalized, lowercased, no spaces/punctuation) for fast lookups.

**Rationale**:
- Deterministic and reproducible
- Fast indexed lookups
- Simple baseline that can be extended
- Avoids complex fuzzy matching in v1

### 2. Alias Origin Tracking
**Decision**: Track how each alias was created (extraction/manual/resolution/merge).

**Rationale**:
- Provenance for audit trail
- Enables filtering by origin
- Supports manual review workflows
- Critical for debugging resolution decisions

### 3. Merge as Status Change
**Decision**: Merged persons get `status=MERGED` and `merged_into_id` set, not deleted.

**Rationale**:
- Preserves history and audit trail
- Allows un-merge if needed
- Mentions/aliases remain linked
- Supports reverse lookups

### 4. Resolution Protocol
**Decision**: Define `PersonResolver` Protocol for extensibility.

**Rationale**:
- Decouples resolution algorithm from pipeline
- Enables A/B testing different algorithms
- Supports future ML-based resolution
- Maintains deterministic baseline

## Code Quality

### Static Analysis
```bash
uv run ruff check src tests        # ✅ All checks passed
uv run mypy --strict src tests     # ✅ Success: no issues found in 73 source files
```

### Test Results
```bash
uv run pytest                       # ✅ 165 passed in 1.33s
```

All existing tests (142) continue to pass, plus 23 new tests.

### Git History
```
198cedb feat: add canonical person model, aliases, and entity resolution baseline
```

**Changes**: 9 files, 1082 insertions

## Integration Points

### Existing Extraction Pipeline
The person resolution infrastructure is designed to integrate with the existing extraction pipeline:

1. **Extraction produces**: `NormalizedMention` with `entity_type=PERSON`, `normalized_text`, `matching_key`
2. **Resolver consumes**: These mentions and resolves to canonical `Person`
3. **Result**: `PersonAlias` records linking mentions to canonical persons

### Future Integration Pattern
```python
# In extraction pipeline
for mention in normalized_mentions:
    if mention.entity_type == EntityType.PERSON:
        resolution = resolver.resolve_and_create(
            normalized_text=mention.normalized_text,
            matching_key=mention.normalized_data.matching_key,
            surface_text=mention.surface_text,
            origin=AliasOrigin.EXTRACTION,
            confidence=mention.confidence,
            source_mention_id=mention.id,
        )
        # Link mention to resolution.person_id
```

## What's NOT Yet Implemented

The following components are part of the full court-monitor vision but were not implemented in this phase:

### High Priority (Next Phase)
1. **Extraction → Resolution Integration**
   - Wire person resolution into extraction pipeline
   - Add `person_id` to extraction results
   - Link events to resolved persons

2. **Political Persecution Classification**
   - `PersecutionClassifier` Protocol
   - Rule-based baseline (event types, article keywords, court patterns)
   - Classification confidence and reasons
   - Evaluation framework

3. **Rosfinmonitoring Integration**
   - Snapshot ingestion pipeline
   - Entry normalization
   - Person ↔ RF matching algorithm
   - Matching evaluation

4. **Main Product Query**
   - "Political but absent from RF" query
   - Result aggregation with full provenance

### Medium Priority (Later Phase)
5. **FastAPI Read-Only API**
   - GET /persons, /persons/{id}, /persons/{id}/events
   - GET /candidates (political, not in RF)
   - GET /rosfinmonitoring/snapshots

6. **CLI Extensions**
   - `resolve-people`: Run entity resolution on extracted mentions
   - `ingest-rosfinmonitoring`: Import RF snapshot
   - `match-rosfinmonitoring`: Match persons to RF
   - `classify-persecution`: Classify political persecution
   - `list-candidates`: List political, not-in-RF persons
   - `show-person <id>`: Show person details with events

7. **Evaluation Frameworks**
   - Entity resolution pairwise F1
   - RF matching precision/recall
   - Political classification accuracy

8. **Documentation**
   - Wiki: Persons.md, Entity-Resolution.md, Rosfinmonitoring.md, Political-Classification.md, Pipeline.md, API.md
   - ADRs: Canonical Person, Entity Resolution Strategy, RF Snapshot Model, Political Classification Baseline
   - Updated README with full pipeline

## Known Limitations

1. **Simple Resolution Algorithm**
   - Current: matching_key exact match only
   - Future: Add fuzzy matching, context-based resolution, birth date matching

2. **No Name Parsing**
   - Current: Matching key is simple normalization
   - Future: Parse first/last/patronymic, handle initials, handle transliteration

3. **No Event-Person Linking Yet**
   - Current: Events extracted but not linked to canonical persons
   - Future: Link events to persons via mention resolution

4. **No Political Classification**
   - Current: Not implemented
   - Future: Rule-based classifier using event types, article content, court patterns

5. **No Rosfinmonitoring Integration**
   - Current: Not implemented
   - Future: Full snapshot ingestion and matching pipeline

## Database Schema

```
persons
├── id (PK)
├── canonical_name
├── normalized_name
├── matching_key (indexed)
├── status (active/merged/needs_review)
├── merged_into_id (FK → persons.id)
├── created_at
└── updated_at

person_aliases
├── id (PK)
├── person_id (FK → persons.id)
├── surface_text
├── normalized_text
├── matching_key (indexed)
├── origin (extraction/manual/resolution/merge)
├── confidence
├── source_mention_id (FK → entity_mentions.id)
└── created_at

person_merges
├── id (PK)
├── source_person_id (FK → persons.id)
├── target_person_id (FK → persons.id)
├── status (pending/applied/reverted)
├── reason
├── applied_at
└── created_at

review_records
├── id (PK)
├── subject_type
├── subject_id
├── decision (approved/rejected/deferred)
├── confidence
├── reason
├── reviewer_note
├── created_at
└── reviewed_at
```

## Migration Path

```bash
# Apply migration
DATABASE_URL="postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor" \
  uv run alembic upgrade head

# Verify
uv run alembic current
# Expected: g1h2i3j4k5l6 (head)
```

## Next Steps (Recommended Order)

1. **Integrate resolution into extraction pipeline**
   - Modify extraction pipeline to call resolver for PERSON mentions
   - Store person_id in extraction results
   - Add tests for integrated flow

2. **Link events to persons**
   - When events reference person mentions, link to canonical persons
   - Add event_person_links table
   - Query: "show all events for person X"

3. **Implement political classification**
   - Define PersecutionClassifier Protocol
   - Rule-based baseline using event types and article keywords
   - Add classification confidence and reasons
   - Evaluation framework with labeled corpus

4. **Add Rosfinmonitoring integration**
   - Snapshot ingestion (parse RF data, store as snapshot)
   - Entry normalization
   - Person ↔ RF matching algorithm
   - Matching evaluation

5. **Build main product query**
   - Query: political persons not in RF snapshot
   - Result includes: person details, political classification, RF match status, relevant events, source articles

6. **Add API and CLI**
   - FastAPI endpoints for persons, events, candidates
   - CLI commands for all pipeline stages

7. **Documentation and evaluation**
   - Wiki pages for all new components
   - ADRs for key decisions
   - Evaluation frameworks for ER, RF matching, political classification
   - End-to-end golden test

## Conclusion

This implementation establishes a solid foundation for the court-monitor project's canonical person tracking and entity resolution. The architecture is:

- **Extensible**: Protocol-based design allows algorithm upgrades
- **Testable**: Comprehensive unit and integration tests
- **Auditable**: Full provenance tracking via aliases and merge records
- **Transactional**: All persistence operations use proper transactions
- **Performant**: Indexed for fast lookups

The next phase should focus on integrating this foundation with the extraction pipeline, then implementing political classification and Rosfinmonitoring integration to complete the core pipeline.

## Verification Commands

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src --cov-report=term-missing

# Static analysis
uv run ruff check src tests
uv run mypy --strict src tests

# Database migration
DATABASE_URL="..." uv run alembic upgrade head
uv run alembic current

# View git history
git log --oneline feature-complete-court-monitor ^main-fast
```

## Files Changed

```
migrations/versions/g1h2i3j4k5l6_add_person_and_resolution_tables.py (new)
src/orm_models.py (extended)
src/person_models.py (new)
src/person_persistence.py (new)
src/person_resolver.py (new)
tests/conftest.py (updated)
tests/test_person_models.py (new)
tests/test_person_persistence.py (new)
tests/test_person_resolver.py (new)
```

**Total**: 1082 lines added, 165 tests passing
