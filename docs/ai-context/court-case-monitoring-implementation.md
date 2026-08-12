# Court Case Monitoring Implementation Summary

## Overview

This document summarizes the implementation of court case monitoring functionality for the court-monitor project. The system now supports automated crawling of court press releases, parsing of case cards, and explainable matching between press releases and actual court cases.

## Architecture

### 1. Press Release Crawler (`src/court_monitor/sources/sudrf.py`)

**SudrfPressCrawler** - Main crawler for sudrf.ru courts:
- Walks monthly archive pages (`/modules.php?name=press_dep&op=12&arc_list=YYYY-MM`)
- Extracts publication links from archives
- Fetches individual press releases
- Returns `FetchResult` with metadata (title, published_at, external_id)
- Supports incremental fetch via `known_ids` (watermark pattern)
- Supports `full_rescan` mode to re-fetch all documents

**Key Features:**
- Incremental fetching: skips already-known documents by `external_id`
- Dual backend: `fixture` (tests) and `http` (production)
- Proper encoding handling (windows-1251 → UTF-8)
- Rate limiting and polite HTTP via `HttpClient`

### 2. Case Card Parser (`src/court_monitor/parsers/sud_delo.py`)

**parse_case_card()** - Parses sud_delo case card HTML:
- Extracts case number, UID, court, dates
- Parses judge information
- Extracts persons with articles, materials, results
- Parses case movement events (hearings, decisions)
- Handles hidden person information ("Информация скрыта")

**Data Models:**
- `ParsedCaseCard` - Main case card structure
- `CasePerson` - Person involved in case
- `CaseEvent` - Event in case lifecycle

### 3. Database Models (`src/court_monitor/storage/orm.py`)

**Case** - Court case:
- `court`, `case_number`, `case_uid`
- `instance_type`, `status`
- `received_at`, `decision_at`
- `judge`, `first_instance_court`, etc.

**PersonCase** - Links person to case:
- `person_id`, `case_id`
- `role`, `articles`, `material`, `result`
- `confidence`, `verified_by`

**CourtEvent** - Case lifecycle event:
- `case_id`, `event_type`
- `event_date`, `event_time`
- `result`, `location`
- `source_document_id` (links to press release)

### 4. Case Search Service (`src/court_monitor/services/case_search.py`)

**search_cases()** - Searches cases by criteria:
- `CaseSearchCriteria` - Search parameters (court, article, date, person_name)
- Returns `CaseCandidate` with score and explainable reasons
- Weights: article (0.40), date (0.30), court (0.20), person_name (0.10)
- Date tolerance: ±7 days

### 5. Explainable Matching (`src/court_monitor/matching/case_matching.py`)

**match_press_release_to_case()** - Matches press release to case card:
- Returns `CaseMatchResult` with confidence score
- Provides `signals` (positive matches) and `missing` (absent fields)
- Handles hidden person information gracefully
- Article normalization (extracts number from "ст.205.1 ч.1 УК РФ")

**Weights:**
- Article: 0.35
- Date: 0.30
- Court: 0.20
- Person name: 0.15

## Live Discovery Results

### Verified URLs (2026-08-12)

**2-й Западный окружной военный суд (2ЗОВС):**
- Base: `https://2zovs.msk.sudrf.ru`
- Press releases: `/modules.php?name=press_dep&op=1`
- Archive: `/modules.php?name=press_dep&op=12&arc_list=YYYY-MM`
- Case search: `/modules.php?name=sud_delo`

**Южный окружной военный суд (ЮОВС):**
- Base: `https://yovs.ros.sudrf.ru`
- Same structure as 2ЗОВС

### Previous Documentation Errors

The old documentation claimed courts were unavailable because it used incorrect URLs:
- ❌ `https://2zovs.sudrf.ru` (404)
- ❌ `https://uovs.sudrf.ru` (404)

Correct URLs include regional subdomain:
- ✅ `https://2zovs.msk.sudrf.ru`
- ✅ `https://yovs.ros.sudrf.ru`

## Test Coverage

### Unit Tests

1. **test_sudrf_press_crawler.py** (11 tests)
   - Archive parsing
   - Publication link extraction
   - Incremental fetch
   - Full rescan mode

2. **test_sud_delo_parser.py** (4 tests)
   - Real case card parsing
   - Minimal HTML parsing
   - Multiple articles
   - Hidden person handling

3. **test_case_matching.py** (9 tests)
   - Match by article
   - Match by date (with tolerance)
   - Match by person name
   - Combined signals
   - Hidden person handling
   - No match scenario
   - Article normalization

### Integration Tests

1. **test_sudrf_crawler_pipeline.py** (3 tests)
   - Full crawler → ingest → parse → extract flow
   - Incremental fetch behavior
   - Known external_ids tracking

2. **test_press_to_case_pipeline.py** (6 tests)
   - Press release extraction
   - Case search by article
   - Case search by date
   - End-to-end matching
   - Hidden person matching
   - No match scenario

**Total: 472 tests passing**

## Configuration

### sources.yaml

```yaml
sources:
  - name: "2zovs"
    type: "sudrf"
    backend: "fixture"  # or "http" for production
    base_url: "https://2zovs.msk.sudrf.ru"
    paths:
      - "/modules.php?name=press_dep&op=1"
    fixture_path: "tests/fixtures/sudrf-live/2zovs/press"
    parser: "sudrf_press"
    enabled: true
    court_name: "2-й Западный окружной военный суд"
    court_region: "Москва"
    press_module: "press_dep"
    case_module: "sud_delo"

  - name: "yovs"
    type: "sudrf"
    backend: "fixture"
    base_url: "https://yovs.ros.sudrf.ru"
    paths:
      - "/modules.php?name=press_dep&op=1"
    fixture_path: "tests/fixtures/sudrf-live/yovs/press"
    parser: "sudrf_press"
    enabled: true
    court_name: "Южный окружной военный суд"
    court_region: "Ростов-на-Дону"
    press_module: "press_dep"
    case_module: "sud_delo"
```

## Fixtures

Real HTML fixtures saved in `tests/fixtures/sudrf-live/`:

```
sudrf-live/
├── 2zovs/
│   ├── press/
│   │   ├── archive-2026-04.html
│   │   ├── release-234-razlugo.html
│   │   ├── release-235-voronezh-architect.html
│   │   ├── release-233-bpla-operator.html
│   │   └── release-231-kaluga-arsonists.html
│   └── sud_delo/
│       ├── search-form.html
│       └── case-card-example.html
└── yovs/
    └── press/
        └── latest-news.html
```

## Database Migration

Migration `0014_add_case_personcase_courtevent.py` adds:
- `cases` table with indexes on court, case_number, case_uid
- `person_cases` table with foreign keys to persons and cases
- `court_events` table with foreign key to cases

## Usage Example

```python
from court_monitor.sources.sudrf import SudrfPressCrawler
from court_monitor.parsers.sud_delo import parse_case_card
from court_monitor.matching.case_matching import match_press_release_to_case
from court_monitor.services.case_search import search_cases, CaseSearchCriteria

# 1. Crawl press releases
crawler = SudrfPressCrawler(config, known_ids=known_ids)
for result in crawler.fetch_new():
    # Process press release
    pass

# 2. Parse case card
case_card = parse_case_card(html, case_uid="...")

# 3. Search for matching cases
criteria = CaseSearchCriteria(
    article="205.1", decision_date=date(2026, 4, 2), court="2-й Западный окружной военный суд"
)
candidates = search_cases(session, criteria)

# 4. Match press release to case card
result = match_press_release_to_case(
    article="205.1",
    decision_date=date(2026, 4, 2),
    court="2-й Западный окружной военный суд",
    person_name="Разлуго Виталий Викторович",
    case_card=case_card,
)

print(f"Confidence: {result.confidence}")
for signal in result.signals:
    print(f"  ✓ {signal.description}")
for missing in result.missing:
    print(f"  ⚠ {missing.description}")
```

## Known Limitations

1. **Person name matching** - Currently simplified, needs integration with PersonRecord table
2. **Case card crawling** - Not yet implemented, requires form submission to sud_delo
3. **CLI integration** - `find-case` command not yet added
4. **Web UI** - Case matching results not yet displayed in operator interface

## Next Steps

1. Implement case card crawler (automate sud_delo form submission)
2. Add CLI command `find-case` for manual case search
3. Integrate case matching into web UI review queue
4. Add case tracking (monitor case lifecycle changes)
5. Implement person name matching via PersonRecord integration

## Files Changed

### New Files
- `src/court_monitor/parsers/sud_delo.py`
- `src/court_monitor/services/case_search.py`
- `src/court_monitor/matching/case_matching.py`
- `tests/unit/test_sudrf_press_crawler.py`
- `tests/unit/test_sud_delo_parser.py`
- `tests/unit/test_case_matching.py`
- `tests/integration/test_sudrf_crawler_pipeline.py`
- `tests/integration/test_press_to_case_pipeline.py`
- `migrations/versions/0014_add_case_personcase_courtevent.py`
- `docs/sudrf-live-discovery.md`

### Modified Files
- `src/court_monitor/sources/sudrf.py` - Added SudrfPressCrawler
- `src/court_monitor/parsers/sudrf_press.py` - Updated selectors for real HTML
- `src/court_monitor/storage/orm.py` - Added Case, PersonCase, CourtEvent models
- `src/court_monitor/config/loader.py` - Extended SourceConfig with court metadata
- `src/court_monitor/services/__init__.py` - Integrated crawler into pipeline
- `src/court_monitor/cli/app.py` - Added --full-rescan flag
- `config/sources.yaml` - Fixed URLs, added court metadata

## Verification

All checks passing:
- ✅ ruff check: All checks passed
- ✅ ruff format: 145 files already formatted
- ✅ mypy: Success, no issues found in 62 source files
- ✅ pytest: 472 passed, 1 skipped
