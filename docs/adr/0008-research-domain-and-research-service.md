# ADR 0008: Research Domain and Research Service

## Status

Accepted

## Context

The pipeline already produces canonical persons, their resolved mentions and
linked events, persecution classifications and Rosfinmonitoring match results.
Business questions on top of that data were hard-wired into individual entry
points: `list-candidates` / `GET /candidates` answer exactly one question
("politically persecuted AND confirmed absent from a snapshot") and nothing else.

The next project stage puts a natural-language interface (LangGraph + LLM) in
front of the data. It needs a stable, typed, deterministic backend to call — one
whose answers do not depend on how a model phrases a query, and whose semantics
(especially "not in Rosfinmonitoring") cannot drift away from the existing
product query.

## Decision

Add a research layer:

```text
CLI (research) ─┐
                ├─> ResearchRequest ─> ResearchService.execute() ─> ResearchResponse
POST /research ─┘                          │
                     ┌─────────────────────┼──────────────────────┐
                     ▼                     ▼                      ▼
            CandidateQueryService   PersonResearchRepository   research_mapping
            (POLITICAL + RF status) (SQLAlchemy read model)    (warnings, provenance)
                     └─────────────────────┴──────────────────────┘
                                           ▼
                                      PostgreSQL
```

- `research_models.py` — `ResearchObjectType`, `ResearchRequest`,
  `PersonResearchCriteria` (strict Pydantic, `extra="forbid"`) and the
  normalized result (`PersonResearchResult`, `ResearchEvent`, `ResearchEvidence`,
  `ResearchSource`, `ResearchRosfinmonitoring`, `ResearchWarning`).
- `research_service.py` — `ResearchService.execute(request) -> ResearchResponse`
  plus the ports it depends on (`PersonResearchRepository`, `CandidateQuery`).
- `research_repository.py` — PostgreSQL implementation of the repository port.
- `research_mapping.py` — pure record → result mapping and review warnings.
- `research_cli.py`, `api.py` — thin adapters.

### 1. The research object is Person, not Article

Researchers ask about people: who is persecuted, what happened to them, are they
on the Rosfinmonitoring list. An article is one of many observations about a
person; returning articles would push entity resolution back onto the caller and
would repeat the same person once per article. The canonical `Person` (after
resolution and merges) is therefore the unit of a result. Articles never appear
as results and `ArticleChunk` is not reintroduced.

### 2. Documents are evidence/provenance

Every fact in a result can be traced back to text:

```text
Person ─> entity_mentions.person_id ─> article_extraction_runs ─> parsed_articles ─> source_documents ─> sources
Person ─> person_event_links ─> extracted_events ─> (same chain)
```

`ResearchEvidence` carries the extraction run, the article id, the existing
`start_offset`/`end_offset` and only the span text (never the full article).
`ResearchSource` lists each contributing article once (title, source name,
canonical URL, published date); evidence and events reference it by
`article_id`. Evidence is collected strictly through this person's resolved
mentions and person-event links — co-occurrence in the same article is not
evidence.

### 3. ResearchService is deterministic

Same request + same database state = same response. Filters are exact
(equality, thresholds, inclusive UTC date bounds, escaped `ILIKE` substring),
results are ordered by `person.id`, the response echoes the validated request and
contains no generation timestamp. There is no fuzzy or semantic interpretation.
Unknown criteria (e.g. `region`) are rejected with a validation error rather than
silently ignored.

Business rules are reused, not re-derived:

- `persecution_status=POLITICAL` + `rosfinmonitoring_status` is delegated to
  `CandidateQueryService.get_candidates(include_rf_statuses={status}, limit=None)`.
  Its result is intersected with the other criteria.
- The match-record → `RosfinmonitoringStatus` mapping moved to
  `candidate_query_models.resolve_rosfinmonitoring_status` and is used by both the
  candidate query and research. `NOT_MATCHED` is the only confirmed absence;
  `NO_MATCH_RECORD`, `AMBIGUOUS`, `NEEDS_REVIEW`, `INSUFFICIENT_DATA` never mean
  "not in Rosfinmonitoring".
- `DEFAULT_MIN_PERSECUTION_CONFIDENCE` (0.7) is shared: POLITICAL without an
  explicit threshold means the same thing in research and in `list-candidates`.
- "The person's classification" is the latest one (`classified_at` desc, then
  `id` desc), defined once in
  `candidate_query_service.latest_persecution_classification_ids()` and used by
  both the candidate query and the research repository. An older POLITICAL
  record superseded by a newer classifier version does not count.
- Persecution classification is read, never recomputed.

`review_required` is derived from `warnings` (a computed field, so it cannot
disagree with them). It is true for UNCERTAIN / NEEDS_REVIEW classifications and
AMBIGUOUS / NEEDS_REVIEW / INSUFFICIENT_DATA match statuses. Missing pipeline
output (no classification, `NO_MATCH_RECORD`) yields a warning with
`requires_review=false`: the fix is to run the classifier/matcher, not a human
decision. The `review_records` table is not consulted: the pipeline does not
create review records and there is no convention linking `subject_id` to a person.

### 4. The LLM sits above the service

Natural-language intake is a translation problem: text → `ResearchRequest`. Once
the request exists, answering it must not involve a model. Keeping the LLM above
the service means every answer is reproducible and testable, the validated
request is an audit trail of what was actually asked, and a mistranslation is
visible in `response.request` instead of being hidden inside a generated answer.

### 5. No dependency on LangGraph, Qdrant or transports

`ResearchService` imports only domain models and its two ports. It does not know
about HTTP, CLI formatting, LangGraph, LLM providers or vector stores. LangGraph
becomes one more adapter (like the CLI and FastAPI) that builds a
`ResearchRequest`; swapping or removing it requires no change to the service.
Semantic retrieval is out of scope (dense search was removed in ADR 0002); if it
returns, it must feed deterministic criteria (e.g. person ids), not replace them.

### 6. Adding Event/Case research objects later

1. Add the enum member (`ResearchObjectType.EVENT`) only when the data model can
   answer it (Case requires a case entity that does not exist yet).
2. Add `EventResearchCriteria` and `EventResearchResult`. `PersonResearchResult`
   already carries `object_type`, so `results` can become a discriminated union
   on it. `criteria` has no discriminator of its own: select its variant from
   the request-level `object_type` (a `mode="before"` validator on
   `ResearchRequest`), so existing person request JSON stays valid unchanged.
3. Add a repository port for the new object and dispatch on `object_type` inside
   `execute()`; reuse `ResearchEvidence`/`ResearchSource` for provenance.

## Consequences

- One entry point for structured research shared by CLI, API and the future
  LangGraph adapter.
- `CandidateQueryService.get_candidates` accepts `limit=None` (no limit); default
  behaviour of existing callers is unchanged.
- `CandidateQueryService` (and therefore `list-candidates` / `GET /candidates`)
  now considers only each person's latest classification. Previously any
  historical POLITICAL record qualified, which could list a person whose current
  classification is not political and returned duplicates per classifier version.
- Repository calls use separate read-only sessions; a person removed between
  filtering and loading details is skipped (still counted in `total_matched`).
- Known data-model gaps (documented in `docs/wiki/Research.md`): no region/city,
  court or organization filters (not linked to persons); no evidence spans behind classification reasons; name
  filter does not fold ё/е; only `active` persons are searchable.
