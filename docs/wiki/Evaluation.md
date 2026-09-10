# Evaluation

Оценка качества поиска на фиксированном наборе запросов, метрика — **Mean Reciprocal Rank (MRR)**.

## Модели (`evaluation_models.py`)

- **`EvaluationDocument`** — тестовая публикация для загрузки в БД перед оценкой (`source_base_url`, `external_id`, `canonical_url`, `title`, `chunks` — уже готовые тексты фрагментов, без реального парсинга HTML).
- **`EvaluationCase`** — тестовый запрос: `query_text` + `expected_chunk` (`ChunkReference`: `source_base_url` + `external_id` + `ordinal`).
- **`EvaluationCaseResult`** — фактический результат одного кейса: найденные чанки + `reciprocal_rank`.
- **`EvaluationReport`** — все результаты + `mean_reciprocal_rank`.

Загружаются из JSON (`evaluation_loader.py`, Pydantic `TypeAdapter`) — фикстуры: `tests/fixtures/evaluation_corpus.json`, `tests/fixtures/evaluation_cases.json`.

## Метрика (`evaluation_metrics.py`)

- `reciprocal_rank(retrieved, expected)` — `1/rank` первого совпадения с `expected_chunk` по списку найденных, `0.0` если не найден.
- `mean_reciprocal_rank(ranks)` — среднее по всем кейсам, `0.0` на пустом списке.

## Прогон (`SearchEvaluator`, `src/search_evaluator.py`)

Принимает любой `SearchBackend` (lexical или dense — см. [Search](Search.md)) и `limit` (по умолчанию 3). Для каждого `EvaluationCase` вызывает `search.search(...)`, сравнивает по `ChunkReference` (не по `chunk_id` — сравнение устойчиво к разным ID в разных backend'ах/БД).

## CLI: `evaluate-search`

`main.py`, команда `evaluate-search --backend lexical|dense`:

1. Загружает `EvaluationDocument` из `--corpus-path`, сохраняет их через `SqlAlchemyIngestionPersistence` (используя `document.chunks` как готовые `ArticleChunk`, минуя `Chunker`/парсер).
2. Для `--backend dense` — дополнительно пересоздаёт коллекцию Qdrant и переиндексирует.
3. Прогоняет `EvaluationCase` из `--cases-path`, печатает или пишет (`--output-path`) `EvaluationReport` как JSON.

## Baseline-отчёты (`reports/`)

- `postgres_lexical_baseline.json` — `mean_reciprocal_rank: 0.5` (2 из 4 запросов не нашли совпадение — lexical чувствителен к точности словоформ/фраз).
- `qdrant_dense_baseline.json` — `mean_reciprocal_rank: 1.0` (все 4 запроса нашли ожидаемый чанк первым).

Это baseline на текущем небольшом тестовом корпусе (4 кейса) — не гарантия на реальных данных, но фиксирует текущее поведение для регрессии при изменении моделей/запросов.
