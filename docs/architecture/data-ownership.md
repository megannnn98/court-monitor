# Data ownership and typed package APIs

Status: **ownership implementation complete; final runtime validation pending**,
2026-10-18, branch `refactor/data-ownership-apis`. See
[inventory](data-ownership-inventory.md) and [ADR 0031](../adr/0031-data-ownership-and-package-apis.md).
Verification evidence and remaining acceptance gates are recorded below; green
architecture tests alone do not mean the whole task is accepted.

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

The physical database also retains legacy `rosfinmonitoring_checks` (migration
`a7b8c9d0e1f2`): logically analysis-owned, with no current ORM class or runtime reader/
writer. It was already absent from main's metadata. It is **not** registered as a
new model here: doing so would change the required baseline `alembic check` result.
`alembic_version` belongs to migration tooling, not a domain package.

## Public contracts

These are intent-specific functions, not repositories exposing arbitrary SQL.
Readers return DTOs, domain models, scalar values or collections, never mapped
entities across a package boundary. Package-local persistence may still use ORM.

| Owner / public modules | Read contracts | Write contracts | Allowed consumers |
|---|---|---|---|
| ingestion.read, ingestion.commands | article cards/texts, text windows, provenance/detail/existence, publication browsing, article counts | discard_articles, refresh_source_details (SourceDetails → SourceRefresh) | processing, analysis, results, orchestrator, app, evaluation |
| processing.read, processing.identity.service, processing.evidence.sentences, processing.roles.commands | Group, Role, Charge, evidence projections, lightweight references, role review queue, hidden sentences and counters | identity commands, role decisions, set_sentence_hidden, refresh_officials (OfficialDetails → OfficialRefresh) | analysis, results, orchestrator, app, evaluation |
| persons.read | PersonSummary, Alias, EventEvidence, ReviewSummary, PersonStats, MentionSpan, EventSpan, LatestEvent; bulk identities, evidence timestamps and review history | resolution/review services | analysis, results, orchestrator, app, evaluation |
| analysis.read, analysis.rosfinmonitoring.read, analysis.politics.classification | verdicts/reasons/counts, RF statuses/snapshots/entries/matches/removals, PersonClassification, PersonListMatch | politics decisions and existing analysis commands | results, orchestrator, app, evaluation |
| results.read, results.review, results.done_marks, results.service | QuestionAnswer; composed politics review and EntryPersonLink; existing result views | mark_done and existing results commands | investigation (results.service only), orchestrator, app, evaluation |
| orchestrator.read, orchestrator.service | LiveOperation, latest run status, last success, stale-run count | existing monitoring/operation commands | app, evaluation |
| investigation.service | existing investigation results | run store (internal ORM) | app, evaluation |
| llm.model_choice | model selection / spend summaries | record_probe_spend, existing settings/spend commands | packages using shared model-selection infrastructure |

The finer approved import surfaces remain in `tests/test_module_boundaries.py`;
the table above does not broaden investigation or orchestrator import rules.

Additional use-case surfaces include:

- `processing.roles.exclusion_list`: Official, ListedOfficial, Suggestion; listing,
  manual addition, activation changes and accepting suggestions, without exposing ORM.
- `processing.junk.hold_view`: held-publication views and funnel counters; ingestion
  supplies article/document facts through bulk readers.
- `processing.read`: FigurantProfile, FigurantMention, ShownSentence, EventFact,
  EventPerson, EventMention, ArticlePerson and filtered group/evidence projections.
- `analysis.read`: Figurant, Screened, NewsItem, VerdictCounts, politics/news/list
  maps and latest classification projections. RF snapshot/entry/removal projections
  live in `analysis.rosfinmonitoring.read`.
- `orchestrator.read`: live-operation facts, source errors and operation state;
  `orchestrator.runs` retains its run-summary/progress contract.
- `results.candidates.service`, `results.queries.*` and `results.service`: aggregate
  final-output DTOs over those upstream APIs, not joins to foreign ORM. Their
  `results.figurant` implementation stays internal. Entity-list filtering keeps
  owner-specific predicates in bulk APIs.

The app composes these readers and commands through its existing typed dependencies;
it owns no SQL mutation details or domain-table counts.

## Transactions

Commands migrated to a caller-supplied `Session` preserve that transaction and do not
introduce an extra commit. Existing higher-level services retain their original
transaction behavior. In particular:

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
  Identity merges and role withdrawal call analysis-owned mutations on the same
  session. String foreign keys and PostgreSQL cascades stay unchanged.

## Evaluation and testing APIs

`ingestion.testing`, `processing.testing`, `persons.testing`, `analysis.testing`,
`results.testing`, `orchestrator.testing` and `investigation.testing` expose explicit
fixture/measurement operations returning values, not a shared model aggregate.
Production may not import these modules; an architecture test enforces that rule.
Package-local persistence tests may still import their own storage. Cross-package
integration fixtures are tooling and may register metadata or use owner models;
they are not a production public surface.

Every owner lists its `DISPOSABLE_TABLES`. `evaluation.disposable.wipe` composes
the same 58 tables as before; platform's maintenance helper only knows its own two
settings tables and accepts the explicit table list. The database-name guard and
single TRUNCATE transaction are retained. The platform no longer names domain tables.

Five named evaluation database probes are the sole foreign SQL-read exceptions:

| Probe | Why it cannot use current-schema owner readers |
|---|---|
| identity_comparison/annotation.py | restored snapshots of potentially older schemas |
| identity_comparison/production.py | restored snapshots of potentially older schemas |
| identity_comparison/real.py | restored snapshots of potentially older schemas |
| real_world/state_snapshot.py | logical digest and invariants of the whole database |
| real_world/monitoring_simulation.py | whole-database row counts and SQL fault injection |

These probes import no foreign ORM and perform no direct foreign-table writes.
They are named with reasons in `tests/test_data_ownership.py::DATABASE_PROBES`, not
an expandable debt baseline. Ordinary evaluation business code uses public/testing APIs.

## Bulk reads and performance

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

Saved measurements against the same restored database (main vs refactor, from
`ebnv-own-baselines/perf_before_2.txt` and `perf_after_2.txt`):

| Page | SQL before → after | Median ms before → after | Time change |
|---|---|---|---|
| /ui/queue | 21 → 16 | 62.4 → 55.7 | −10.7% |
| /ui/candidates | 16 → 16 | 32.7 → 32.6 | −0.3% |
| /ui/entities | 26 → 25 | 55.8 → 56.7 | +1.6% |
| /ui/rfm | 19 → 20 | 102.9 → 101.7 | −1.2% |
| /ui/sentences | 15 → 15 | 31.6 → 31.2 | −1.3% |
| /ui/overview | 48 → 52 | 475.1 → 448.2 | −5.7% |

For APIs, `/api/v1/political` changes 11 → 22 statements and 310.2 → 268.4 ms;
`/api/v1/overview` changes 36 → 40 and 447.4 → 424.5 ms. These are fixed bulk query
sets, not per-row queries. Small endpoints remain noisy: sentences API is 3 → 3
queries and 3.5 → 5.4 ms (+1.9 ms); do not interpret that as a measured list-page N+1.
The six required UI pages show no >20% degradation in the saved sample. A second
saved after run is consistent, but these local timings are not a statistical load test.
They have not been remeasured in the current sandbox, which cannot reach the test DB.

## Boundary enforcement

No transitional storage/SQL debt lists remain. Tests require zero foreign storage
imports, zero foreign raw writes and no foreign SQL reads outside the five named
tooling probes. They also verify complete modeled-table ownership, platform-only
infrastructure, no all-model aggregate, no production use of testing APIs, unchanged
layering and an acyclic package graph. The lexical SQL scan is deliberately not a SQL
parser: dynamic SQL and unannotated/dynamically returned values still need review.

## Validation evidence (2026-10-18)

Fresh checks on `df3ed04f` plus the working-tree classification-reader and Alembic
configuration fixes described in [the validation report](data-ownership-validation.md):

- `uv sync --frozen` passed using an offline writable copy of the local cache.
- Ruff check and format passed (767 Python files); strict mypy passed (706 files).
- Ownership, module boundaries, package graph and analysis package tests: 250 passed
  (141 architecture/ownership and 109 analysis cases).
- Wheel isolation: all 10 distributions passed; 504 own tests passed against installed
  wheels. One optional evaluation module was skipped for the semantic numpy extra.
- Frontend: 141 tests passed and TypeScript/Vite build passed (chunk-size warning).
- OpenAPI and frontend API client regenerated without a tracked diff.
- Current PostgreSQL compiled metadata exactly matches the saved main baseline:
  SHA-256 `6d2976b53f423a91e86e3f1bf6cbdac8dbbca569eab7eac19b9ae1a30bcda145`.
- Saved normalized schema dumps before/after are identical:
  SHA-256 `f07f4a94869e546764a87358d074ddac8e7f4c0da1377c14abeccbe7d27e7ac5`.
  Only nondeterministic pg_dump restrict tokens may be normalized. Migration revision
  files have no diff against main.
- Saved `alembic check` outputs are identical, not clean: pre-existing drift includes
  legacy rosfinmonitoring_checks, nullable/index and constraint differences already
  present on main. No migration is added to fix that unrelated drift.
- Saved oracle comparisons reconfirmed zero differences: 178 GET cases, 208 entity
  filter combinations, 47 extra views, 32 holds/funnel cases. The comparator skips
  dynamic `/api/v1/about` and `/api/v1/entity-model`; XLSX timestamps/container metadata
  and the HTML about-page minute are normalized. Golden evaluation output is identical.

Still required for final acceptance: a successful full pytest run on the current
code (with PostgreSQL) and Docker build/entry/health smoke.
The previous full DB run had 3336 passed and two failures subsequently fixed, so it
is not reported as green. In this sandbox the Docker socket and `127.0.0.1:5442` are
inaccessible; no production DB or service was touched.
