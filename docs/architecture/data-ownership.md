# Data ownership and typed package APIs

Status: **implementation in progress**, 2026-10-17, branch
`refactor/data-ownership-apis`. This is not an acceptance report. See
[inventory](data-ownership-inventory.md) and [ADR 0031](../adr/0031-data-ownership-and-package-apis.md).

Ownership means one package defines and mutates a table. It does **not** mean a
separate database or a microservice. All packages call Python APIs in-process;
PostgreSQL foreign keys and the shared SQLAlchemy Base stay in place.

```text
                         PostgreSQL
                             │
        ┌────────────────────┼───────────────────┐
        │                    │                   │
 ingestion owns       processing owns      analysis owns
        │                    │                   │
        └──────── public typed APIs ─────────────┘
                             │
                           results
                             │
                      orchestrator / app
```

## Complete table ownership map

`tests/test_data_ownership.py::OWNER_OF_TABLE` checks these 60 names against
`migrations/model_registry.py` metadata. Names and migration revisions are unchanged.

| Distribution | Owned tables |
|---|---|
| court-ingestion | sources, source_documents, parsed_articles |
| court-processing | article_extraction_runs, entity_mentions, extracted_events, event_entity_mentions, entity_groups, entity_group_mentions, entity_group_unnamed_mentions, entity_group_charges, entity_group_roles, entity_role_answers, entity_role_decisions, entity_name_overrides, entity_name_normalizations, entity_pair_decisions, entity_removals, entity_official_marks, article_sentences, article_sentence_readings, article_digest_answers, junk_screen_holds, excluded_persons, persons, person_aliases, person_merges, person_event_links, person_resolution_decisions, person_resolution_ai_reviews, review_records |
| court-analysis | entity_group_politics, entity_politics_answers, entity_politics_decisions, entity_group_news, entity_news_answers, entity_group_rf_matches, rosfinmonitoring_snapshots, rosfinmonitoring_entries, rosfin_matches, rfm_operator_entries, unnamed_figurants, unnamed_screened, unnamed_keeps, unnamed_answers, unnamed_decisions, unnamed_identity_resolutions, persecution_classifications, airtable_known_persons, criminal_articles |
| court-results | monitoring_findings, entity_done_marks, chat_questions |
| court-orchestrator | monitoring_runs, monitoring_run_items, source_monitoring_state, operator_operation_runs |
| court-investigation | investigation_runs |
| court-platform | app_settings, entity_model_spend |
| court-core | none |

Models live in each owner's `<namespace>/storage/models.py`; ER v2 models are in
`persons/storage/models.py` within court-processing. Platform retains only
`db/models/base.py` and `db/models/settings.py`. Migration composition is tooling,
not a production data API. No public all-model re-export replaces `db.orm_models`.

`excluded_persons` and `entity_group_unnamed_mentions` ownership is explained in
ADR 0031 rather than inferred from their names.

## Contracts implemented so far

These are intent-specific functions, not repositories exposing arbitrary SQL.
Readers return frozen DTOs, scalar values or collections, never mapped entities.
Legacy APIs outside this list have not all been converted yet.

| Owner / public modules | Read contracts | Write contracts | Allowed consumers |
|---|---|---|---|
| ingestion.read, ingestion.commands | article cards/texts, text windows, provenance/detail/existence, publication browsing, article counts | discard_articles, refresh_source_details (SourceDetails → SourceRefresh) | processing, analysis, results, orchestrator, app, evaluation |
| processing.read, processing.identity.service, processing.evidence.sentences, processing.roles.commands | Group, Role, Charge, evidence projections, lightweight references, role review queue, hidden sentences and counters | identity commands, role decisions, set_sentence_hidden, refresh_officials (OfficialDetails → OfficialRefresh) | analysis, results, orchestrator, app, evaluation |
| persons.read | PersonSummary, Alias, EventEvidence, ReviewSummary; unmerged IDs, bulk persons and review history | existing resolution services (migration in progress) | analysis, results, orchestrator, app, evaluation |
| analysis.read, analysis.rosfinmonitoring.read, analysis.politics.classification | verdicts/reasons/counts, RF statuses/snapshots/entries/matches/removals, PersonClassification, PersonListMatch | politics decisions and existing analysis commands | results, orchestrator, app, evaluation |
| results.read, results.review, results.done_marks, results.service | QuestionAnswer; composed politics review and EntryPersonLink; existing result views | mark_done and existing results commands | investigation (results.service only), orchestrator, app, evaluation |
| orchestrator.read, orchestrator.service | LiveOperation, latest run status, last success, stale-run count | existing monitoring/operation commands | app, evaluation |
| investigation.service | existing investigation results | run store (internal ORM) | app, evaluation |
| llm.model_choice | model selection / spend summaries | record_probe_spend, existing settings/spend commands | packages using shared model-selection infrastructure |

The finer approved import surfaces remain in `tests/test_module_boundaries.py`;
the table above does not broaden investigation or orchestrator import rules.

## Transactions

Owner commands accepting `Session` do not commit. The caller retains the original
transaction. In particular:

- Junk purge captures processing orphan IDs, calls ingestion.discard_articles for
  deletion/document tombstones, then cleans up processing orphans in the same session.
  Existing FK cascades still perform dependent deletes.
- Airtable sync prepares typed source/exclusion updates and calls each owner; the
  original per-list commit remains in Airtable sync, not in the owner command.
- Probe spend and the model's tested mark remain in one transaction.
- Sentence visibility and done marks are mutated by their owner and committed at
  the existing application boundary.
- IdentityDownstream is an upstream-owned Protocol. Analysis implements the downstream
  group-following effects; composition injects it without a reverse dependency.

## Bulk reads and performance status

The following are **static query shapes**, not PostgreSQL timings or measured page
totals. Owner APIs do not run one query per returned row.

| Converted read | Before | After |
|---|---|---|
| role review queue | one join | one owner join |
| politics review queue | one cross-owner join | one verdict query + one limited group-reference query |
| RF links for displayed entries | one cross-owner join | one match query + one selected-groups query |
| hidden sentences | one cross-owner join | one sentence query + one article-card query |
| person's event evidence | one cross-owner join | one event query + one bulk article query |
| article detail | one join | same join inside ingestion |

Empty batches skip downstream queries. Python slicing of event evidence is retained
exactly, including unusual persisted offsets. Review ordering, RF full-match priority
and SQL LIMIT/date filters are retained.

Still required: comparable SQL counts and wall time on the **same PostgreSQL DB** for
Work/queue, candidates, entities, RF, sentences and monitoring overview. No claim of
the ~20% acceptance threshold is made until those measurements exist.

## Remaining boundary debt (not approved exceptions)

The shrinking sets in `tests/test_data_ownership.py` currently record 51 foreign
storage import pairs and 130 foreign literal-SQL read pairs. There are zero detected
foreign raw-SQL write pairs, but that does not prove the absence of ORM attribute
mutations. These sets must reach zero (or a specific justified exception must be
documented and tested) before the task is complete.

Major remaining seams: entities/candidate/dossier/export queries, console reference
editing and work-cycle views, analysis pending/classification readers, results
facades, orchestrator aggregation, and evaluation internal imports. Integration
fixtures still import owner models directly and need classification/migration.
The lexical SQL scan deliberately is not a SQL parser and cannot prove arbitrary
dynamic SQL safe.

## Validation at this checkpoint

- Package-local tests: 478 passed. Focused architecture/consumer suite: 142 passed,
  55 skipped without PostgreSQL. New portable command/read tests cover rollback,
  immutability, matching and bounded query counts.
- Full suite was attempted but stalls in FastAPI TestClient; the same stall reproduces
  in a minimal independent FastAPI application in this sandbox. This is not a green
  full-suite result.
- No PostgreSQL service or usable Docker socket is available here. Clean upgrade,
  baseline/post-change alembic check, normalized pg_dump equality and page timings
  remain unverified. Migration revision files have not been edited.
- `ruff check .` and format check: clean (756 files). `mypy --strict`: clean
  (695 files). Architecture suite after final edits: 137 passed.
- Wheel isolation: all 10 distributions passed against installed wheels; 500 own
  tests passed in those isolated environments. One evaluation module requires the
  optional numpy extra and was skipped as declared.
- Frontend: 141 tests passed; TypeScript/Vite build passed (existing chunk-size warning).
- PostgreSQL-dialect compiled CREATE TABLE/INDEX DDL for all metadata is byte-identical
  to the main checkout's metadata (SHA-256
  `522973ae0aca33c4d7bdfa3c7ef811d126dbb4f36c2c36698ec7869ff1a721fd`).
  This is useful static evidence, **not** the required before/after pg_dump comparison.
- Docker build/smoke, DB integration and full HTTP suite remain outstanding; no
  earlier author evidence is treated as proof for these changes.
