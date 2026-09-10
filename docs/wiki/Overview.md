# Overview

## Поток данных (ingestion)

```plantuml
@startuml
title Полный runtime-поток

rectangle "main()" as Main
rectangle "SourceReference" as Reference
component "IngestionPipeline.run()" as Pipeline
component "WebsiteAdapter.fetch()" as Adapter
rectangle "RawDocument" as Raw
component "OvdInfoArticleParser.parse()" as Parser
rectangle "ParsedArticle" as Article
component "Chunker.split()" as Chunker
rectangle "IngestionResult" as Result

Main --> Reference : создаёт
Main --> Pipeline : asyncio.run()
Pipeline --> Adapter : await fetch(reference)
Adapter --> Raw : возвращает
Pipeline --> Parser : parse(raw)
Parser --> Article : возвращает
Pipeline --> Chunker : split(article)
Chunker --> Result : ArticleChunk[]
Pipeline --> Result : article + chunks
Result --> Main : возвращается

@enduml
```

*(взято из `docs/uml/Полный runtime-поток.puml`)*

## Модули (`src/`)

| Модуль | Роль |
|---|---|
| `main.py` | CLI: команды `ingest`, `search`, `evaluate-search` |
| `models.py` | Pydantic-модели домена (`SourceReference` … `SearchHit`) — см. [CONTEXT.md](../../CONTEXT.md) |
| `website_adapter.py` | HTTP-загрузка публикации (`httpx`) |
| `article_parser.py` | Разбор HTML ОВД-Инфо (`selectolax`) |
| `chunker.py` | Разбиение статьи на фрагменты |
| `ingestion_pipeline.py` | Оркестратор: adapter → parser → chunker → persistence |
| `persistence.py` | `Protocol IngestionPersistence` |
| `sqlalchemy_persistence.py` | Реализация persistence поверх SQLAlchemy/Postgres |
| `orm_models.py` | ORM-модели (таблицы) |
| `database.py` | Engine/session factory |
| `search_backend.py` | `Protocol SearchBackend` |
| `postgres_lexical_search.py` | Lexical-поиск (tsvector) |
| `qdrant_dense_search.py`, `qdrant_chunk_indexer.py` | Dense-поиск и индексация в Qdrant |
| `text_embedder.py`, `dense_config.py` | Эмбеддинги (`sentence-transformers`), конфиг dense-поиска из env |
| `search_evaluator.py`, `evaluation_*.py` | Оценка качества поиска (MRR) |

Детали — на страницах [Ingestion](Ingestion.md), [Data-Model](Data-Model.md), [Search](Search.md), [Evaluation](Evaluation.md).

## Стек

Python 3.13, PostgreSQL 18 (tsvector/GIN), Qdrant 1.19, SQLAlchemy 2.x + Alembic, `sentence-transformers`, `selectolax`, `httpx`, Pydantic 2.
