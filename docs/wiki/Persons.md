# Person Resolution

## Overview

The person resolution system extracts person mentions from articles, normalizes them, and resolves them to canonical person entities. This enables tracking individuals across multiple articles and sources.

## Architecture

### Domain Models

- **Person**: Canonical person entity with unique ID
  - `canonical_name`: Standardized full name
  - `normalized_name`: Normalized for matching (lowercase, no punctuation)
  - `matching_key`: Unique key for fast lookups
  - `merged_into_id`: Reference to another person if merged

- **PersonAlias**: Alternative names for a person
  - `surface_text`: Original text as it appeared
  - `normalized_text`: Normalized version
  - `matching_key`: For fast lookups
  - `origin`: How this alias was created (extraction, manual, merge)
  - `confidence`: Confidence score (0.0-1.0)

- **PersonMergeRecord**: Audit trail for person merges
  - `source_person_id`: Person being merged
  - `target_person_id`: Person being merged into
  - `status`: pending/applied/reverted
  - `reason`: Why the merge was performed

### Entity Resolution

The `RuleBasedPersonResolver` uses a deterministic, rule-based approach:

1. **Matching Key Strategy**: Normalized name → lowercase → remove punctuation → create unique key
2. **Exact Match**: If matching_key exists, link to existing person
3. **New Person**: If no match, create new canonical person
4. **Alias Creation**: Always create alias for the mention, linked to the person

This approach is:
- **Deterministic**: Same input always produces same output
- **Fast**: O(1) lookups via indexed matching_key
- **Transparent**: Easy to understand and debug
- **Extensible**: Can add fuzzy matching later without breaking existing code

### Database Schema

```sql
-- Canonical persons
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

-- Person aliases
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

-- Person-event links
CREATE TABLE person_event_links (
    id SERIAL PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    event_id INTEGER NOT NULL REFERENCES extracted_events(id) ON DELETE CASCADE,
    role VARCHAR(64) NOT NULL,
    confidence FLOAT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(person_id, event_id, role)
);
```

## CLI Commands

### resolve-people

Resolve extracted person mentions to canonical persons:

```bash
# Resolve all articles
court-monitor resolve-people

# Resolve specific article
court-monitor resolve-people --article-id 123

# Limit to N articles
court-monitor resolve-people --limit 100
```

Output:
```
Resolved 42 mentions, created 15 persons, linked 28 events
```

## API Endpoints

### GET /persons

List all persons:

```bash
curl http://localhost:8000/persons?limit=100&status=active
```

Response:
```json
[
  {
    "id": 1,
    "canonical_name": "Иванов Иван Иванович",
    "normalized_name": "иванов иван иванович",
    "matching_key": "ивановиваниванович",
    "status": "active",
    "merged_into_id": null
  }
]
```

### GET /persons/{id}

Get a specific person:

```bash
curl http://localhost:8000/persons/1
```

### GET /persons/{id}/aliases

Get all aliases for a person:

```bash
curl http://localhost:8000/persons/1/aliases
```

Response:
```json
[
  {
    "id": 1,
    "person_id": 1,
    "surface_text": "Иванова И.И.",
    "normalized_text": "иванова и и",
    "matching_key": "ивановаии",
    "origin": "extraction",
    "confidence": 0.9
  }
]
```

## Evaluation

Entity resolution quality is measured using pairwise F1:

```bash
court-monitor evaluate-er --dataset tests/fixtures/er_golden_dataset.json
```

Metrics:
- **Pairwise Precision**: Of all predicted pairs, how many are correct?
- **Pairwise Recall**: Of all correct pairs, how many did we predict?
- **Pairwise F1**: Harmonic mean of precision and recall

Example output:
```json
{
  "total_pairs_expected": 100,
  "total_pairs_actual": 95,
  "true_positives": 90,
  "false_positives": 5,
  "false_negatives": 10,
  "pairwise_precision": 0.947,
  "pairwise_recall": 0.9,
  "pairwise_f1": 0.923
}
```

## Future Improvements

### Fuzzy Matching

Current implementation uses exact matching_key matches. Future enhancements:
- Edit distance (Levenshtein) for typos
- Phonetic matching (Soundex, Metaphone) for similar-sounding names
- Context-based matching (same article, same event)

### Machine Learning

- Train classifier on labeled data
- Use embeddings for semantic similarity
- Active learning for ambiguous cases

### Manual Review

Integration with manual review workflow:
- Flag low-confidence matches
- Allow human reviewers to confirm/reject
- Learn from reviewer decisions
