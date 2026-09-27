# Implementation Status

Эта страница — единственный источник текущего статуса проекта в wiki. Остальные
страницы объясняют сценарии и архитектуру, но не должны дублировать полный
журнал проверок.

Ранние root-level отчёты (`IMPLEMENTATION_STATUS.md`, `PROGRESS_REPORT.md`,
`IMPLEMENTATION_REPORT.md`) описывали старые ветки и были удалены; они остались
только в истории Git.

## Как читать эту страницу

- Оператору: смотрите `Components`, `CLI commands`, `Deployment profiles` и
  `Known limitations`.
- Программисту: смотрите `Last verified`, `Skipped tests and why`, `Reproduce`.
- Если дата `Last verified` старше текущей ветки, считайте цифры историческим
  снимком и перепроверьте нужные команды перед релизным решением.

## Last verified

2026-09-26, branch `feat/investigator-ui`, commit `6258f87` (then merged into `main`).

| Check | Command | Result |
|---|---|---|
| Ruff | `uv run ruff check .` | all checks passed |
| mypy | `uv run mypy src` | no issues, 327 files |
| Tests, PostgreSQL | `TEST_DATABASE_URL=…/court_monitor_test uv run pytest` | 2 230 passed, 33 skipped |
| Migrations | `alembic upgrade head` on `court_monitor_test` and on the working database (after a dump) | ok, up to `c9d0e1f2a4b5` |
| Image | `docker compose -f compose.yaml -f compose.gpu.yaml --profile production build api`, API recreated | healthy; every console page answers on the working data |

The 33 skipped tests need a service or a model: person-NER model 21
(`PERSON_NER_MODEL_TESTS=1`), Qdrant 4 (`QDRANT_TEST_URL`), Together AI 4
(`TOGETHER_LIVE_TESTS=1` / `LIVE_LLM_TESTS=1`), embedding model 3
(`SEMANTIC_MODEL_TESTS=1`), `sentence_transformers` not installed 1.

Not run: CI on GitHub; a visual check in a dark theme.

## Previous verification

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
| Entities (console) | `src/entities/` | the five-step pipeline of the console: people from mentions, roles, and the final Rosfinmonitoring check plus political verdicts, a model through OpenRouter with a cache and a budget — [Pipeline](Pipeline.md) |
| Unnamed figurants | `src/entities/unnamed.py`, `src/web/ui/unnamed.py` | unnamed persons of the publications and their candidates from the list — [Unnamed-Figurants](Unnamed-Figurants.md) |
| Candidates (old path) | `src/candidates/` | political persecution and `NOT_MATCHED` over Persons; used by monitoring findings — [Pipeline](Pipeline.md) |
| Monitoring | `src/monitoring/` | Dagster schedules per source, runs, findings; the junk screen before step 2 (`junk_screen.py`, its embedding model in `embedder.py`) — [Monitoring](Monitoring.md), [Junk-Screen](Junk-Screen.md) |
| HTTP API and console | `src/api.py` (entry point), `src/web/` | REST routers, operator console, exports, wiki — [Local-Web-UI](Local-Web-UI.md) |
| Operator operations | `src/operator_console.py` | runs in PostgreSQL (`operator_operation_runs`) — [Data-Model](Data-Model.md) |
| CLI | `src/main.py` (entry point), `src/cli/` | one composition root (`cli/context.py`) |
| ORM | `src/db/models/`, `src/db/orm_models.py` | models by domain on one `Base`; migrations in `migrations/` |

## Быстрый операторский сценарий

Проверить, что приложение живо и monitoring не застрял:

```bash
curl -s http://127.0.0.1:8001/health/live
curl -s http://127.0.0.1:8001/health/ready
uv run python src/main.py monitoring-status
```

Ожидание:

```text
live: процесс отвечает
ready: БД доступна, схема на ожидаемой миграции
monitoring-status: нет зависших или бесконечно падающих runs
```

## CLI commands

`python src/main.py <command>` (`--help` works without a database):

- ingestion: `ingest`, `discover-and-ingest`
- extraction: `extract-entities`, `evaluate-extraction`
- persons: `resolve-people`, `resolve-person`, `person-resolution-reviews`, `evaluate-er`
- Rosfinmonitoring: `import-rosfinmonitoring`, `match-rosfinmonitoring`
- classification and candidates: `classify-persecution`, `list-candidates`
- monitoring: `monitor`, `monitor-derived`, `monitoring-status`, `monitoring-findings`
- evaluation: `evaluate-final`, `build-real-world-corpus`, `real-world-corpus-status`, `real-world-golden`, `evaluate-real-world`
- configuration: `validate-config`

## Deployment profiles

| Profile | Services |
|---|---|
| (none) | PostgreSQL 18.6 with pgvector (`pgvector/pgvector:pg18-bookworm`, pinned by digest) |
| `migrate` | one-shot `alembic upgrade head` |
| `api` | + API |
| `monitoring` | + Dagster DB init, webserver, daemon |
| `production` | PostgreSQL, API, Dagster |

`compose.gpu.yaml` on top builds the image with the semantic (junk screen) and NER groups
and gives the containers the GPU ([ADR 0014](../adr/0014-production-deployment.md)).

## Known limitations

- Models and migrations disagree on a few pre-existing details (`alembic check`):
  `created_at` nullability on several tables, JSON vs JSONB in `rosfin_matches`, two
  indexes and one unique constraint. Left as they are: fixing them changes the stored
  schema.
- Operation runs execute in a thread of the API process that accepted them; if that
  process dies, the run becomes `interrupted` after 5 minutes without a heartbeat and
  is not resumed.
- `src/monitoring/service.py` and `src/extraction/extractors.py` stay single modules:
  their parts share run state and rule tables, and splitting them was not worth the
  risk.
- A registry card changed after ingestion is not re-read.
- Dagster schedules are created stopped; they are started by hand.
- The migration `t4u5v6w7x8y9` creates the `vector` extension: the PostgreSQL image must
  be the pgvector one before `alembic upgrade head` runs.
- The tables of the removed vector index stay in the schema, written and read by nothing:
  `semantic_documents`, `semantic_vector_collections`, `semantic_vectors`,
  `semantic_index_state`. Dropping them needs its own migration.

## Reproduce

```bash
uv sync --frozen
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run mypy --strict src tests
uv run pytest

docker compose up -d postgres
TEST_DATABASE_URL=postgresql+psycopg://…/court_monitor_test uv run pytest

DATABASE_URL=postgresql+psycopg://…/court_monitor_test uv run alembic upgrade head
docker compose --profile production config
docker compose --profile production build
```

Architecture decisions: [docs/adr](../adr/) (ADR 0001–0018). Testing details:
[Testing](Testing.md). Setup: [Setup](Setup.md), [Getting-Started](Getting-Started.md).
