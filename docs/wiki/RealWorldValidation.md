# Real-World Validation v1

## Зачем это нужно

Real-World Validation v1 — отдельный evaluation контур для проверки pipeline на реальных публикациях из source adapters. Он не заменяет unit/integration tests и final synthetic evaluation: цель — измерить качество на зафиксированном корпусе, golden annotations и safety gates.

Код: `src/evaluation/real_world/`. Данные: `evaluation/real_world/`. Локальный raw cache не коммитится: `var/real_world/`. Отчёты пишутся в `reports/real_world_validation_v1.{json,md}`.

## Быстрый сценарий

```bash
uv run python src/main.py real-world-corpus-status
uv run python src/main.py real-world-golden validate
EVALUATION_DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor_eval \
  uv run python src/main.py evaluate-real-world --split dev --no-fail-on-gates
```

Результат может быть `PRELIMINARY`: это значит, что pipeline измерен, но
разметка/объём VERIFIED данных пока не дают production claim.

## Поток

```plantuml
@startuml
title Real-World Validation v1

rectangle "build-real-world-corpus" as Build
database "var/real_world\nraw cache" as Cache
file "evaluation/real_world/corpus_manifest.json" as Manifest
file "evaluation/real_world/golden/*" as Golden
file "evaluation/real_world/policy_v1.json" as Policy
file "evaluation/real_world/rf_snapshot_eval_v1.csv" as RF
database "disposable PostgreSQL\n*_test / *_eval" as DB
database "Qdrant\noptional semantic benchmark" as Qdrant
rectangle "evaluate-real-world" as Eval
file "reports/real_world_validation_v1.json/md" as Reports

Build --> Cache : polite fetch, >= 1s/domain
Build --> Manifest : references, hashes, temporal split
Golden --> Eval : DRAFT / VERIFIED annotations
Manifest --> Eval
Cache --> Eval : replay documents offline
Policy --> Eval : hard gates, quality targets
RF --> Eval : evaluation RF snapshot
Eval --> DB : truncate disposable tables, run product pipeline
Eval ..> Qdrant : only with --semantic-model
Eval --> Reports
@enduml
```

## Корпус

`corpus_manifest.json` хранит only references and hashes, без полного текста статей. Текущий draft manifest:

- 156 articles total: `ovd-info=144`, `sota-vision=12`.
- Temporal periods: `T0=109`, `T1=16`, `T2=15`, `T3=16`.
- Evaluation sample: 156 articles.
- Raw replay требует matching cache в `var/real_world/`.

### Почему 156, а не ~1000

Цель 600/400 недостижима существующими адаптерами за 2026-03-01…2026-08-31; discovery публикации не теряет. Проверено 2026-09-15:

- **OVD-Info** (`/express-news`): листинг непрерывен — границы страниц стыкуются по датам (стр. 1: 2026-05-21…08-21, стр. 2: 04-09…05-19, стр. 3: 03-03…04-09, стр. 4: 02-03…03-02). Публикаций за месяц по датам в URL: март 38, апрель 48, май 26, июнь 6, июль 16, август 10 — всего 144, все в manifest. Парсер терял 11 дайджестов (`<ul><li>`), это исправлено до сборки manifest. Другие разделы сайта адаптер не обходит (новые адаптеры вне scope).
- **SOTA** (`/category/news/`): 14 страниц по 12 ссылок = 155 публикаций за 2020–2026 (около 2 в месяц; страница 15 отвечает 404). В период попадают 12; ещё 4 старые страницы не парсятся (пустой `entry-content`).
- HTTP-ошибок, 403/429 и дублей нет; `discovery_limit` 1000 заканчивается раньше начала периода.

Больше статей за этот период дадут только другие разделы/ленты источников (новый adapter) или более длинный период — это изменение критериев корпуса, а не исправление discovery.

Команды:

```bash
uv run python src/main.py build-real-world-corpus
uv run python src/main.py build-real-world-corpus --offline
uv run python src/main.py build-real-world-corpus --from-manifest
uv run python src/main.py real-world-corpus-status
```

`build-real-world-corpus` refuses `--min-interval` ниже 1.0 second per domain. Exit code `2` значит infrastructure/data problem: missing manifest/cache, changed content hash, invalid annotations, unsafe database URL, unavailable disposable DB.

## Golden Dataset

Golden data is human-reviewable JSON:

```text
evaluation/real_world/golden/VERSION.json
evaluation/real_world/golden/persons.json
evaluation/real_world/golden/articles/*.json
```

Current draft dataset:

- 45 annotated articles: `dev=27`, `validation=9`, `test=9`.
- 129 golden persons.
- All 45 articles are `DRAFT`; no production accuracy claim until human verification.
- `policy_v1.json` requires at least 100 VERIFIED articles and 20 per split before result can be non-preliminary.

Identity uses `golden_person_id`, never database IDs. Mentions/events/evidence are character spans over parsed article text. `VERIFIED` can only be set by explicit reviewer command.

```bash
uv run python src/main.py real-world-golden validate

uv run python src/main.py real-world-golden locate \
  --key ovd-info:/express-news/... \
  --text "Иван Петров"

uv run python src/main.py real-world-golden review-sheet \
  --case-id rw-20260301-00

uv run python src/main.py real-world-golden verify \
  --case-id rw-20260301-00 \
  --reviewer "Name" \
  --confirm-checked-against-source
```

`validate --write-hash` records current content hash in `VERSION.json`; bump `dataset_version` before publishing changed results.

### Как выбрать sample

Manifest уже содержит детерминированный stratified sample (`evaluation_sample=true`, `sampling_seed` в manifest). Strata — sampling tags (`hyphenated_name`, `yo_letter`, `initials`, `multi_person`, `several_events`, `shared_sentence`, `historical_reference`, `political_keywords`, `non_political`): round-robin по strata, самые редкие первыми. Tags — эвристики для отбора, не разметка. Следующие кейсы берутся из sample в порядке публикации; статьи, делящие персону или duplicate story (`duplicate_group`), кладутся в один split (`assign_splits`, 60/20/20 по группам).

### Как добавить golden case

1. `real-world-corpus-status` — cache совпадает с manifest по hash.
2. Для каждого упоминания/события получить offsets через `real-world-golden locate` (offsets по `ParsedArticle.text`, не по HTML и не по заголовку).
3. Записать `articles/<case_id>.json`: `annotation_status: DRAFT`, `annotation_origin` честно (`agent_draft`, `system_output`, `human`), excerpts только вокруг размеченных spans.
4. Персоны и person-level ожидания (persecution, RF, candidate) — в `persons.json`; `same_as`/`distinct_from` для однофамильцев.
5. `real-world-golden validate` → исправить проблемы → bump `dataset_version` → `validate --write-hash`.

Вывод court-monitor можно использовать как черновик, но это `system_output` DRAFT, а не ground truth.

### Единица разметки события

Одно событие размечается **один раз на факт**, а не на каждое предложение, которое о нём сообщает. Живой репортаж повторяет освобождение нарастающим итогом («отпустили шесть человек», «уже восемь задержанных»), новость пересказывает приговор в лиде и в теле — это один golden event на первом упоминании, остальные повторы в `disputed`. Разные факты одного типа размечаются раздельно: девять административных арестов Кирмана — девять событий.

Экстрактор устроен иначе: он выдаёт событие на каждое предложение с триггером. Поэтому повтор факта всегда попадает в `event_precision` как false positive, и потолок точности на корпусе ниже единицы. Это осознанное расхождение: схлопывать повторы по паре (тип, персона) нельзя, случай Кирмана показывает, почему.

### Как проверить source evidence

`review-sheet` пишет Markdown-лист в `var/real_world/review/`: URL → персоны → mentions → events → persecution → RF → expected candidate → evidence → спорные места, и полный текст из cache с подсвеченными mentions. Reviewer открывает `canonical_url`, сверяет каждый пункт с опубликованным текстом и только после этого запускает `verify --confirm-checked-against-source`. `verify` отказывает, если у кейса есть ошибки validation.

### Задача human verification (не выполнена)

Без неё любой результат `PRELIMINARY`; агент не ставит `VERIFIED` и не расширяет golden по выводу системы.

1. Проверить против источника все 45 DRAFT-кейсов (`review-sheet` → `verify`); исправления разметки — отдельным bump `dataset_version`, не под метрику.
2. Добавить и проверить ≥ 55 новых кейсов из `evaluation_sample`, чтобы было ≥ 100 VERIFIED и ≥ 20 в каждом split.
3. Отдельно проверить разметку, против которой система спорит чаще всего (`claim_failure_categories` в отчёте). Самые частые: `upstream_persecution_state` — golden `uncertain`/`needs_review` против системного `non_political`; `event_extraction_or_annotation_granularity` — одно golden-событие против нескольких системных. Проверяется источник, не отчёт.
4. Test split остаётся locked: thresholds по нему не калибруются; первый прогон `--split test` — после верификации.

### Спорная разметка

- Сомнение фиксируется в `disputed`, а не решается молча.
- Если человек не может решить статус — `expected_status: uncertain`, допустимые варианты в `acceptable_statuses` (`needs_review` для пограничных дел).
- RF: если персона в snapshot, но статья не даёт отчества/даты рождения — `needs_review`/`ambiguous` с `acceptable_review: true`; `not_matched` только если записи с таким ФИО нет.
- Известное ограничение системы — `known_limitation` + конкретные `known_limitation_kinds`; оно исключает из hard gates только эти виды ошибок.

## Retrieval queries и relevance judgments

`evaluation/real_world/retrieval_queries.json` — список `RealRetrievalQuery`
(схема в `src/evaluation/real_world/retrieval_eval.py`, валидация при загрузке):

| поле | смысл |
|---|---|
| `query_id`, `text`, `entity_type`, `split` | идентификатор, текст запроса, PERSON/EVENT, split запроса |
| `judgments` | ключ сущности → grade `2` (явно релевантна), `1` (частично), `0` (явно нерелевантна). Ключ: golden person id, `case_id/event_id` для события или `extra-person:`/`extra-event:` для сущности из статьи корпуса без golden-разметки |
| `expected_no_match` | запрос, для которого в корпусе не должно быть принятой сущности; у него не может быть grade > 0 и он не может быть `semantic_only` |
| `semantic_only` | у запроса нет общих словарных основ с релевантными документами (проверяется отдельно) |
| `tags` | класс запроса: `semantic_only`, `group`, `negative_hard`, `negative_offtopic`, `namesake_trap`, `initials`, `paraphrase`, … — по ним режутся метрики |
| `notes` | пояснение разметчика |

Ключ, которого **нет** в `judgments`, считается UNJUDGED: он никогда не
учитывается как нерелевантный, поэтому precision считается только по
размеченной части выдачи, а recall@k — нижняя оценка. Пустой `judgments`
допустим только при `expected_no_match`.

`query_problems()` дополнительно проверяет уникальность `query_id`, наличие
тегов, существование ключей в golden и то, что релевантная сущность принадлежит
тому же split, что и запрос (иначе запрос протекал бы в чужой split).

DRAFT-оценки pooled-кандидатов лежат отдельно, в
`evaluation/semantic_v2/pool_judgments.json`:

```json
{
  "status": "DRAFT",
  "annotation_origin": "agent_draft",
  "judgments":   {"rq-12": {"gp-ivanov-ivan": 2, "gp-petrov-petr": 0}},
  "cross_split": {"rq-12": {"gp-sidorov-sidor": 1}},
  "retired":     {"rq-108": "pool review found a partial match: …"}
}
```

- `judgments` — оценки, которые используют метрики;
- `cross_split` — релевантные сущности из статей другого split: оценены, но в
  метриках остаются UNJUDGED, чтобы не протащить чужой split;
- `retired` — запросы, выбывшие из метрик по итогу review (например «negative»,
  для которого нашлось частичное совпадение).

Кандидаты для разметки набираются пулингом (union top-100 нескольких систем),
а листы для человека — blind: `var/real_world/review/semantic_v2/`
(`sheet_priority.csv`, `sheet_full.csv`, `keys.json`), без модели, ранга и score.

## Evaluation Run

`evaluate-real-world` runs product code through one disposable PostgreSQL database:

1. Namesake ER benchmark on seeded persons.
2. Temporal corpus replay `T0..T3`.
3. Extraction, event association, ER, persecution, RF and candidate metrics against selected golden split.
4. Retrieval and research benchmarks.
5. Monitoring scenarios and DB invariants.
6. Safety gates and JSON/Markdown report.

```bash
export EVALUATION_DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_dev@localhost:5433/court_monitor_eval
uv run python src/main.py evaluate-real-world --split dev --no-fail-on-gates
```

Options:

- `--split dev|validation|test|all` selects golden split; `test` forces `--verified-only`.
- `--verified-only` excludes DRAFT cases.
- `--full` adds repeated runs, failure injection and manual review continuation.
- `--semantic-model` uses real embeddings + Qdrant; otherwise semantic benchmark is marked `NOT_RUN`.
- `--llm-intake` uses Together AI for natural-language intake; otherwise intake is `NOT_RUN`.
- `--no-fail-on-gates` writes reports and exits 0 even when gates fail.

Database safety: URL must point to a disposable database name ending `_test` or `_eval`; runner truncates disposable domain tables.

Locked test split: DRAFT-кейсы `test` не попадают ни в `--split test`, ни в `--split all`. Thresholds из `policy_v1.json` нельзя калибровать по test.

Exit codes: `0` — все обязательные gates прошли (или `--no-fail-on-gates`), `1` — провален hard gate или quality target, `2` — infrastructure/data error (нет manifest/cache, hash не совпал, golden невалиден, БД недоступна или не disposable).

Полный прогон с реальной моделью:

```bash
docker compose --profile semantic up -d qdrant
uv sync --group semantic
QDRANT_TEST_URL=http://127.0.0.1:6333 \
  uv run python src/main.py evaluate-real-world --split all --full --semantic-model
```

## CI

- Обычный CI: unit tests evaluator-а (`tests/evaluation/test_real_world_*.py`) на committed fixtures, без сети и моделей.
- PostgreSQL job: `test_real_world_monitoring_scenarios.py` реплеит маленький HTML-корпус через production pipeline (rerun, crash recovery, Qdrant outage, source/DB failures, no snapshot, manual review).
- Реальный корпус (`build-real-world-corpus`) и полный `evaluate-real-world` — только явные команды, не CI.

## Report Contract

Stable JSON schema lives in `src/evaluation/real_world/results.py`.

Important statuses:

- `PASSED` — all hard gates and required targets pass, enough VERIFIED data.
- `FAILED_GATES` — measured hard/quality gates failed.
- `PRELIMINARY` — DRAFT annotations or too few VERIFIED articles; no production claim.
- `INFRASTRUCTURE_ERROR` — run could not execute safely.

Hard gates include false person auto-link, false RF absence, cross-person persecution attribution, contradicted report claims, duplicate monitoring findings, rerun duplicates, no-snapshot findings, RF review-status findings and DB invariant violations.

## Limits

- Current dataset is draft-only; metrics are preliminary.
- Evaluation RF snapshot is committed for this corpus, not real current Rosfinmonitoring list.
- Real network is used only while building corpus; evaluation replays cached documents.
- Failure injection is in-process; real SIGKILL and PostgreSQL restart are not simulated.
- Semantic and LLM sections are opt-in and may be `NOT_RUN`.
