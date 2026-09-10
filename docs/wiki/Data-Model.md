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
    created_at : TIMESTAMP WITH TIME ZONE NOT NULL
    --
    UNIQUE(document_id)
}

entity "article_chunks" as article_chunks {
    * id : INTEGER <<PK>>
    --
    parsed_article_id : INTEGER NOT NULL <<FK>>
    ordinal : INTEGER NOT NULL
    text : TEXT NOT NULL
    search_vector : TSVECTOR GENERATED ALWAYS AS (to_tsvector('russian', text)) STORED
    created_at : TIMESTAMP WITH TIME ZONE NOT NULL
    --
    UNIQUE(parsed_article_id, ordinal)
    GIN INDEX(search_vector)
}

sources ||--o{ source_documents : source_id
source_documents ||--o| parsed_articles : document_id
parsed_articles ||--o{ article_chunks : parsed_article_id

@enduml
```

> **Внимание, расхождение:** файл `docs/uml/top to bottom direction.puml` в репозитории описывает **другую**, более раннюю схему — с таблицей `document_snapshots` (снапшоты по `content_hash`) между `source_documents` и `parsed_articles`, и `raw_content` в `document_snapshots`, а не в `source_documents`. Ни миграции (`migrations/versions/`), ни `orm_models.py` такой таблицы не содержат — судя по всему, это след утраченного/незавершённого функционала версионирования снапшотов. Диаграмма выше построена заново по фактическим миграциям (`fdac899276a2`, `df2c42a78b48`) и коду; старый `.puml`-файл не тронут, но как источник правды для текущей схемы использовать нельзя.

Таблицы созданы миграциями:
- `fdac899276a2_create_ingestion_tables.py` — `sources`, `source_documents`, `parsed_articles`, `article_chunks`
- `df2c42a78b48_add_article_chunk_search_vector.py` — добавляет `search_vector` (generated column) + GIN-индекс

## Persistence: `SqlAlchemyIngestionPersistence.save()`

`src/sqlalchemy_persistence.py`. Одна транзакция (`session_factory.begin()`), upsert по естественным ключам на каждом уровне:

1. **Source** — ищется по `base_url`, создаётся при отсутствии.
2. **SourceDocument** — ищется по `(source_id, external_id)`; при повторной загрузке той же публикации обновляются `canonical_url`, `fetched_at`, `content_type`, `raw_content` (перезапись, не версионирование — старое содержимое не хранится).
3. **ParsedArticleRecord** — ищется по `document_id` (1:1 с документом); при повторном разборе текст/заголовок перезаписываются.
4. **ArticleChunkRecord** — старые chunks статьи **удаляются** (`DELETE ... WHERE parsed_article_id = ...`) и вставляются заново. Это делает переиндексацию в Qdrant после повторного ingestion обязательной (id чанков меняются) — см. [Search](Search.md).

Таким образом повторный `ingest` той же публикации — не добавление новой версии, а замена: одна строка `source_documents`/`parsed_articles` на публикацию, актуальные chunks.
