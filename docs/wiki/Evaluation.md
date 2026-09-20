# Evaluation

## Зачем это нужно

Оценка качества поиска выполняется на фиксированном корпусе и наборе запросов.

Основная метрика — **Mean Reciprocal Rank (MRR)**.

Эта страница про быстрый article-level lexical search. Системная оценка всего
pipeline — [Real-World Validation](RealWorldValidation.md). Semantic retrieval
benchmarks — [Semantic Retrieval](Semantic-Retrieval.md).

## Быстрый сценарий

```bash
uv run python src/main.py evaluate-search \
  --output-path reports/postgres_lexical_baseline.json
```

Если нужен disposable DB, выставить `DATABASE_URL` на тестовую/оценочную базу до
запуска. Команда пишет evaluation corpus в выбранную БД.

## Evaluation Corpus

Фикстуры:

```text
tests/fixtures/evaluation_corpus.json
tests/fixtures/evaluation_cases.json
```

`EvaluationDocument` описывает тестовую публикацию: `source_base_url`, `external_id`, `canonical_url`, `title`, полный `text` (раньше — список `chunks`, см. [ADR 0002](../adr/0002-drop-dense-hybrid-search.md)).

`EvaluationCase` содержит:

```text
query_text
expected_article
```

`expected_article` (`ArticleReference`) определяется через:

```text
source_base_url
external_id
```

`ordinal` (позиция фрагмента) убран вместе с `ArticleChunk` — идентичность результата теперь на уровне статьи, а не фрагмента. Evaluation по-прежнему не зависит от конкретных database IDs.

## Метрики

Для одного запроса используется Reciprocal Rank:

```text
RR = 1 / rank
```

где `rank` — позиция первой ожидаемой статьи в результатах.

Если ожидаемая статья не найдена:

```text
RR = 0
```

Mean Reciprocal Rank:

```text
MRR = mean(RR)
```

по всем evaluation cases.

## SearchEvaluator

`src/search/evaluator.py`.

`SearchEvaluator` зависит только от общего `SearchBackend` — сейчас это `PostgresLexicalSearch` (единственная реализация).

## CLI

Evaluation запускается командой:

```bash
uv run python src/main.py evaluate-search
```

Опции:

```text
--corpus-path   путь к evaluation corpus (по умолчанию tests/fixtures/evaluation_corpus.json)
--cases-path    путь к evaluation cases (по умолчанию tests/fixtures/evaluation_cases.json)
--limit         лимит выдачи на один запрос
--output-path   путь для сохранения отчёта; без него отчёт печатается в stdout
```

Выбора backend'а через `--backend` больше нет — dense/hybrid/reranked-hybrid удалены, `evaluate-search` всегда использует lexical поиск.

## Baseline

Прежний baseline (`reports/*.json`) был построен на chunk-based evaluation corpus и удалён вместе с `ArticleChunk` — числа `MRR` для lexical/dense/hybrid/reranked-hybrid, которые здесь раньше приводились, относились к старой (chunk-level) схеме данных и не переносятся автоматически на новую.

Актуальный baseline для article-level lexical search в этой сессии не перегенерирован — не было доступа к `DATABASE_URL`/`TEST_DATABASE_URL` (см. [Setup](Setup.md)). Перегенерировать:

```bash
uv run python src/main.py evaluate-search \
  --output-path reports/postgres_lexical_baseline.json
```

## Real-World Validation

Системная оценка на реальных публикациях вынесена в [Real-World Validation](RealWorldValidation.md). Это отдельный контур:

- `build-real-world-corpus` фиксирует manifest и raw cache для replay;
- `real-world-golden` валидирует / готовит human review golden annotations;
- `evaluate-real-world` прогоняет product pipeline в disposable PostgreSQL и пишет `reports/real_world_validation_v1.{json,md}`;
- результат может быть `PRELIMINARY`, пока golden dataset содержит DRAFT cases или мало VERIFIED articles.
