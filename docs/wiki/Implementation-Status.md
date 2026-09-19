# Implementation Status

The single source of the project's current status. Earlier root-level reports
(`IMPLEMENTATION_STATUS.md`, `PROGRESS_REPORT.md`, `IMPLEMENTATION_REPORT.md`) described
older branches and were removed; they remain in Git history.

## Last verified

2026-09-18, branch `fix-main-technical-debt`, commit `80092e3`, Python 3.13.13.
Every result below comes from a run on that date; nothing is carried over.

| Check | Command | Result |
|---|---|---|
| Clean install | `uv sync --frozen` in `python:3.13-slim-bookworm` (no `pg_config`, no `gcc`) | ok; drivers and `api` import |
| Ruff | `uv run ruff check src tests migrations` | all checks passed |
| Format | `uv run ruff format --check src tests migrations` | 432 files formatted |
| mypy | `uv run mypy --strict src tests` | no issues, 412 files |
| Diff hygiene | `git diff --check main...HEAD` | clean |
| Tests, no services | `uv run pytest` | 1 157 passed, 397 skipped |
| Tests, PostgreSQL + Qdrant | `TEST_DATABASE_URL=…/court_monitor_test QDRANT_TEST_URL=http://127.0.0.1:6333 uv run pytest` | 1 526 passed, 28 skipped |
| Migrations | `alembic upgrade head`, `downgrade -1`, `upgrade head` on `court_monitor_test`; `r2s3t4u5v6w7 → s3t4u5v6w7x8` on a copy of the working database | ok, data kept |
| Compose | `docker compose --profile production config` | valid |
| Image | `docker compose --profile production build` (under a separate tag) | built, 1.42 GB, no compiler inside |
| Image checks | `import api` offline; `/health/live`, `/health/ready` against `court_monitor_test`; `dagster definitions validate -m monitoring.dagster.definitions` | ok; ready; no migration in the API logs; validation successful |
| CLI | `--help` of 29 commands and 8 nested ones compared with the pre-split CLI | byte-identical, same exit codes |
| API | method + path of every route compared with the pre-split app | identical (48 routes); OpenAPI paths and components identical |

Not run: `docker compose --profile production up -d` with this branch (the running
deployment stays on `main` until the branch is reviewed); CI on GitHub.

### Skipped tests and why

| Tests | Without services | With PostgreSQL + Qdrant | Enabled by |
|---|---|---|---|
| PostgreSQL integration | 368 | 0 | `TEST_DATABASE_URL` (a database named `court_monitor_test`) |
| Qdrant integration | 1 | 0 | `QDRANT_TEST_URL` |
| Real person-NER model | 21 | 21 | `PERSON_NER_MODEL_TESTS=1` (downloads the GLiNER model) |
| Real embedding model | 3 | 3 | `SEMANTIC_MODEL_TESTS=1` (downloads the embedding model) |
| Live Together AI | 4 | 4 | `TOGETHER_LIVE_TESTS=1` / `LIVE_LLM_TESTS=1` with `TOGETHER_API_KEY`, `TOGETHER_MODEL` |

## Components

| Area | Where | Notes |
|---|---|---|
| Sources | `src/sources/` | 75 sources: `ovd-info`, `sota-vision`, `sudrf-2zovs`, `memopzk-figurants` (registry of «Поддержка политзаключённых. Мемориал»), `kommersant` (site via RSS), 70 Telegram channels — [Ingestion](Ingestion.md) |
| Extraction | `src/extraction/` | rule-based mentions, normalization to the nominative, events; optional GLiNER recognizer (off in production) — [Extraction](Extraction.md) |
| Entity resolution | `src/persons/` | ER v2 with human review — [Entity-Resolution](Entity-Resolution.md) |
| Persecution classification | `src/persecution/` | rule-based — [Persecution-Classification](Persecution-Classification.md) |
| Rosfinmonitoring | `src/rosfinmonitoring/` | snapshots and matching — [Rosfinmonitoring](Rosfinmonitoring.md) |
| Candidates | `src/candidates/` | political persecution and `NOT_MATCHED` — [Pipeline](Pipeline.md) |
| Research | `src/research/` | deterministic research in one read-only REPEATABLE READ snapshot, LangGraph workflow, reports — [Research](Research.md), [Research-Workflow](Research-Workflow.md), [Research-Reports](Research-Reports.md) |
| Semantic retrieval | `src/semantic_retrieval/` | candidate ids from Qdrant (default) or pgvector (`SEMANTIC_VECTOR_BACKEND`, [ADR 0018](../adr/0018-pgvector-vector-store.md)), facts from PostgreSQL — [Semantic-Retrieval](Semantic-Retrieval.md) |
| Monitoring | `src/monitoring/` | Dagster schedules per source, runs, findings — [Monitoring](Monitoring.md) |
| HTTP API and console | `src/api.py` (entry point), `src/web/` | REST routers, operator console, exports, wiki — [Local-Web-UI](Local-Web-UI.md) |
| Operator operations | `src/operator_console.py` | runs in PostgreSQL (`operator_operation_runs`) — [Data-Model](Data-Model.md) |
| Channel queue | `src/channel_feed/` | queue for @enbv2022 — [ADR 0017](../adr/0017-channel-feed-sources.md) |
| CLI | `src/main.py` (entry point), `src/cli/` | one composition root (`cli/context.py`) |
| ORM | `src/db/models/`, `src/db/orm_models.py` | models by domain on one `Base`; migrations in `migrations/` |

## CLI commands

`python src/main.py <command>` (`--help` works without a database):

- ingestion: `ingest`, `discover-and-ingest`
- search: `search`, `evaluate-search`
- extraction: `extract-entities`, `evaluate-extraction`
- persons: `resolve-people`, `resolve-person`, `person-resolution-reviews`, `evaluate-er`
- Rosfinmonitoring: `import-rosfinmonitoring`, `match-rosfinmonitoring`
- classification and candidates: `classify-persecution`, `list-candidates`
- research: `research`, `ask`
- semantic retrieval: `rebuild-semantic-index`, `semantic-search`, `evaluate-retrieval`
- monitoring: `monitor`, `monitor-derived`, `monitoring-status`, `monitoring-findings`
- evaluation: `evaluate-final`, `build-real-world-corpus`, `real-world-corpus-status`, `real-world-golden`, `evaluate-real-world`
- configuration: `validate-config`

## Deployment profiles

| Profile | Services |
|---|---|
| (none) | PostgreSQL 18.6 with pgvector (`pgvector/pgvector:pg18-bookworm`, pinned by digest) |
| `semantic` | + Qdrant |
| `migrate` | one-shot `alembic upgrade head` |
| `api` | + API |
| `monitoring` | + Dagster DB init, webserver, daemon |
| `production` | PostgreSQL, Qdrant, API, Dagster |

`compose.gpu.yaml` on top builds the image with the semantic and NER groups and gives
the containers the GPU ([ADR 0014](../adr/0014-production-deployment.md)).

## Known limitations

- Models and migrations disagree on a few pre-existing details (`alembic check`):
  `created_at` nullability on several tables, JSON vs JSONB in `rosfin_matches`, two
  indexes and one unique constraint. Left as they are: fixing them changes the stored
  schema.
- Operation runs execute in a thread of the API process that accepted them; if that
  process dies, the run becomes `interrupted` after 5 minutes without a heartbeat and
  is not resumed.
- The workflow resolves "the latest Rosfinmonitoring snapshot" before the research
  transaction; the chosen id is re-checked inside it.
- `evaluate-search` writes its fixed corpus into the database of `DATABASE_URL`
  (source «ОВД-Инфо evaluation»), unlike the evaluations with a disposable database.
- `src/monitoring/service.py` and `src/extraction/extractors.py` stay single modules:
  their parts share run state and rule tables, and splitting them was not worth the
  risk.
- A registry card changed after ingestion is not re-read; the channel queue's name
  suggestions for unnamed news are suggestions only (about three in four right).
- Dagster schedules are created stopped; they are started by hand.
- Switching `SEMANTIC_VECTOR_BACKEND` needs a full `rebuild-semantic-index`: the
  `indexed_at` marks are shared, and an incremental run on the other backend stops with
  `IndexBackendMismatchError` ([ADR 0018](../adr/0018-pgvector-vector-store.md)).
- The migration `t4u5v6w7x8y9` creates the `vector` extension: the PostgreSQL image must
  be the pgvector one before `alembic upgrade head` runs.
- With pgvector, person search is exact (no HNSW), event search HNSW; exact search time
  grows linearly with the number of persons ([ADR 0018](../adr/0018-pgvector-vector-store.md)).

## Reproduce

```bash
uv sync --frozen
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run mypy --strict src tests
uv run pytest

docker compose up -d postgres
docker compose --profile semantic up -d qdrant
TEST_DATABASE_URL=postgresql+psycopg://…/court_monitor_test \
QDRANT_TEST_URL=http://127.0.0.1:6333 uv run pytest

DATABASE_URL=postgresql+psycopg://…/court_monitor_test uv run alembic upgrade head
docker compose --profile production config
docker compose --profile production build
```

Architecture decisions: [docs/adr](../adr/) (ADR 0001–0018). Testing details:
[Testing](Testing.md). Setup: [Setup](Setup.md), [Getting-Started](Getting-Started.md).
