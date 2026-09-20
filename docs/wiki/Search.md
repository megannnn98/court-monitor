# Search

## Зачем это нужно

Search здесь — поиск по сохраненным статьям. Он нужен для оператора и
разработчика как быстрый способ найти публикации и evidence, но не заменяет
Research и semantic entity retrieval.

## Быстрый сценарий

```bash
uv run python src/main.py search "реабилитация нацизма" --limit 5
```

Через UI:

```text
/ui/search
```

## Что происходит внутри

Общий контракт:

```python
search(SearchQuery) -> list[SearchHit]
```

Текущая article-level реализация одна: `PostgresLexicalSearch`.

```text
parsed_articles.search_vector @@ websearch_to_tsquery('russian', query)
```

Результат — статья целиком, не chunk. Chunk-level dense/hybrid/reranked search
удален вместе с `ArticleChunk` (ADR 0002).

## Пример

```text
query: "антивоенный пикет"
hit: article_id=123 title="Суд назначил штраф..." rank=0.42
```

Оператор открывает статью и проверяет контекст. Если нужен поиск людей по смыслу,
использовать [Semantic Retrieval](Semantic-Retrieval.md) через research workflow,
а не article search.

## Кодовые точки входа

| Сценарий | Код |
|---|---|
| backend protocol | `src/search/backend.py` |
| PostgreSQL lexical search | `src/search/postgres_lexical.py` |
| CLI | `src/search/cli.py` |
| API/UI | `src/web/routers/search.py`, `src/web/ui/search.py` |
| evaluation | `src/search/evaluator.py`, `src/search/evaluation_*.py` |

## Данные и артефакты

- `parsed_articles.text` — полный текст.
- `parsed_articles.search_vector` — generated tsvector.
- `reports/postgres_lexical_baseline.json` — baseline evaluation artifact.

## Проверка

```bash
uv run python src/main.py evaluate-search --output-path reports/postgres_lexical_baseline.json
uv run pytest tests/search
```

## Ограничения и типичные ошибки

- Поиск лексический: синонимы и смысловые формулировки может пропустить.
- Веса по `title` не реализованы; поиск идет по `text`.
- Dense article search не восстановлен; semantic retrieval работает на уровне
  Person/Event, а не статей.
