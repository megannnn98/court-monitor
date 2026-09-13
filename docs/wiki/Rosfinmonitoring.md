# Rosfinmonitoring

## Overview

Rosfinmonitoring (Росфинмониторинг) is the Russian Federal Financial Monitoring Service that maintains a list of terrorists and extremists. The court-monitor project imports this list to identify persons who are:
1. Politically persecuted (according to our classification)
2. NOT present in the Rosfinmonitoring list

This is the main product query of the project.

## Architecture

### Data Model

#### Snapshot

A snapshot represents the Rosfinmonitoring list at a specific point in time:

```python
class RosfinmonitoringSnapshot:
    id: int
    snapshot_date: datetime  # When the list was captured
    source_url: str  # Where the list was fetched from
    content_hash: str  # SHA-256 hash for deduplication
    entry_count: int  # Number of entries in the snapshot
    fetched_at: datetime  # When we fetched it
    raw_content: bytes  # Original content (optional)
```

#### Entry

An entry represents a single person/organization in the list:

```python
class RosfinmonitoringEntry:
    id: int
    snapshot_id: int  # Which snapshot this belongs to
    full_name: str  # Full name as it appears in the list
    normalized_name: str  # Normalized for matching
    matching_key: str  # Unique key for fast lookups
    birth_date: datetime | None  # Date of birth (if available)
    birth_place: str | None  # Place of birth (if available)
    snils: str | None  # Russian personal ID number
    inn: str | None  # Taxpayer ID
    inclusion_reason: str | None  # Why they were added
    inclusion_date: datetime | None  # When they were added
    status: str  # active/removed/unknown
    raw_data: dict  # Original data from the source
```

#### Match Result

A match result links a canonical person to a Rosfinmonitoring entry:

```python
class RosfinMatchResult:
    person_id: int  # Canonical person
    snapshot_id: int  # Which snapshot was used
    status: str  # matched/not_matched/ambiguous/needs_review/insufficient_data
    confidence: float  # 0.0 to 1.0 — not_matched reports NOT_MATCHED_CONFIDENCE
    # (0.8), not 1.0: finding zero candidates in a snapshot search supports
    # absence probabilistically, not with certainty. insufficient_data means
    # the person's own name is too thin (< 2 words) to search reliably at
    # all — a bare surname finding nothing says nothing about presence.
    matched_entry_id: int | None  # Which entry (if matched)
    matched_entry_name: str | None  # Name from the entry
    candidate_entries: list  # Top candidate entries
    reasons: list  # Why this match was chosen
    matched_at: datetime  # When the match was performed
```

### Database Schema

```sql
-- Snapshots
CREATE TABLE rosfinmonitoring_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_date TIMESTAMP WITH TIME ZONE NOT NULL,
    source_url TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    entry_count INTEGER NOT NULL,
    fetched_at TIMESTAMP WITH TIME ZONE NOT NULL,
    raw_content BYTEA,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(content_hash)
);

CREATE INDEX ix_rosfinmonitoring_snapshots_snapshot_date
    ON rosfinmonitoring_snapshots(snapshot_date);

-- Entries
CREATE TABLE rosfinmonitoring_entries (
    id SERIAL PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES rosfinmonitoring_snapshots(id) ON DELETE CASCADE,
    full_name VARCHAR(512) NOT NULL,
    normalized_name VARCHAR(512) NOT NULL,
    matching_key VARCHAR(255) NOT NULL,
    birth_date TIMESTAMP WITH TIME ZONE,
    birth_place VARCHAR(512),
    snils VARCHAR(20),
    inn VARCHAR(20),
    inclusion_reason TEXT,
    inclusion_date TIMESTAMP WITH TIME ZONE,
    status VARCHAR(32) DEFAULT 'active',
    raw_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(snapshot_id, full_name, birth_date)
);

CREATE INDEX ix_rosfinmonitoring_entries_snapshot_id
    ON rosfinmonitoring_entries(snapshot_id);
CREATE INDEX ix_rosfinmonitoring_entries_matching_key
    ON rosfinmonitoring_entries(matching_key);
CREATE INDEX ix_rosfinmonitoring_entries_normalized_name
    ON rosfinmonitoring_entries(normalized_name);

-- Match results
CREATE TABLE rosfin_matches (
    id SERIAL PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    snapshot_id INTEGER NOT NULL REFERENCES rosfinmonitoring_snapshots(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL,
    confidence FLOAT NOT NULL,
    matched_entry_id INTEGER REFERENCES rosfinmonitoring_entries(id),
    matched_entry_name VARCHAR(512),
    candidate_entries JSONB NOT NULL DEFAULT '[]',
    reasons JSONB NOT NULL DEFAULT '[]',
    matched_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(person_id, snapshot_id)
);

CREATE INDEX ix_rosfin_matches_person_id ON rosfin_matches(person_id);
CREATE INDEX ix_rosfin_matches_snapshot_id ON rosfin_matches(snapshot_id);
CREATE INDEX ix_rosfin_matches_status ON rosfin_matches(status);
```

## Ingestion

### Import from File

```bash
court-monitor import-rosfinmonitoring --file rosfin.xml
```

Supported formats:
- **XML**: Official Rosfinmonitoring format
- **CSV**: Comma-separated values
- **JSON**: JSON array or object

### Import from URL

```bash
court-monitor import-rosfinmonitoring --url https://rosfinmonitoring.gov.ru/list
```

### Deduplication

The system prevents duplicate snapshots using content hash:

```python
if persistence.snapshot_exists(content_hash):
    logger.info(f"Snapshot {content_hash} already exists, skipping")
    return existing_snapshot
```

## Matching

### Algorithm

The `RuleBasedRosfinmonitoringMatcher` uses multiple strategies:

1. **Exact matching_key match** (confidence: 0.95)
   - Normalize person name → matching_key
   - Look up in Rosfinmonitoring entries
   - Highest confidence if exact match

2. **Name similarity** (confidence: 0.6-0.9)
   - Compute Jaccard coefficient on name words
   - Higher similarity → higher confidence

3. **Birth date comparison** (confidence boost: +0.05)
   - If both have birth dates and they match
   - Increases confidence in the match

4. **Multiple candidates** (status: ambiguous)
   - If multiple entries have similar scores
   - Flag for manual review

### CLI

```bash
# Match all persons against snapshot
court-monitor match-rosfinmonitoring --snapshot-id 5

# Match specific person
court-monitor match-rosfinmonitoring --snapshot-id 5 --person-id 123

# Limit to N persons
court-monitor match-rosfinmonitoring --snapshot-id 5 --limit 100
```

### Output

```json
{
  "person_id": 123,
  "snapshot_id": 5,
  "status": "matched",
  "confidence": 0.95,
  "matched_entry_id": 456,
  "matched_entry_name": "Иванов Иван Иванович",
  "candidate_entries": [
    {
      "entry_id": 456,
      "full_name": "Иванов Иван Иванович",
      "normalized_name": "иванов иван иванович",
      "matching_key": "ивановиваниванович",
      "similarity_score": 0.95,
      "reasons": ["Exact matching_key match", "Birth date match"]
    }
  ],
  "reasons": ["Exact matching_key match", "Birth date match"],
  "matched_at": "2026-09-13T15:30:00Z"
}
```

## API Endpoints

### GET /rosfinmonitoring/snapshots

List all snapshots:

```bash
curl http://localhost:8000/rosfinmonitoring/snapshots
```

Response:
```json
[
  {
    "id": 1,
    "snapshot_date": "2026-09-01T00:00:00Z",
    "source_url": "https://rosfinmonitoring.gov.ru/list",
    "content_hash": "abc123...",
    "entry_count": 10000
  }
]
```

### GET /rosfinmonitoring/snapshots/{id}

Get a specific snapshot:

```bash
curl http://localhost:8000/rosfinmonitoring/snapshots/1
```

### GET /rosfinmonitoring/snapshots/{id}/entries

List entries in a snapshot:

```bash
curl "http://localhost:8000/rosfinmonitoring/snapshots/1/entries?limit=100&offset=0"
```

Response:
```json
[
  {
    "id": 456,
    "snapshot_id": 1,
    "full_name": "Иванов Иван Иванович",
    "normalized_name": "иванов иван иванович",
    "matching_key": "ивановиваниванович",
    "birth_date": "1980-01-15T00:00:00Z",
    "inclusion_reason": "Extremism",
    "inclusion_date": "2025-06-01T00:00:00Z"
  }
]
```

## Evaluation

### Dataset

Create a golden dataset with expected matches:

```json
{
  "snapshot_id": 1,
  "cases": [
    {
      "person_id": 123,
      "expected_match": true,
      "expected_entry_id": 456,
      "description": "Should match Иванов"
    },
    {
      "person_id": 789,
      "expected_match": false,
      "description": "Should not match"
    }
  ]
}
```

### Metrics

```bash
court-monitor evaluate-rosfinmatch --dataset tests/fixtures/rosfinmatch_golden_dataset.json
```

Metrics:
- **Precision**: Of all predicted matches, how many are correct?
- **Recall**: Of all actual matches, how many did we predict?
- **F1**: Harmonic mean of precision and recall

Example output:
```json
{
  "total_cases": 100,
  "true_positives": 45,
  "false_positives": 5,
  "true_negatives": 48,
  "false_negatives": 2,
  "precision": 0.9,
  "recall": 0.957,
  "f1": 0.928
}
```

## Querying

### Main Product Query

Find politically persecuted persons NOT in Rosfinmonitoring:

```bash
court-monitor list-candidates --snapshot-id 1
```

This query:
1. Finds all persons with political persecution classification
2. Checks their Rosfinmonitoring match status
3. Returns only those with a **confirmed** `not_matched` result by default —
   `no_match_record` (matching never ran), `ambiguous`, `needs_review` and
   `insufficient_data` are excluded, since none of them mean "absent"

### API

```bash
curl "http://localhost:8000/candidates?snapshot_id=1&min_confidence=0.8"
```

Response:
```json
{
  "snapshot_id": 1,
  "candidates": [
    {
      "person_id": 42,
      "canonical_name": "Петр Петров",
      "normalized_name": "петр петров",
      "persecution_status": "political",
      "persecution_confidence": 0.9,
      "persecution_reasons": ["Charged under article 280"],
      "rosfinmonitoring_status": "not_matched",
      "rosfinmonitoring_match_confidence": null,
      "event_count": 3,
      "alias_count": 5
    }
  ],
  "total_count": 1
}
```

## Versioning

### Snapshot Strategy

The system maintains immutable snapshots:

1. **No modification**: Once created, a snapshot cannot be changed
2. **Content hash**: Prevents duplicate imports
3. **Historical tracking**: Can query any point in time
4. **Comparison**: Can compare snapshots to see changes

### Entry Status

Entries have status:
- `active`: Currently in the list
- `removed`: Was in the list, now removed
- `unknown`: Status not determined

### Tracking Changes

To see who was added/removed between snapshots:

```sql
-- Added between snapshot 1 and 2
SELECT e2.full_name
FROM rosfinmonitoring_entries e2
LEFT JOIN rosfinmonitoring_entries e1
  ON e1.full_name = e2.full_name
  AND e1.snapshot_id = 1
WHERE e2.snapshot_id = 2
  AND e1.id IS NULL;

-- Removed between snapshot 1 and 2
SELECT e1.full_name
FROM rosfinmonitoring_entries e1
LEFT JOIN rosfinmonitoring_entries e2
  ON e2.full_name = e1.full_name
  AND e2.snapshot_id = 2
WHERE e1.snapshot_id = 1
  AND e2.id IS NULL;
```

## Manual Review

### Review Queue

When matching is ambiguous, entries are flagged for manual review:

```bash
court-monitor review-queue --type rosfinmatch
```

### Review Actions

```bash
# Confirm match
court-monitor review --id 123 --decision confirm

# Reject match
court-monitor review --id 123 --decision reject

# Mark as ambiguous
court-monitor review --id 123 --decision ambiguous
```

### Review Records

All reviews are stored in `review_records` table:

```sql
SELECT * FROM review_records
WHERE subject_type = 'rosfinmatch'
ORDER BY created_at DESC;
```

## Performance

### Indexing

Key indexes for performance:
- `rosfinmonitoring_entries.matching_key`: Fast lookups
- `rosfinmonitoring_entries.normalized_name`: Name similarity searches
- `rosfin_matches.person_id`: Query matches for a person
- `rosfin_matches.snapshot_id`: Query matches for a snapshot

### Batch Processing

For large datasets, process in batches:

```python
batch_size = 1000
for offset in range(0, total_persons, batch_size):
    persons = get_persons(limit=batch_size, offset=offset)
    matcher.match_all_persons(persons, snapshot_id)
```

## Future Improvements

### Fuzzy Matching

Current implementation uses exact matching_key. Future enhancements:
- Edit distance (Levenshtein) for typos
- Phonetic matching (Soundex, Metaphone)
- Context-based matching (same city, same organization)

### Machine Learning

- Train classifier on labeled data
- Use embeddings for semantic similarity
- Learn from manual review decisions

### International Lists

Support for other countries' sanction lists:
- UN sanctions list
- EU sanctions list
- US OFAC list
