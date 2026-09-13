# ADR 0007: Rosfinmonitoring Snapshot Model

## Status

Accepted

## Context

The court-monitor project needs to track the Rosfinmonitoring (Russian Federal Financial Monitoring Service) list of terrorists and extremists over time. The challenge is:

1. The list changes over time (people added/removed)
2. Need to match persons against specific versions of the list
3. Must be able to answer "was person X in the list at time T?"
4. Must handle multiple formats (XML, CSV, JSON)
5. Must prevent duplicate imports
6. Must maintain provenance (where did the list come from?)

Options considered:
- Single table with current state
- Versioned table with history
- Snapshot-based model
- Event sourcing model

## Decision

We implemented a **snapshot-based model** where each import creates an immutable snapshot:

### Data Model

```python
class RosfinmonitoringSnapshot:
    id: int
    snapshot_date: datetime  # When the list was captured
    source_url: str  # Where the list was fetched from
    content_hash: str  # SHA-256 hash for deduplication
    entry_count: int  # Number of entries in the snapshot
    fetched_at: datetime  # When we fetched it
    raw_content: bytes | None  # Original content (optional)

class RosfinmonitoringEntry:
    id: int
    snapshot_id: int  # Which snapshot this belongs to
    full_name: str  # Full name as it appears in the list
    normalized_name: str  # Normalized for matching
    matching_key: str  # Unique key for fast lookups
    birth_date: datetime | None
    birth_place: str | None
    snils: str | None  # Russian personal ID
    inn: str | None  # Taxpayer ID
    inclusion_reason: str | None
    inclusion_date: datetime | None
    status: str  # active/removed/unknown
    raw_data: dict  # Original data from source

class RosfinMatchResult:
    person_id: int  # Canonical person
    snapshot_id: int  # Which snapshot was used
    status: str  # matched/not_matched/ambiguous/needs_review
    confidence: float  # 0.0 to 1.0
    matched_entry_id: int | None
    matched_entry_name: str | None
    candidate_entries: list  # Top candidate entries
    reasons: list  # Why this match was chosen
    matched_at: datetime
```

### Key Design Decisions

1. **Immutable Snapshots**
   - Once created, a snapshot cannot be modified
   - Ensures historical accuracy
   - Enables reproducible queries

2. **Content Hash Deduplication**
   - Compute SHA-256 hash of raw content
   - Prevent duplicate imports
   - UNIQUE constraint on content_hash

3. **Entry-per-Snapshot**
   - Each entry belongs to exactly one snapshot
   - Enables tracking changes over time
   - Simple queries for "what was in snapshot X?"

4. **Separate Match Results**
   - Match results are separate from entries
   - Can match same person against multiple snapshots
   - Enables tracking match history

5. **Provenance Tracking**
   - Every snapshot has source_url and fetched_at
   - Can trace back to original source
   - Enables auditing

## Consequences

### Positive

1. **Historical Accuracy**
   - Can query any point in time
   - No data loss on updates
   - Full audit trail

2. **Deduplication**
   - Content hash prevents duplicate imports
   - Saves storage and processing time
   - Clear error messages for duplicates

3. **Flexibility**
   - Can compare snapshots to see changes
   - Can match against any snapshot version
   - Can import from multiple sources

4. **Simplicity**
   - Simple data model
   - Easy to understand and query
   - No complex versioning logic

5. **Performance**
   - Indexed matching_key for fast lookups
   - Can handle thousands of entries
   - Efficient batch matching

### Negative

1. **Storage Overhead**
   - Each snapshot stores all entries
   - May have duplicate entries across snapshots
   - Mitigated by efficient storage and indexes

2. **Complex Queries**
   - Need to join snapshots and entries
   - Mitigated by clear API and helper methods

3. **Manual Snapshot Management**
   - Need to decide when to create new snapshots
   - Mitigated by clear import workflow

## Implementation

### Database Schema

```sql
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

### Import Workflow

```python
def import_rosfinmonitoring(raw_content: bytes, source_url: str):
    # Compute content hash
    content_hash = hashlib.sha256(raw_content).hexdigest()

    # Check for duplicate
    if persistence.snapshot_exists(content_hash):
        logger.info(f"Snapshot {content_hash} already exists, skipping")
        return

    # Parse content
    parser = detect_parser(raw_content)  # XML, CSV, or JSON
    entries = parser.parse(raw_content)

    # Create snapshot
    snapshot = RosfinmonitoringSnapshot(
        snapshot_date=datetime.now(UTC),
        source_url=source_url,
        content_hash=content_hash,
        entry_count=len(entries),
        fetched_at=datetime.now(UTC),
        raw_content=raw_content,
    )
    snapshot_id = persistence.create_snapshot(snapshot)

    # Save entries
    for entry in entries:
        entry.snapshot_id = snapshot_id
        persistence.save_entry(entry)
```

### Matching Workflow

```python
def match_person(person_id: int, snapshot_id: int):
    # Get person
    person = person_persistence.get_person(person_id)

    # Get snapshot entries
    entries = rosfin_persistence.get_entries_for_snapshot(snapshot_id)

    # Find best match
    candidates = []
    for entry in entries:
        similarity = compute_similarity(person, entry)
        if similarity > threshold:
            candidates.append((entry, similarity))

    # Create match result
    if candidates:
        best = max(candidates, key=lambda x: x[1])
        result = RosfinMatchResult(
            person_id=person_id,
            snapshot_id=snapshot_id,
            status='matched',
            confidence=best[1],
            matched_entry_id=best[0].id,
            matched_entry_name=best[0].full_name,
            candidate_entries=[...],
            reasons=[...],
            matched_at=datetime.now(UTC),
        )
    else:
        result = RosfinMatchResult(
            person_id=person_id,
            snapshot_id=snapshot_id,
            status='not_matched',
            confidence=0.0,
            matched_at=datetime.now(UTC),
        )

    return match_persistence.save_result(result)
```

## Alternatives Considered

### Alternative 1: Single Table with Current State

Store only current state, overwrite on update.

**Pros**: Simple, less storage
**Cons**: No history, cannot track changes, no audit trail

**Rejected**: Need historical accuracy for legal/compliance reasons

### Alternative 2: Event Sourcing

Store each change as an event.

**Pros**: Full history, can reconstruct any state
**Cons**: Complex queries, expensive storage, overkill for use case

**Rejected**: Snapshot model is simpler and sufficient

### Alternative 3: Versioned Table

Single table with version column.

**Pros**: Simpler queries, less storage
**Cons**: Complex updates, hard to track changes

**Rejected**: Snapshot model is clearer and more flexible

### Alternative 4: External Database

Store in external database (e.g., Rosfinmonitoring API).

**Pros**: No storage overhead, always up-to-date
**Cons**: Dependency on external service, no historical data, no control

**Rejected**: Need full control and historical data

## Future Enhancements

### Snapshot Comparison

Add ability to compare snapshots:

```python
def compare_snapshots(snapshot_id_1: int, snapshot_id_2: int):
    entries_1 = get_entries_for_snapshot(snapshot_id_1)
    entries_2 = get_entries_for_snapshot(snapshot_id_2)

    added = [e for e in entries_2 if e.full_name not in entries_1]
    removed = [e for e in entries_1 if e.full_name not in entries_2]

    return {
        'added': added,
        'removed': removed,
        'snapshot_1_count': len(entries_1),
        'snapshot_2_count': len(entries_2),
    }
```

### Automated Imports

Add scheduled imports:

```python
# Cron job or scheduled task
def scheduled_import():
    raw_content = fetch_from_rosfinmonitoring()
    import_rosfinmonitoring(raw_content, source_url)
```

### Change Notifications

Notify when persons are added/removed:

```python
def check_for_changes(person_id: int, old_snapshot_id: int, new_snapshot_id: int):
    old_match = get_match(person_id, old_snapshot_id)
    new_match = get_match(person_id, new_snapshot_id)

    if old_match.status == 'not_matched' and new_match.status == 'matched':
        notify(f"Person {person_id} was added to Rosfinmonitoring")
    elif old_match.status == 'matched' and new_match.status == 'not_matched':
        notify(f"Person {person_id} was removed from Rosfinmonitoring")
```

## References

- Implementation: `src/rosfinmonitoring_models.py`, `src/rosfinmonitoring_persistence.py`, `src/rosfinmonitoring_parser.py`, `src/rosfinmonitoring_ingestion.py`, `src/rosfinmonitoring_matcher.py`, `src/rosfinmonitoring_matcher_persistence.py`
- Tests: `tests/test_rosfinmonitoring_parser.py`, `tests/test_rosfinmonitoring_matcher.py`
- Migration: `migrations/versions/j4k5l6m7n8o9_add_rosfinmonitoring_tables.py`
- Related: `docs/wiki/Rosfinmonitoring.md`
