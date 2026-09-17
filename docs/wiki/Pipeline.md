# Pipeline

## Overview

The court-monitor pipeline processes articles from multiple sources through several stages to identify politically persecuted persons absent from the Rosfinmonitoring list. This document describes the complete pipeline architecture and how to run it.

## Pipeline Stages

```
┌─────────────┐
│   Source    │  OVD-Info, SOTA, etc.
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Ingestion  │  Fetch and parse articles
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Extraction │  Extract entities and events
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Resolution │  Resolve persons to canonical entities
└──────┬──────┘
       │
       ▼
┌─────────────┐
│Classify     │  Classify political persecution
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Match     │  Match against Rosfinmonitoring
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Query     │  Identify candidates (political, not in RF)
└─────────────┘
```

## Bulk rebuild: workers and GPU

`extract-entities`, `match-rosfinmonitoring` and `classify-persecution` take
`--workers N` (default 8, capped by the CPU count). Their items are independent, so the
result does not depend on N; workers are spawned processes with their own connection.
`resolve-people --workers N` stays opt-in (default 1): its decisions depend on the order
(see Entity-Resolution).

```bash
court-monitor extract-entities --limit 100000
court-monitor resolve-people --limit 100000 --workers 8
court-monitor match-rosfinmonitoring --snapshot-id 1 --limit 100000
court-monitor classify-persecution --limit 100000
```

Measured on the working corpus (20 996 articles, 12 cores): extraction 242 s → 80 s;
matching 2 000 persons 163 s → 18 s (trigram index on
`rosfinmonitoring_entries.normalized_name`, then 8 workers); classifying 2 000 persons
11.7 s → 7.8 s.

The person recognizer (`PERSON_EXTRACTION_STRATEGY=ner|hybrid`) runs on CUDA when it is
available: 26 ms per article against 269 ms on the CPU. Each worker loads its own copy of
the model (1.2 GiB), so extraction with the model on the GPU uses at most 4 workers. In
Docker the GPU needs `compose.gpu.yaml` (see the file header for the host setup). Changing
the strategy changes the extraction run version: every article is extracted again.

## Stage Details

### 1. Source Ingestion

**Purpose**: Fetch articles from configured sources and store them in the database.

**Components**:
- `SourceAdapter`: Fetches raw HTML from source websites
- `ArticleParser`: Parses HTML into structured `ParsedArticle` objects
- `IngestionPipeline`: Orchestrates fetch → parse → save

**CLI**:
```bash
# Discover and ingest articles
court-monitor discover-and-ingest --source ovd-info --limit 100

# Ingest single article
court-monitor ingest https://ovd.info/news/example
```

**Database Tables**:
- `sources`: Source definitions (name, base_url)
- `source_documents`: Raw fetched documents
- `parsed_articles`: Parsed article content

### 2. Entity Extraction

**Purpose**: Extract entities (persons, organizations, courts, locations, legal references) and events from article text.

**Components**:
- `EntityExtractor`: Identifies entity mentions in text
- `MentionNormalizer`: Normalizes entity data (e.g., person names)
- `EventExtractor`: Identifies events (arrests, charges, sentences)
- `ExtractionPipeline`: Orchestrates extraction → normalization → validation → persistence

**CLI**:
```bash
# Extract from all articles
court-monitor extract-entities

# Extract from specific article
court-monitor extract-entities --article-id 123
```

**Database Tables**:
- `entity_mentions`: Extracted entity mentions
- `extracted_events`: Extracted events
- `event_entity_mentions`: Links between events and entities
- `article_extraction_runs`: Extraction run metadata

**Key Invariants**:
- Each mention has exact offsets into the article text
- `article.text[start_offset:end_offset] == surface_text`
- Idempotent: re-running produces same results
- Provenance: every mention traces back to source article

### 3. Person Resolution

**Purpose**: Resolve extracted person mentions to canonical person entities, creating new persons as needed.

**Components**:
- `ExtractionResolutionService`: Orchestrates resolution for extraction runs
- Entity Resolution v2 (`src/persons/resolution/`, see [Entity-Resolution](Entity-Resolution.md))

**CLI**:
```bash
# Resolve all persons
court-monitor resolve-people

# Resolve specific article
court-monitor resolve-people --article-id 123
```

**Database Tables**:
- `persons`: Canonical person entities
- `person_aliases`: Alternative names for persons
- `person_event_links`: Links between persons and events

**Key Features**:
- `matching_key` is a candidate lookup key, not an identity key (namesakes allowed)
- Aliases only for clean full forms (`AliasPromotionPolicy`)
- Person merging for duplicates
- Audit trail for all merges

### 4. Persecution Classification

**Purpose**: Classify persons as politically persecuted based on their events and articles.

**Components**:
- `PersecutionClassifier`: Protocol for classification strategies
- `RuleBasedPersecutionClassifier`: Rule-based implementation using charges, keywords, and event patterns
- `PersecutionClassificationService`: Orchestrates classification

**CLI**:
```bash
# Classify all persons
court-monitor classify-persecution

# Classify specific person
court-monitor classify-persecution --person-id 123
```

**Database Tables**:
- `persecution_classifications`: Classification results

**Evidence Types**:
- Political charges (articles 280, 282, 207.3, etc.)
- Political keywords in article text
- Event patterns (arrests, charges, sentences)

### 5. Rosfinmonitoring Ingestion

**Purpose**: Import Rosfinmonitoring terrorist/extremist list as versioned snapshots.

**Components**:
- `RosfinmonitoringParser`: Parses XML/CSV/JSON formats
- `RosfinmonitoringPersistence`: Stores snapshots and entries
- `RosfinmonitoringIngestionPipeline`: Orchestrates import

**CLI**:
```bash
# Import from file
court-monitor import-rosfinmonitoring --file rosfin.xml

# Import from URL
court-monitor import-rosfinmonitoring --url https://rosfinmonitoring.gov.ru/list
```

**Database Tables**:
- `rosfinmonitoring_snapshots`: Snapshot metadata
- `rosfinmonitoring_entries`: Individual entries

**Key Features**:
- Immutable snapshots (no modification after creation)
- Content hash deduplication
- Multiple format support (XML, CSV, JSON)
- Provenance tracking

### 6. Person ↔ Rosfinmonitoring Matching

**Purpose**: Match canonical persons against Rosfinmonitoring entries.

**Components**:
- `RosfinmonitoringMatcher`: Protocol for matching strategies
- `RuleBasedRosfinmonitoringMatcher`: Rule-based implementation using matching keys and name similarity
- `RosfinMatchPersistence`: Stores match results

**CLI**:
```bash
# Match all persons
court-monitor match-rosfinmonitoring --snapshot-id 5

# Match specific person
court-monitor match-rosfinmonitoring --snapshot-id 5 --person-id 123
```

**Database Tables**:
- `rosfin_matches`: Match results

**Matching Algorithm**:
1. Exact matching_key match (highest confidence)
2. Name similarity using Jaccard coefficient
3. Birth date comparison (if available)
4. Status: matched / ambiguous / needs_review / not_matched

### 7. Candidate Query

**Purpose**: Identify persons who are politically persecuted but NOT in Rosfinmonitoring list.

**Components**:
- `CandidateQueryService`: Main product query implementation

**CLI**:
```bash
# List candidates
court-monitor list-candidates --snapshot-id 5

# Filter by confidence
court-monitor list-candidates --snapshot-id 5 --min-confidence 0.8

# Output to file
court-monitor list-candidates --snapshot-id 5 --output-path candidates.json
```

**Query Logic**:
1. Find all persons whose **latest** persecution classification (by `classified_at`, then `id`) is political — an older political record superseded by a newer classifier version does not count
2. Filter by minimum confidence threshold
3. Check Rosfinmonitoring match status
4. Return only those with a **confirmed** `not_matched` result by default —
   `no_match_record` (matching never ran), `ambiguous`, `needs_review` and
   `insufficient_data` are excluded, since none of them mean "absent"

### 8. Research

**Purpose**: Deterministic structured research over canonical persons — the stable backend for CLI, API and the future natural-language (LangGraph) adapter.

**Components**:
- `ResearchService`: executes `ResearchRequest` → `ResearchResponse`
- Reuses `CandidateQueryService` for `political` + Rosfinmonitoring status queries

**CLI / API**:
```bash
court-monitor research --persecution-status political --rosfin-status not_matched --snapshot-id 5
curl -X POST http://localhost:8000/research -H 'Content-Type: application/json' \
  -d '{"object_type": "person", "criteria": {"persecution_status": "political", "rosfinmonitoring_status": "not_matched", "snapshot_id": 5}}'
```

See [Research](Research.md) and [ADR 0008](../adr/0008-research-domain-and-research-service.md).

## Running the Full Pipeline

### Option 1: Manual Execution

Run each stage separately:

```bash
# 1. Ingest articles
court-monitor discover-and-ingest --source ovd-info --limit 100

# 2. Extract entities and events
court-monitor extract-entities

# 3. Resolve persons
court-monitor resolve-people

# 4. Classify persecution
court-monitor classify-persecution

# 5. Import Rosfinmonitoring (if not already imported)
court-monitor import-rosfinmonitoring --file rosfin.xml

# 6. Match persons to Rosfinmonitoring
court-monitor match-rosfinmonitoring --snapshot-id 1

# 7. List candidates
court-monitor list-candidates --snapshot-id 1
```

### Option 2: API

Use the FastAPI endpoints:

```bash
# Start API server
uvicorn --app-dir src api:app --host 0.0.0.0 --port 8000

# Ingest articles
curl -X POST http://localhost:8000/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"source": "ovd-info", "limit": 100}'

# Extract entities
curl -X POST http://localhost:8000/api/extract

# Resolve persons
curl -X POST http://localhost:8000/api/resolve

# Classify persecution
curl -X POST http://localhost:8000/api/classify

# Import Rosfinmonitoring
curl -X POST http://localhost:8000/api/rosfinmonitoring/import \
  -F "file=@rosfin.xml"

# Match persons
curl -X POST http://localhost:8000/api/match?snapshot_id=1

# Get candidates
curl http://localhost:8000/api/candidates?snapshot_id=1
```

## Idempotency

All pipeline stages are idempotent:

- **Ingestion**: Re-fetching same article produces same result
- **Extraction**: Re-running produces same mentions and events
- **Resolution**: Re-resolving links to same persons (no duplicates)
- **Classification**: Re-classifying produces same result
- **Matching**: Re-matching produces same result
- **Query**: Query is read-only, always same result for same data

## Error Handling

Each stage handles errors gracefully:

- **Ingestion**: Failed fetches are logged, other articles continue
- **Extraction**: Invalid articles are skipped, extraction continues
- **Resolution**: Resolution errors are logged, other mentions continue
- **Classification**: Classification errors are logged, other persons continue
- **Matching**: Matching errors are logged, other persons continue

## Monitoring

### Metrics

Track pipeline health:

```python
# Articles processed
SELECT COUNT(*) FROM parsed_articles;

# Extraction runs
SELECT COUNT(*) FROM article_extraction_runs WHERE status = 'succeeded';

# Persons resolved
SELECT COUNT(*) FROM persons;

# Persecution classifications
SELECT status, COUNT(*) FROM persecution_classifications GROUP BY status;

# Rosfinmonitoring matches
SELECT status, COUNT(*) FROM rosfin_matches GROUP BY status;

# Candidates
SELECT COUNT(*) FROM persecution_classifications pc
WHERE pc.status = 'political'
AND NOT EXISTS (
    SELECT 1 FROM rosfin_matches rm
    WHERE rm.person_id = pc.person_id
    AND rm.status = 'matched'
);
```

### Logs

All pipeline stages log their progress:

```
2026-09-13 15:30:00 INFO  Starting ingestion for source=ovd-info
2026-09-13 15:30:05 INFO  Ingested 100 articles (5 failed)
2026-09-13 15:30:10 INFO  Starting extraction for 100 articles
2026-09-13 15:30:30 INFO  Extracted 450 mentions, 120 events
2026-09-13 15:30:35 INFO  Starting person resolution
2026-09-13 15:30:40 INFO  Resolved 150 persons, created 45 new
2026-09-13 15:30:45 INFO  Starting persecution classification
2026-09-13 15:30:50 INFO  Classified 150 persons (45 political)
2026-09-13 15:30:55 INFO  Starting Rosfinmonitoring matching
2026-09-13 15:31:00 INFO  Matched 150 persons (30 matched, 120 not matched)
2026-09-13 15:31:05 INFO  Query returned 40 candidates
```

## Performance

### Benchmarks

Typical performance on modern hardware:

- **Ingestion**: ~10 articles/second (network-bound)
- **Extraction**: ~50 articles/second (CPU-bound)
- **Resolution**: ~100 mentions/second (database-bound)
- **Classification**: ~200 persons/second (CPU-bound)
- **Matching**: ~500 persons/second (database-bound)

### Optimization

- **Batching**: Process articles in batches to reduce database round-trips
- **Indexes**: Key fields are indexed for fast lookups
- **Connection pooling**: Database connections are pooled
- **Caching**: Frequently accessed data is cached

## Testing

### Unit Tests

Each component has comprehensive unit tests:

```bash
uv run pytest tests/ -v
```

### Integration Tests

End-to-end tests verify the full pipeline:

```bash
uv run pytest tests/app/test_end_to_end.py -v
```

### Evaluation

Evaluate quality of each stage:

```bash
# Extraction quality
uv run pytest tests/extraction/test_extraction_metrics.py -v

# Person resolution quality
uv run pytest tests/persons/test_er_evaluation.py -v

# Persecution classification quality
uv run pytest tests/persecution/test_persecution_evaluation.py -v

# Rosfinmonitoring matching quality
uv run pytest tests/rosfinmonitoring/test_rosfin_match_evaluation.py -v
```
