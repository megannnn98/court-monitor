# Evaluation

Оценка качества поиска выполняется на фиксированном корпусе и наборе запросов.

Основная метрика — **Mean Reciprocal Rank (MRR)**.

## Evaluation Corpus

Фикстуры:

```text
tests/fixtures/evaluation_corpus.json
tests/fixtures/evaluation_cases.json
```

`EvaluationDocument` описывает тестовую публикацию и её chunks.

`EvaluationCase` содержит:

```text
query_text
expected_chunk
```

`expected_chunk` определяется через:

```text
source_base_url
external_id
ordinal
```

Поэтому evaluation не зависит от конкретных database IDs.

## Метрики

Для одного запроса используется Reciprocal Rank:

```text
RR = 1 / rank
```

где `rank` — позиция первого ожидаемого chunk в результатах.

Если ожидаемый chunk не найден:

```text
RR = 0
```

Mean Reciprocal Rank:

```text
MRR = mean(RR)
```

по всем evaluation cases.

## SearchEvaluator

`src/search_evaluator.py`.

`SearchEvaluator` зависит только от общего:

```text
SearchBackend
```

Поэтому один evaluator используется для всех backend'ов:

```text
lexical
dense
hybrid
reranked-hybrid
```

Специальной evaluation-логики для cross-encoder reranking нет.

Это позволяет сравнивать разные retrieval/ranking pipelines через один и тот же набор cases.

## CLI

Evaluation запускается командой:

```bash
uv run python src/main.py evaluate-search \
  --backend BACKEND
```

Доступные значения:

```text
lexical
dense
hybrid
reranked-hybrid
```

Пример:

```bash
uv run python src/main.py evaluate-search \
  --backend reranked-hybrid \
  --output-path reports/reranked_hybrid_baseline.json
```

Для dense-based backend'ов evaluation использует отдельную Qdrant collection:

```text
QDRANT_EVALUATION_COLLECTION
```

Она должна отличаться от:

```text
QDRANT_COLLECTION
```

Перед evaluation collection пересоздаётся и заполняется фиксированным evaluation corpus.

## Baseline

Текущий evaluation corpus содержит 4 search cases.

Полученные baseline:

| Backend | MRR |
|---|---:|
| lexical | 0.5 |
| dense | 1.0 |
| hybrid | 1.0 |
| reranked-hybrid | 1.0 |

Отчёты:

```text
reports/postgres_lexical_baseline.json
reports/qdrant_dense_baseline.json
reports/hybrid_baseline.json
reports/reranked_hybrid_baseline.json
```

### Lexical

```text
MRR = 0.5
```

Ожидаемый chunk стоит первым для:

```text
rehabilitation-of-nazism
extremist-activity
```

Для:

```text
military-fakes
picket-detention
```

ожидаемый chunk lexical backend не находит.

### Dense

```text
MRR = 1.0
```

Все четыре expected chunks находятся на первой позиции.

### Hybrid

```text
MRR = 1.0
```

На текущем небольшом corpus все четыре expected chunks также находятся на первой позиции.

По MRR hybrid пока не улучшает dense baseline.

### Reranked Hybrid

```text
MRR = 1.0
```

Все четыре expected chunks остаются на первой позиции после cross-encoder reranking.

Cross-encoder при этом меняет порядок части остальных hybrid candidates, то есть reranking действительно выполняется.

Однако MRR не увеличивается, поскольку dense и hybrid уже достигают максимального:

```text
MRR = 1.0
```

на текущих evaluation cases.

## Интерпретация

Текущий evaluation corpus подходит для regression testing поискового pipeline, но слишком мал и прост для объективного сравнения dense, hybrid и reranked-hybrid.

Полученный результат не означает, что hybrid retrieval или cross-encoder reranking не улучшают поиск на реальных данных.

Он означает только следующее:

```text
на текущих 4 evaluation cases улучшение MRR измерить невозможно,
потому что dense baseline уже достигает MRR = 1.0
```

Для дальнейшего сравнения ranking quality понадобится более сложный evaluation corpus с:

- неоднозначными запросами;
- лексически похожими нерелевантными chunks;
- семантически близкими документами;
- cases, где expected chunk находится ниже первой позиции у baseline retrieval.
