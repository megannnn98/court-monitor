# Person Resolution

**Status:** persons are resolved by Entity Resolution v2 only: candidate
generation (exact `matching_key`, aliases, pg_trgm, optional semantic),
feature-based scoring and a decision policy with human review
(`AUTO_LINK` / `REVIEW` / `CREATE_NEW`). **`matching_key` is a candidate lookup
key, not an identity key**: active namesakes may share it, and an equal key is
never an automatic link on its own. See [Entity-Resolution](Entity-Resolution.md)
and `docs/adr/0012-entity-resolution-v2.md`. ER v2 never merges existing persons
automatically.

## Overview

The person resolution system extracts person mentions from articles, normalizes them, and resolves them to canonical person entities. This enables tracking individuals across multiple articles and sources.

## Architecture

### Domain Models

- **Person**: Canonical person entity with unique ID
  - `canonical_name`: Standardized full name
  - `normalized_name`: Normalized for matching (lowercase, no punctuation)
  - `matching_key`: Candidate lookup key (not unique: namesakes share it)
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

ER v2 (`src/persons/resolution/`) under an identity-block advisory lock:
candidates → features → `resolution_score` → decision. A single active person
with the incoming key auto-links; several (namesakes or duplicates) go to
review (`multiple_exact_name_matches`). AUTO_LINK links; CREATE_NEW creates a
person; REVIEW leaves the mention unlinked with a pending `person_resolution`
review, where a reviewer may link, create a same-name person, merge (audited)
or keep two persons separate. Aliases are added only for clean full forms
(`AliasPromotionPolicy`).

Details, thresholds, evaluation: [Entity-Resolution](Entity-Resolution.md).

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
-- Not unique (namesakes): partial index for candidate lookup.
CREATE INDEX ix_persons_matching_key_active ON persons(matching_key) WHERE status = 'active';
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
Resolved 42 mentions, created 15 persons, linked 28 events, 3 mentions pending person resolution review
```

ER v2 commands (`resolve-person` dry-run, `person-resolution-reviews`,
`evaluate-er`): [Entity-Resolution](Entity-Resolution.md).

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

`evaluate-er` measures candidate recall@k per generator and decision actions
(auto-link precision/recall, false links, false create-new, review rate) on
`tests/fixtures/er_v2_corpus.json` — see [Entity-Resolution](Entity-Resolution.md#evaluation).
The older pairwise-F1 helpers (`persons/er_evaluation.py`, `er_golden_dataset.json`)
remain as a library, without a CLI command.

## Not implemented

- Transliteration, diminutives without an alias, phonetic matching.
- Birth dates and context features (not extracted).
- ML classifier (no labelled dataset large enough).
- Automatic merge of existing persons (by design: reviewer action only).
