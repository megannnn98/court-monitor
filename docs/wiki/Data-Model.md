# Data Model / Persistence

## Схема (актуальная, по миграциям + `orm_models.py`)

```plantuml
@startuml
top to bottom direction

hide methods
hide stereotypes

skinparam linetype ortho
skinparam classAttributeIconSize 0

entity "sources" as sources {
    * id : INTEGER <<PK>>
    --
    name : VARCHAR(255) NOT NULL
    base_url : VARCHAR(2048) NOT NULL
    created_at : TIMESTAMP WITH TIME ZONE NOT NULL
    --
    UNIQUE(base_url)
}

entity "source_documents" as source_documents {
    * id : INTEGER <<PK>>
    --
    source_id : INTEGER NOT NULL <<FK>>
    external_id : VARCHAR(255) NOT NULL
    canonical_url : VARCHAR(2048) NOT NULL
    fetched_at : TIMESTAMP WITH TIME ZONE NOT NULL
    content_type : VARCHAR(255) NOT NULL
    raw_content : BYTEA NOT NULL
    discovered_at : TIMESTAMP WITH TIME ZONE NOT NULL
    created_at : TIMESTAMP WITH TIME ZONE NOT NULL
    --
    UNIQUE(source_id, external_id)
}

entity "parsed_articles" as parsed_articles {
    * id : INTEGER <<PK>>
    --
    document_id : INTEGER NOT NULL <<FK>>
    title : TEXT NOT NULL
    published_at : TIMESTAMP WITH TIME ZONE NULL
    text : TEXT NOT NULL
    search_vector : TSVECTOR GENERATED ALWAYS AS (to_tsvector('russian', text)) STORED
    created_at : TIMESTAMP WITH TIME ZONE NOT NULL
    --
    UNIQUE(document_id)
    GIN INDEX(search_vector)
}

sources ||--o{ source_documents : source_id
source_documents ||--o| parsed_articles : document_id

entity "article_extraction_runs" as extraction_runs {
    * id : INTEGER <<PK>>
    --
    article_id : INTEGER NOT NULL <<FK>>
    article_content_hash : VARCHAR(64) NOT NULL
    extractor_name : VARCHAR(255) NOT NULL
    extractor_version : VARCHAR(64) NOT NULL
    normalizer_version : VARCHAR(64) NOT NULL
    status : VARCHAR(32) NOT NULL
    started_at : TIMESTAMP WITH TIME ZONE NOT NULL
    finished_at : TIMESTAMP WITH TIME ZONE NULL
    error_message : TEXT NULL
    created_at : TIMESTAMP WITH TIME ZONE
    --
    UNIQUE(article_id, article_content_hash, extractor_name, extractor_version, normalizer_version)
}

entity "entity_mentions" as entity_mentions {
    * id : INTEGER <<PK>>
    --
    extraction_run_id : INTEGER NOT NULL <<FK>>
    entity_type : VARCHAR(64) NOT NULL
    surface_text : TEXT NOT NULL
    normalized_text : TEXT NOT NULL
    start_offset : INTEGER NOT NULL
    end_offset : INTEGER NOT NULL
    confidence : FLOAT NOT NULL
    normalized_data : JSONB NOT NULL
    extractor_name : VARCHAR(255) NOT NULL
    extractor_version : VARCHAR(64) NOT NULL
    normalizer_version : VARCHAR(64) NOT NULL
    created_at : TIMESTAMP WITH TIME ZONE
    --
    UNIQUE(extraction_run_id, entity_type, start_offset, end_offset)
}

entity "extracted_events" as extracted_events {
    * id : INTEGER <<PK>>
    --
    extraction_run_id : INTEGER NOT NULL <<FK>>
    event_type : VARCHAR(64) NOT NULL
    event_date : TIMESTAMP WITH TIME ZONE NULL
    start_offset : INTEGER NOT NULL
    end_offset : INTEGER NOT NULL
    confidence : FLOAT NOT NULL
    attributes : JSONB NOT NULL
    extractor_name : VARCHAR(255) NOT NULL
    extractor_version : VARCHAR(64) NOT NULL
    created_at : TIMESTAMP WITH TIME ZONE
}

entity "event_entity_mentions" as event_entity_mentions {
    * event_id : INTEGER <<PK, FK>>
    * mention_id : INTEGER <<PK, FK>>
    * role : VARCHAR(64) <<PK>>
}

parsed_articles ||--o{ extraction_runs : article_id
extraction_runs ||--o{ entity_mentions : extraction_run_id
extraction_runs ||--o{ extracted_events : extraction_run_id
extracted_events ||--o{ event_entity_mentions : event_id
entity_mentions ||--o{ event_entity_mentions : mention_id

@enduml
```

> **Внимание, расхождение:** файл `docs/uml/top to bottom direction.puml` в репозитории описывает **другую**, более раннюю схему — с таблицей `document_snapshots` (снапшоты по `content_hash`) между `source_documents` и `parsed_articles`, `raw_content` в `document_snapshots`, а не в `source_documents`, и с таблицей `article_chunks`, которой в текущей схеме больше нет. Ни миграции (`migrations/versions/`), ни `orm_models.py` такой таблицы/структуры не содержат. Диаграмма выше построена заново по фактическим миграциям и коду; старый `.puml`-файл не тронут, но как источник правды для текущей схемы использовать нельзя.

Таблицы созданы/изменены миграциями:
- `fdac899276a2_create_ingestion_tables.py` — `sources`, `source_documents`, `parsed_articles`, `article_chunks`
- `df2c42a78b48_add_article_chunk_search_vector.py` — добавляет `search_vector` (generated column) + GIN-индекс на `article_chunks`
- `a3f7c9d21b44_drop_article_chunks_add_article_search_vector.py` — удаляет `article_chunks`, переносит `search_vector` (generated column) + GIN-индекс на `parsed_articles`
- `f0b1c2d3e4f5_add_extraction_tables.py` — добавляет `article_extraction_runs`, `entity_mentions`, `extracted_events`, `event_entity_mentions`

`article_chunks` — это уже история: таблица существовала до удаления `ArticleChunk` из домена, см. [ADR 0002](../adr/0002-drop-dense-hybrid-search.md). Старые миграции не переписаны задним числом.

## Persistence: `SqlAlchemyIngestionPersistence.save()`

`src/sqlalchemy_persistence.py`. Одна транзакция (`session_factory.begin()`), upsert по естественным ключам на каждом уровне:

1. **Source** — ищется по `base_url`, создаётся при отсутствии.
2. **SourceDocument** — ищется по `(source_id, external_id)`; при повторной загрузке той же публикации обновляются `canonical_url`, `fetched_at`, `content_type`, `raw_content` (перезапись, не версионирование — старое содержимое не хранится).
3. **ParsedArticleRecord** — ищется по `document_id` (1:1 с документом); при повторном разборе `title`/`published_at`/`text` перезаписываются, `search_vector` пересчитывается автоматически (generated column).

Таким образом повторный `ingest` той же публикации — не добавление новой версии, а замена: одна строка `source_documents`/`parsed_articles` на публикацию.

## Extraction persistence

`SqlAlchemyExtractionPersistence.save()` сохраняет результат extraction одной транзакцией. Идемпотентность задаёт ключ:

```text
article_id + article_content_hash + extractor_name + extractor_version + normalizer_version
```

Повторный запуск той же версии возвращает существующий successful run и не создаёт дубликаты. Если `ParsedArticle.text` изменился, `content_hash` меняется и создаётся новый run. Старые результаты не смешиваются с новыми.
