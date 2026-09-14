# ADR 0010: Research Reports, Human Review Policy and Source Routing

## Status

Accepted

## Context

ADR 0008 gives a deterministic `ResearchService` that returns
`PersonResearchResult` objects with statuses, events, evidence spans and
sources. ADR 0009 puts a LangGraph workflow in front of it: natural language →
`ResearchRequest` → `ResearchService` → `ResearchQueryResult`.

What was missing for a usable research answer:

- a **verifiable report**: each statement tied to the evidence behind it;
- an explicit treatment of **uncertainty**: which results a human must check,
  and why, decided in one place;
- a **database-first plan**: whether the stored data is enough or a source
  refresh is worth recommending, without crawling on every query.

The constraint from ADR 0009 stays: an LLM must not become a source of facts,
and must not turn `UNCERTAIN` into `POLITICAL`, or `AMBIGUOUS` /
`INSUFFICIENT_DATA` / `NO_MATCH_RECORD` into `NOT_MATCHED`.

## Decision

```text
User query
   ↓
Request Intake (LLM, ADR 0009)
   ↓
ResearchRequest ── resolve_snapshot ── validate_request
   ↓
ResearchPlanner.plan          (deterministic)
   ↓
ResearchService.execute       (facts, ADR 0008)
   ↓
ResearchResponse              (kept unchanged)
   ↓
ResearchResultEvaluator       (ResearchReviewPolicy + ResearchPlanner.route)
   ↓
ResearchReportBuilder         (deterministic presentation)
   ↓
human_review_gate             (marks review; persists nothing)
   ↓
ResearchQueryResult { results, plan, report }
```

Graph topology (`src/research/workflow/graph.py`):

```text
START → request_intake → resolve_snapshot → validate_request
      → build_research_plan → research → evaluate_result → build_report
      → human_review_gate → END
(failure and clarification exits of ADR 0009 are unchanged; clarification
 never reaches build_research_plan or research)
```

### Facts vs. presentation

Facts are established by `ResearchService`, the persecution classifier, the
Rosfinmonitoring matcher, entity resolution and PostgreSQL. The report layer
(`src/research/reports/`) only presents them:

- `ResearchReportItem` copies status and confidence values from
  `PersonResearchResult`; it never recomputes them.
- `ResearchQueryResult.results` still carries the raw `PersonResearchResult`
  objects; `report` is added next to them, not instead of them.
  `POST /research` and `ResearchService` are unchanged.
- Texts are fixed templates per status. Only `NOT_MATCHED` is worded as
  "не найден в перечне"; `NO_MATCH_RECORD` says matching has not been run,
  `INSUFFICIENT_DATA` says data is insufficient for reliable matching, and
  `AMBIGUOUS`/`NEEDS_REVIEW` say neither presence nor absence is confirmed.
  Tests enforce that no other status reads as absence.
- `why_matched` is built only from the criteria that were actually applied
  (with the effective persecution threshold) next to the person's own values;
  unfiltered criteria never appear.
- No LLM is used after intake. There is no LLM-written summary.

### Claims and provenance

Every substantive statement is a `ResearchClaim` (`identity`,
`persecution_classification`, `rosfinmonitoring_status`, `event`) with its own
confidence, a `basis` and citations:

- `ResearchCitation` = existing `ResearchEvidence` span + the `ResearchSource`
  of its article (article id, source name, URL, published_at, evidence type,
  offsets, span text). There is no second provenance pipeline and no full
  article text in the report.
- Evidence whose article is missing from the result's sources is not citable
  (`evidence_without_source` warning).
- `basis=source_documents` claims (identity, classification, events) must have
  citations; `basis=rosfinmonitoring_snapshot` claims cite the snapshot id.
  A source-derived claim without citation is `supported=false`, produces a
  `claim_without_citation` warning and a `MISSING_EVIDENCE` review reason, so it
  is never presented as a confirmed source fact.
- Persecution `reasons` are strings without per-reason evidence. The classifier
  only sees the person's own mentions and events (ADR 0006), so the
  classification claim cites exactly those spans. Per-reason citations are not
  available in the current data model.
- Confidences stay separate (classification, match, event extraction); there is
  no aggregate confidence.

### Human review policy

`ResearchReviewPolicy.evaluate(request=, result=) -> ResearchReviewDecision` is
the only place with review conditions. Nodes, the builder and the review-task
service call it.

| Reason | Condition | Severity |
|---|---|---|
| `persecution_uncertain` | latest classification `UNCERTAIN` | blocking |
| `persecution_needs_review` | latest classification `NEEDS_REVIEW` | blocking |
| `rosfin_ambiguous` | match `AMBIGUOUS` | blocking |
| `rosfin_needs_review` | match `NEEDS_REVIEW` | blocking |
| `rosfin_insufficient_data` | match `INSUFFICIENT_DATA` | blocking |
| `missing_evidence` | no citable span for the person, or an event without its own span | blocking |
| `low_confidence` | `POLITICAL` below `DEFAULT_MIN_PERSECUTION_CONFIDENCE` (0.7) | advisory |

Not review reasons, but data gaps (report `partial`): no classification yet,
`NO_MATCH_RECORD` (run the matcher). `ENTITY_RESOLUTION_AMBIGUOUS` is not
defined: research returns active canonical persons only, so resolution
ambiguity is not visible in a result. The policy is a superset of
`PersonResearchResult.review_required` (tested over every status pair).

### Review required ≠ review record exists

A report only marks `review_required`. Nothing is written on read, so repeated
queries cannot create duplicates. A persistent task is an explicit action:
`POST /research/reviews` (`ResearchReviewTaskService`):

- input: `person_id`, `reason`, and `classification_id` (persecution reasons)
  or `snapshot_id` (Rosfinmonitoring reasons); unknown fields and free text are
  rejected; `missing_evidence` cannot be persisted (no stored subject);
- the condition is re-checked against current data with the same policy (the
  classification must be the person's latest; the match record must exist and
  still have the status) → 409 otherwise, 404 for a missing subject;
- reuses `review_records` / `ManualReviewService` with the existing subject
  types `persecution_classification` (classification id) and `rosfinmatch`
  (`rosfin_matches.id`); `reason` stores the reason code only;
- idempotent: partial unique index `uq_review_records_pending_subject` on
  `(subject_type, subject_id) WHERE decision = 'pending'` +
  `INSERT … ON CONFLICT DO NOTHING`; 201 when created, 200 with the existing
  pending review. A decided review does not block a new one. The response
  carries `requested_reason` and `stored_reason`: a re-run matcher updates the
  `rosfin_matches` row in place, so an existing pending review of the same
  subject may have been opened for another reason.

### Research planning and source routing (database first)

`ResearchPlanner` (`src/research/planning/`) is deterministic:

- `plan(request) -> ResearchPlan`: steps, data requirements of the applied
  criteria, and candidate sources. Candidates come from `sources.source_registry`
  (`SourceCapability`: registry id, `sources.name`, base URL,
  `supports_discovery`, `supports_direct_fetch`, data types); source names are
  never hard-coded in the graph. With `criteria.source`, only the source whose
  `source_name` equals it (the exact filter ResearchService applies) is a
  candidate; an unknown name yields none.
- `route(plan, response) -> SourceRoutingDecision` after the database search:
  refresh is recommended only when `total_matched == 0`, the request is not a
  `person_id` lookup, and a candidate supports discovery. Reasons:
  `database_sufficient`, `no_matches_in_database`, `refresh_cannot_help`,
  `no_compatible_source`.
- Freshness (`source_documents.fetched_at`) is not used: there is no agreed
  staleness threshold.

The report status reflects this: `complete`, `partial`, `review_required`
(outranks partial), `no_matches` (0 results, refresh would not help) and
`insufficient_data` (0 results in the current database, refresh recommended).
An empty result is therefore never presented as proof that nobody matches.

### Why routing does not run ingestion yet

`source_refresh_required=true` is a recommendation in the report
(`source_refresh_recommended`, `recommended_sources`), never an executed action.
Running discover → fetch → parse → extract → resolve → classify from a read
request would make a query slow, non-repeatable and side-effecting, would need
scheduling, rate limits and failure handling, and would still not update
Rosfinmonitoring matches. Automated monitoring is a separate, later part; the
routing decision is the contract it can consume.

## Consequences

- `build_research_graph()` requires a `planner`; the composition root
  (`research/workflow_factory.py`) builds `ResearchPlanner(SOURCES)`, so the
  graph module does not import `sources.source_registry`.
- New packages `research.reports` and `research.planning`; new module
  `research/review_tasks.py`; `SourceDefinition` gains
  `supports_discovery`/`supports_direct_fetch`.
- Migration `l6m7n8o9p0q1` adds the partial unique index on `review_records`.
  Before creating it, the migration checks for subjects with more than one
  pending review and stops with an actionable `RuntimeError` (count, example
  ids, inspection SQL) instead of a raw unique violation. Duplicates are not
  resolved automatically: which review to keep is a human decision.
- `ResearchQueryResult` gains `plan` and `report`; existing fields are unchanged.
  `review_required` is taken from the report when present, which can add
  `low_confidence` and `missing_evidence` to the domain review flags.
- CLI `ask` prints the report by default; `--raw` prints the previous
  `ResearchResponse` view, `--show-plan` prints the plan.
- New logs: `research_plan_built`, `result_evaluated`, `report_built`,
  `human_review_gate`.
- ADR 0009's `assemble_response` node is replaced by
  `evaluate_result → build_report → human_review_gate`.
