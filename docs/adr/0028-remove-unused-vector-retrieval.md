# ADR 0028: Remove the unused vector retrieval and pgvector

## Status

Accepted, 2026-10-13. Amends [ADR 0027](0027-investigation-runtime-scenarios-and-retrieval.md)
(its dense retrieval is removed) and finishes what ADR 0011 and ADR 0018 left when their
code was removed on 2026-09-27 (their tables and the extension).

## Context

Three things in the repository were built for searching by vectors, and none was in use.

- **The investigation's dense retrieval** (ADR 0027): `publications_dense`, the table
  `publication_vectors`, the command `index-publications`, the setting
  `INVESTIGATION_DENSE_MODEL`. Optional, off by default, its migration applied to no
  working database, its index built nowhere but on a copy for one measurement.
- **The tables of the first semantic subsystem** (ADR 0011, 0018): `semantic_documents`,
  `semantic_vector_collections`, `semantic_vectors`, `semantic_index_state`. Their code
  went on 2026-09-27; the tables stayed «for the applied migrations», empty on the
  operator's server.
- **pgvector**: the `vector` extension those tables needed, and with it a database image
  that is not the stock PostgreSQL one, in compose, in CI and in every instruction for a
  test database.

The cost was not the code alone. A new database could not be migrated on a server
without pgvector; the image had to be the pgvector build «because the applied migrations
still create it»; a reader of the investigation met a retriever, a command and a setting
that nothing used.

The Telegram bot (ADR 0019) was named in the same request. It was removed on 2026-09-27
and nothing of it is left in the code, the settings, compose or the dependencies; this
ADR records that it was checked.

## Decision

Remove all three, completely: no flag that turns them off, no table kept empty, no
command that says the feature is disabled.

### What is removed

- `results/semantic.py`, `processing/evidence/publication_vectors.py`,
  `DensePublicationsRetriever`, `ProcessingService.index_publication_vectors`,
  `ResultsService.dense_publications`, the wiring of an embedder for the investigation
  in `application.py`, the command `index-publications`, the setting
  `INVESTIGATION_DENSE_MODEL`, the score types `cosine` and `rrf`.
- `db/models/semantic.py`: the five ORM models and the `Vector` column type.
- The dense and hybrid branches of `evaluation.investigation`; the golden set and the
  measures of the name search and the ranked text search stay.
- What the revision `m3n4o5p6q7r8` created (`publication_vectors`). It was applied to
  no working database — the operator's server and the local one were both at
  `k1f2a3b4c5d6` — but it was the head of `main` for a day, so a developer's database or
  a copy may be at it. The revision is kept, empty; its table goes with the others.

### What stays

- **The ranked text search** (`publications_fts`, PostgreSQL full-text search) and the
  search by name in the text: they never depended on vectors.
- **Rank fusion in `merge_evidence`**: it still fuses the lists of several retrievers
  that find the same document (by name, by ranked text, in a channel).
- **The junk screen's embedding mode** (`JUNK_SCREEN=1`, `processing/junk/embedder.py`,
  the optional `semantic` dependency group, `INSTALL_SEMANTIC` in the Dockerfile). It is
  a different feature — a classifier of whole articles, with no vector stored anywhere —
  and it is not removed here. The embedder is back to what it was before the dense
  retrieval asked it for query vectors.
- **Telegram as a source of publications** (`ingestion/sources/telegram`, the channels of
  `channels.csv`) and the investigation's search of those channels. They are the
  pipeline's main input and a working retriever; «Telegram» in the request meant the bot.

### The database

No revision is removed or renumbered: every one keeps its id and its place, so a
database at any of them still finds it. Three of them do nothing now.

- `m4n5o6p7q8r9` (new, after `m3n4o5p6q7r8`): drops the five tables if they exist, then
  tries the `vector` extension.
- `t4u5v6w7x8y9`, `u5v6w7x8y9z0` and `m3n4o5p6q7r8`, which created the extension and the
  vector tables, are empty now. A database that ran them is at the same revision as
  before and is cleaned by `m4n5o6p7q8r9`; a new database never creates what would only
  be dropped, and so needs no pgvector on its server.
- **The extension may not be this database's alone**, so it is not dropped as a rule. It
  goes when the migrating role installed it and nothing is left that depends on it. The
  second is not looked up — a column, an array, a domain over the type, a function that
  takes it, in any schema: the list has no end — it is asked of the database: `DROP
  EXTENSION` without `CASCADE`, in a savepoint; a refusal for dependent objects keeps the
  extension. Kept for either reason, the migration says why in its log and still ends;
  it never drops anything of somebody else's.
- `m7n8o9p0q1r2` (`semantic_documents`): its downgrade tolerates a table already gone.
- `m4n5o6p7q8r9` does not bring anything back on downgrade. Between a downgrade below it
  and the next upgrade a database that had the old tables and one that never did are not
  the same; at the new head they are, but for an extension kept for the reasons above.
- `investigation_runs` (`l2m3n4o5p6q7`) is untouched.
- `monitoring_runs.semantic_entities_indexed` stays: a counter column of a stored run,
  always zero now; dropping it would change a table for nothing.

The image is `postgres:18.6-bookworm`, pinned by digest, in compose and in CI: the same
PostgreSQL build the pgvector image carried, so a data directory opens under either.

**Order on a deployed database.** Migrate first — with `run --rm --no-deps migrate`, so
that compose does not recreate the database on the new image on the way — and change the
image after (`docs/wiki/Rebuild-Image.md` has the block for this one release). Tried on
throwaway volumes: under the plain image the empty tables and the extension are dropped
without pgvector's library, but a table with a vector index (`hnsw`, `ivfflat`) cannot
be — «could not access file "vector"»; in the documented order a database with such an
index, rows and the old head migrates and then opens under the plain image. Neither
working database has such an index. A migration that says it kept the extension leaves
the database needing the pgvector image: the image is not changed until that is settled.

**Stored runs.** A run stored while the dense retrieval existed may hold evidence whose
score is called `cosine`. Such evidence is still read — as a result and as the progress
to resume from — with its text, its reference and its number; the score has no kind.
No new result names a removed kind.

**CI.** The suite runs on plain PostgreSQL, where the tests of the transition have
nothing to drop and are skipped. A job of its own (`migration from pgvector`) runs them,
and nothing else, on the pgvector image, and fails if one is skipped. It goes when no
database of that kind is left.

## Consequences

- Investigation retrieval is: structured (entity groups, unnamed figurants) + text
  (by name, ranked full-text) + the configured channels' own search.
- A new database is migrated on stock PostgreSQL. The application starts without
  pgvector, without an embedding model, without a GPU.
- The measurements of ADR 0027 for dense and hybrid retrieval are history: hybrid was
  better than lexical alone on that set (Recall@10 0.801 against 0.707). It was removed
  because it was not used, not because it measured badly. Bringing semantic retrieval
  back is a new feature: a benchmark first, then a decision on where vectors live.
- A test database made from the old instructions (`ebnv-pgvector-test`, the pgvector
  image) keeps working: the migrations no longer ask it for the extension.
- `.env.example` is not edited by this change (it carries the owner's local edits); if it
  names `INVESTIGATION_DENSE_MODEL`, that line is dead.
