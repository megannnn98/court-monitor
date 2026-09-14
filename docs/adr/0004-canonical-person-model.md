# ADR 0004: Canonical Person Model

## Status

Accepted

## Context

The court-monitor project needs to track individuals across multiple articles and sources. Person mentions appear in various forms:
- Full names: "Иванов Иван Иванович"
- Partial names: "Иван Иванов", "И. Иванов"
- Different cases: "Иванова", "Иванову", "Ивановым"
- Transliterations: "Ivan Ivanov", "Ivanov I.I."

We need a way to:
1. Identify when different mentions refer to the same person
2. Store canonical information about each person
3. Track all variations of a person's name
4. Support merging when duplicates are discovered
5. Maintain provenance (where each piece of information came from)

## Decision

We implemented a canonical person model with the following components:

### Person Entity

```python
class Person:
    id: int  # Unique identifier
    canonical_name: str  # Standardized full name
    normalized_name: str  # Normalized for matching (lowercase, no punctuation)
    matching_key: str  # Unique key for fast lookups
    status: str  # active/merged/needs_review
    merged_into_id: int | None  # Reference to another person if merged
```

### PersonAlias Entity

```python
class PersonAlias:
    id: int
    person_id: int  # Reference to canonical person
    surface_text: str  # Original text as it appeared
    normalized_text: str  # Normalized version
    matching_key: str  # For fast lookups
    origin: str  # extraction/manual/merge
    confidence: float  # 0.0 to 1.0
    source_mention_id: int | None  # Link to source mention
```

### PersonMergeRecord Entity

```python
class PersonMergeRecord:
    id: int
    source_person_id: int  # Person being merged
    target_person_id: int  # Person being merged into
    status: str  # pending/applied/reverted
    reason: str  # Why the merge was performed
```

### Key Design Decisions

1. **Matching Key Strategy**
   - Normalize name: lowercase, remove punctuation, remove spaces
   - Example: "Иванов Иван Иванович" → "ивановиваниванович"
   - Provides O(1) lookups via indexed matching_key
   - Deterministic: same input always produces same output

2. **Alias-per-Mention**
   - Create one alias for each mention encountered
   - Preserves provenance (which article, which mention)
   - Enables tracking name variations over time
   - Supports deduplication via UNIQUE(person_id, surface_text)

3. **Soft Deletes for Merges**
   - Merged persons are not deleted
   - Set status='merged' and merged_into_id
   - Preserves all mentions and events
   - Allows reverting merges if needed

4. **Provenance Tracking**
   - Every alias links to source_mention_id
   - Origin field tracks how alias was created
   - Confidence score indicates reliability
   - Full audit trail via merge records

## Consequences

### Positive

1. **Deterministic Resolution**
   - Same input always produces same output
   - Easy to test and debug
   - No randomness or ML model drift

2. **Fast Lookups**
   - Indexed matching_key enables O(1) lookups
   - Can handle millions of persons efficiently
   - No need for expensive similarity searches

3. **Full Provenance**
   - Every piece of data traces back to source
   - Can answer "where did we learn this name?"
   - Enables trust scoring based on sources

4. **Reversible Operations**
   - Merges can be reverted
   - No data loss on errors
   - Full audit trail for compliance

5. **Extensible**
   - Can add fuzzy matching later
   - Can add ML-based resolution
   - Can add more attributes to Person

### Negative

1. **Storage Overhead**
   - One alias per mention can be verbose
   - Mitigated by indexes and efficient queries

2. **No Fuzzy Matching**
   - Exact matching_key may miss variations
   - Mitigated by creating multiple aliases
   - Future: add fuzzy matching as separate strategy

3. **Manual Review Needed**
   - Some merges require human judgment
   - Mitigated by review queue and UI
   - Future: active learning from reviews

4. **Name Normalization Complexity**
   - Russian names have complex morphology
   - Mitigated by rule-based normalizer
   - Future: ML-based normalization

## Alternatives Considered

### Alternative 1: No Canonical Model

Store each mention separately, group at query time.

**Pros**: Simpler data model, no merge logic
**Cons**: Expensive queries, no canonical name, hard to track individuals

**Rejected**: Cannot efficiently answer "show me all articles about person X"

### Alternative 2: Graph-Based Model

Use graph database (Neo4j) to model person-mention relationships.

**Pros**: Flexible, can model complex relationships
**Cons**: New technology stack, operational complexity, overkill for current needs

**Rejected**: PostgreSQL is sufficient, graph DB adds complexity without clear benefit

### Alternative 3: ML-Based Resolution Only

Use machine learning embeddings for all resolution.

**Pros**: Can handle fuzzy matching, semantic similarity
**Cons**: Non-deterministic, hard to debug, requires training data, model drift

**Rejected**: Start with deterministic baseline, add ML as enhancement

### Alternative 4: External Identity Service

Use external service (e.g., Wikidata) for canonical identities.

**Pros**: Leverages existing data, community-maintained
**Cons**: Not all persons are in Wikidata, no control over data quality, dependency

**Rejected**: Many persons of interest are not in public databases, need full control

## Implementation Details

### Database Schema

```sql
CREATE TABLE persons (
    id SERIAL PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    matching_key VARCHAR(255) NOT NULL,
    status VARCHAR(32) DEFAULT 'active',
    merged_into_id INTEGER REFERENCES persons(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP
);

CREATE INDEX ix_persons_matching_key ON persons(matching_key);
CREATE INDEX ix_persons_status ON persons(status);

CREATE TABLE person_aliases (
    id SERIAL PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    surface_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    matching_key VARCHAR(255) NOT NULL,
    origin VARCHAR(32) NOT NULL,
    confidence FLOAT NOT NULL,
    source_mention_id INTEGER REFERENCES entity_mentions(id),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(person_id, surface_text)
);

CREATE INDEX ix_person_aliases_person_id ON person_aliases(person_id);
CREATE INDEX ix_person_aliases_matching_key ON person_aliases(matching_key);
```

### Resolution Algorithm

```python
def resolve_mention(mention: EntityMention) -> Person:
    # Normalize the mention
    normalized = normalize_person_name(mention.surface_text)
    matching_key = create_matching_key(normalized)

    # Look for existing person
    existing = find_person_by_matching_key(matching_key)

    if existing:
        # Link to existing person
        create_alias(existing, mention)
        return existing
    else:
        # Create new person
        person = create_person(normalized, matching_key)
        create_alias(person, mention)
        return person
```

### Merge Algorithm

```python
def merge_persons(source_id: int, target_id: int, reason: str):
    # Move all aliases from source to target
    move_aliases(source_id, target_id)

    # Move all events from source to target
    move_events(source_id, target_id)

    # Mark source as merged
    update_person(source_id, status='merged', merged_into_id=target_id)

    # Record the merge
    create_merge_record(source_id, target_id, reason)
```

## Future Enhancements

1. **Fuzzy Matching**
   - Add edit distance for typo handling
   - Add phonetic matching for similar-sounding names
   - Add context-based matching (same article, same event)

2. **Machine Learning**
   - Train classifier on labeled data
   - Use embeddings for semantic similarity
   - Active learning from manual reviews

3. **Additional Attributes**
   - Birth date
   - Location
   - Occupation
   - Known associations

4. **Cross-Reference Integration**
   - Link to Wikidata
   - Link to social media
   - Link to news databases

## References

- Implementation: `src/persons/models.py`, `src/persons/persistence.py`, `src/persons/resolver.py`
- Tests: `tests/persons/test_person_models.py`, `tests/persons/test_person_persistence.py`, `tests/persons/test_person_resolver.py`
- Migration: `migrations/versions/g1h2i3j4k5l6_add_person_and_resolution_tables.py`
