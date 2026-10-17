# ADR 0031: Data ownership and the packages' APIs

## Status

Accepted, 2026-10-17. Builds on [ADR 0030](0030-workspace-packages.md) (the packages)
and [ADR 0025](0025-modular-monolith-and-pipeline-orchestrator.md) (the layering). The
map is [docs/architecture/data-ownership.md](../architecture/data-ownership.md); the
inventory that started it, [data-ownership-inventory.md](../architecture/data-ownership-inventory.md).

## Context

After ADR 0030 the modules were separate distributions, but their data was not:
`court-platform` held the ORM classes of every table (`db.models.*`, re-exported by
`db.orm_models`), and any package — the console most of all — imported any of them, or
read and wrote any table in SQL. The boundary tests saw imports of modules only, so a
`DELETE FROM entity_group_politics` in `processing` (upstream of the analysis that owns
the verdicts) crossed the layering unseen. The inventory counted 482 references from
100 production files to tables another package owns.

## Decision

**One PostgreSQL.** The database, its schema and its Alembic history stay as they are;
ownership is a rule of the code, not a separate database or a service. Calls between
packages stay typed Python calls in one process.

**A table has one owner.** The owner keeps the ORM class in its `storage`:

| owner | storage | tables |
|---|---|---|
| court-ingestion | `ingestion.storage.models` | sources, source_documents, parsed_articles |
| court-processing | `processing.storage.models` | extraction, entity groups and a person's decisions on them, roles, evidence, junk holds, the officials' exclusion list |
| court-processing (ER v2) | `persons.storage.models` | persons and their resolution |
| court-analysis | `analysis.storage.models` | politics, case news, the Rosfinmonitoring list and matches, unnamed figurants, persecution, the operator's reference lists |
| court-results | `results.storage.models` | monitoring_findings, entity_done_marks, chat_questions |
| court-orchestrator | `orchestrator.storage.models` | monitoring runs, their items and checkpoints, operator operation runs |
| court-investigation | `investigation.storage.models` | investigation_runs |
| court-platform | `db.models.settings` | app_settings, entity_model_spend (`llm.model_choice`) |

`db.models.base.Base` stays in the platform: every model registers on it.
`db.orm_models` is gone. `migrations/model_registry.py` is the one module that imports
every owner's models, for Alembic; nothing in production imports it.

**`storage` is internal.** Only its own package imports it (and the registry, and that
package's own tests). Another package reads through the owner's public API, which
returns DTOs (frozen dataclasses), never ORM records; it writes through the owner's
commands. A command takes the caller's `Session` when a change must be atomic with the
caller's own: the session is the unit of work, the mutation stays the owner's.

**Upstream never reads or writes downstream.** Where identity (upstream) must take
account of the analysis (downstream), the contract is identity's:
`processing.identity.downstream.IdentityDownstream`. It hands identity the unnamed
figurants an operator identified, and is told, in the same transaction, when a group
stops being a figurant (the politics verdict goes) or is merged into another (its list
matches follow). The analysis implements it (`analysis.service.GroupFollowers`); the
composition root hands it in. `IdentityService.merge_confirmed_pairs` takes the groups
on the list from its caller instead of reading the analysis's matches.

**Disputed owners**, decided here:

- `excluded_persons` (the officials' exclusion list) is **processing**'s, not the
  analysis's: step 4 (roles) reads it and writes the officials it finds by title, and the
  console's list edits it; Airtable (analysis) only syncs it, through processing's
  command — the allowed direction.
- `entity_group_unnamed_mentions` is **processing**'s: the rebuild of entity groups
  writes it.
- `investigation_runs` is the **investigation**'s: its model and its store
  (`investigation.run_store`) are the investigation's only SQL; the rest of it reads
  the domain through `results.service` and `ingestion.search` as before (ADR 0027).

**Read facades.** A cross-domain read that cannot be expressed through the owners'
APIs without many round trips is a read facade in the package that owns the result
(usually `results`): it may read the tables of packages upstream of it in SQL, read
only, returns DTOs, and is listed with its reason in data-ownership.md. The console
does no SQL on domain tables.

## Consequences

- `tests/test_data_ownership.py` holds it: one owner per table, the registry complete,
  no domain model in the platform, `storage` imported by its owner only, no SQL that
  writes another package's table, and the SQL reads of other packages' tables are
  exactly the listed read facades.
- `tests/test_package_graph.py` and `tests/test_module_boundaries.py` keep the layering;
  no dependency was reversed (`court-analysis` now declares `court-ingestion`, which it
  read through the platform before).
- A package can be moved to another repository with its tables' models; the tables stay
  in the one database.
