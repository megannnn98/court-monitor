# Data-ownership implementation and validation report

2026-10-18. Branch `refactor/data-ownership-apis`, base `main` (`edfe1d3`), committed
HEAD `df3ed04f`, plus the working-tree fixes below. **Not yet accepted:** full current
DB pytest and Docker build/smoke remain outstanding. Nothing has been pushed/merged.

## Implementation (task report items 1–11)

1. **Table mapping.** The [complete ownership map](data-ownership.md#complete-table-ownership-map)
   assigns all 60 modeled tables to owners. Legacy migration-only
   `rosfinmonitoring_checks` is analysis-owned without introducing a new ORM class;
   `alembic_version` is migration infrastructure. The exceptions for excluded_persons
   and entity_group_unnamed_mentions are decided in ADR 0031.
2. **ORM relocation.** `db.models.sources` was split between ingestion (source,
   document, article), processing (sentences/digest) and results (chat questions).
   `entities` was split across processing, analysis and results. `extraction` moved
   to processing; `persons` to persons (same distribution); `monitoring` split across
   orchestrator, processing (holds) and results (findings); `operations` to orchestrator;
   `persecution`/`rosfinmonitoring` to analysis; `reference` to analysis and processing
   (exclusions); `investigation` to investigation. Each uses its owner's storage/models.py.
3. **Platform.** ORM files are only base.py, settings.py and __init__.py. Engine/session,
   pool settings, advisory lock/maintenance/array helpers, LLM and observability remain.
   Platform declares no domain tables, even in disposable reset lists.
4. **Direct imports.** `db.orm_models` and the central re-export are removed.
   Production foreign-storage imports, including app and evaluation, are zero under
   the ownership scanner. Alembic composes metadata in migrations/model_registry.py.
5. **Read/write APIs.** Ingestion read/commands, processing.read and identity/role/
   exclusion/sentence/hold commands, persons.read and resolution services, analysis.read
   and RF read/decision APIs, results read/review/service/queries, orchestrator.read
   and investigation's run store are detailed in data-ownership.md. No arbitrary-SQL
   or generic query-anything service was introduced.
6. **DTOs.** Representative contracts: ArticleCard/StoredArticle/ArticleDetail,
   Group/Role/Charge/FigurantProfile/FigurantMention/ShownSentence/EventFact,
   PersonSummary/PersonStats/MentionSpan/EventSpan/ReviewSummary,
   Politics/CaseNews/PersonClassification/PersonMatch/Figurant/Screened/VerdictCounts,
   RF snapshot/entry/match/removal values, QuestionAnswer/EntryPersonLink and
   LiveOperation/OperationState/SourceError. DTOs stay with their owners, not in core.
7. **Cross-owner writes found.** Identity merge/role cleanup touched analysis state;
   processing junk purge deleted ingestion articles/documents; Airtable sync modified
   ingestion sources and processing exclusions; UI edited domain visibility, done
   marks and reference/role decisions; model probe wrote platform spend records.
8. **Write replacements.** Analysis implements the processing-owned IdentityDownstream
   Protocol; composition injects it. Other mutations use owner commands/services on
   the original Session, with existing commit boundaries. FK cascades remain as before.
   Probe spend is recorded by llm.model_choice. Disposable resets are composed in
   evaluation from owner-declared lists and still use one guarded TRUNCATE transaction.
9. **Cross-owner reads found.** The frozen pre-refactor inventory recorded 478 references
   from 100 production files: domain evidence, derived analysis, console lists/details/
   health/counts, result composition, orchestrator counters and evaluation fixtures.
10. **Read replacements.** Intent-specific bulk owner readers supply values to app/
    results/domain consumers. Evaluation uses public or explicit owner testing APIs.
    Five old-schema/whole-database probes are documented SQL-read exceptions, not
    permission for evaluation business code to query any domain table.
11. **Joins and transactions.** Publication evidence is fetched in bulk by article IDs;
    entities/candidates/results combine owner projections, not one read per row.
    RF links use match + selected-group reads, review uses reasons + ordered references,
    sentences use sentence + article projections. Local SQL filtering/order/LIMIT stays
    in the owner where needed. Shared Session remains the explicit transaction contract.

## Performance (item 12)

See the six-page query-count/time table in [data-ownership.md](data-ownership.md#bulk-reads-and-performance).
Saved main → refactor measurements on the same restored DB show:

- Work queue: 21 → 16 SQL, 62.4 → 55.7 ms.
- Candidates: 16 → 16, 32.7 → 32.6 ms.
- Entities: 26 → 25, 55.8 → 56.7 ms.
- RF list: 19 → 20, 102.9 → 101.7 ms.
- Sentences: 15 → 15, 31.6 → 31.2 ms.
- Overview: 48 → 52, 475.1 → 448.2 ms.

No >20% UI page regression appears in that sample. Political API is explicitly
11 → 22 fixed bulk statements, 310.2 → 268.4 ms; overview API is 36 → 40,
447.4 → 424.5 ms. Neither growth is per returned row. Small absolute endpoint timing
noise is documented rather than hidden. These are saved measurements, not a fresh
benchmark at the current sandbox boundary.

## Verification (items 13–21)

| Item | Evidence | Status |
|---|---|---|
| 13. Schema | Saved normalized before/after pg_dump schema files identical; current compiled metadata also matches saved main | Verified artifacts; live rerun blocked |
| 14. Migrations | revisions unchanged; saved upgrade/check evidence, identical pre-existing check drift | Current live upgrade/check not rerun |
| 15. Pytest | 250 current selected tests passed (141 architecture/ownership + 109 analysis) | Full suite pending |
| 16. PostgreSQL integration | Prior full run: 3336 passed and two failures later fixed | No successful full current run; not green |
| 17. Strict mypy | 706 files, zero errors | Passed |
| 18. Ruff | check + format check, 767 files | Passed |
| 19. Package isolation | All 10 built wheels import in separate environments; 504 own tests passed | Passed; one declared optional semantic module skipped |
| 20. Frontend | 141 tests, TypeScript/Vite build; API generation leaves no tracked diff | Passed; existing bundle-size warning |
| 21. Docker | Socket connection denied in current sandbox | Build/entry/health smoke pending |

Saved artifact directory: `/home/b/Documents/ebnv-own-baselines/`. The schema hash is
`f07f4a94869e546764a87358d074ddac8e7f4c0da1377c14abeccbe7d27e7ac5`, metadata hash
`6d2976b53f423a91e86e3f1bf6cbdac8dbbca569eab7eac19b9ae1a30bcda145`.
Only nondeterministic restrict tokens are eligible for schema normalization. The saved
Alembic checks include pre-existing nullable/index/constraint differences as well as
rosfinmonitoring_checks; identical drift is not a clean check.

The saved oracle files compare equal: 178 GET cases, 208 entity filter combinations,
47 extra views, 32 holds/funnel cases, plus byte-identical golden output. The comparator
excludes the two dynamic API endpoints /api/v1/about and /api/v1/entity-model, normalizes
XLSX container/time metadata and the HTML about-page minute. These are differential
evidence, not a substitute for the pending full suite.

Current environment: TCP to test DB 127.0.0.1:5442 fails with EPERM; Docker socket
access fails. Production port 5433 was never used. Full pytest was not relaunched:
handoff records a prior PC crash and a rejected run; confirmation was requested.

## Final static-review fixes in this continuation

- `PersecutionClassificationService.get_person_classification` still returned a
  detached ORM entity. It now returns the existing PersonClassification DTO, including
  its timestamp and copied JSON lists. Classifier filter (including empty-string
  semantics) and classified_at ordering stay unchanged. The four portable regression
  tests failed before the fix and passed after it.
- Migration registry loading failed outside the checkout because Alembic prepended
  cwd rather than the config directory. `prepend_sys_path = %(here)s` fixes that
  tooling path. An isolated-Python regression test reproduced ModuleNotFoundError
  before the change and now loads every owner's metadata without a DB connection.
- Legacy rosfinmonitoring_checks is now covered by the ownership SQL guard while
  deliberately remaining absent from metadata, preserving baseline drift/schema.

No prompt, threshold, extraction/classification rule, cache key, REST path or migration
revision was modified in this continuation. Full runtime equivalence still has the
explicit validation gates above.

## Diff, commits and branch (items 22–24)

22. `git diff --shortstat main`: 270 tracked files, +10269/−5240. Including the three
    new report/context/test files: **273 files, +10550/−5240**. Use `git diff --stat main`
    for per-file tracked changes; it does not include untracked files.
23. Existing logical commits run from `bb543f17` (ORM ownership) through `df3ed04f`
    (last prior ER v2 consumer conversion), including the preserved original work and
    incremental owner/read/write/evaluation changes. `git log --oneline main..HEAD`
    is the source of truth; no history was rewritten. New fixes/docs are **not committed**:
    writing `/home/b/Documents/ebnv/.git/worktrees/ebnv-own/index.lock` fails because
    that metadata directory is mounted read-only, even though source files are writable.
24. Branch is `refactor/data-ownership-apis`. No push, merge or PR was made.

## A–D: remaining coupling

**A. Does any domain table still have two effective owners?** No known case in the
current code: models and commands have one owner; legacy table ownership is explicit.
Database FK cascades and coordinated shared transactions are not a second Python
writer. This is evidence from code/tests, not a claim that lexical scanning proves
arbitrary dynamic SQL impossible.

**B. Does production still know a foreign package's ORM?** No foreign-storage imports
are permitted/found. Converted public readers return values; the remaining detached
classification getter was fixed here. Owner-local persistence and migration composition
still know ORM, intentionally. No global model re-export replaces the old aggregate.

**C. Can a court package move into its own repository without separating its DB?**
Yes as a packaging arrangement: publish the distribution with its models and declared
upstream contracts, keep one shared PostgreSQL and migration composition. This does
not make its deployment/schema lifecycle independent or remove its workspace-derived
dependency versions. A repository boundary needs no HTTP/gRPC/database split.

**D. Three strongest coupling points:**

1. Shared relational schema, cross-owner foreign keys/cascades and one migration history.
2. Shared SQLAlchemy Session/transaction timing and locks for atomic coordinated changes.
3. Synchronous bulk DTO/identity/evidence contracts, whose ordering, snapshot consistency
   and query cost must evolve together across the DAG (including IdentityDownstream).

## Outstanding acceptance gates

With access restored, use **only disposable** test databases, never production :5433.
After explicit confirmation for the full run:

```sh
DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_ci@127.0.0.1:5442/court_monitor_test uv run alembic upgrade head
env -u DATABASE_URL TEST_DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_ci@127.0.0.1:5442/court_monitor_test uv run pytest -q -p no:cacheprovider
docker build -t court-monitor:ownership .
docker run --rm court-monitor:ownership python -I -c 'import api, dagster_location.definitions'
docker run --rm court-monitor:ownership court-monitor --help
docker run --rm -w /app/src court-monitor:ownership python main.py --help
```

Also exercise the real `/health/live` and static endpoint on a disposable smoke
container, retain the full logs, and record the results before accepting or merging.
Commit the current source/docs changes once Git metadata is writable. Optional
semantic installations were not downloaded or image-built in this continuation.
