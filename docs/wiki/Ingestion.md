# Ingestion

Загрузка одной публикации ОВД-Инфо и подготовка её к сохранению и поиску.

## Шаги

```plantuml
@startuml
title Первый вертикальный срез court-monitor

rectangle "CLI\nURL" as CLI
rectangle "SourceReference" as Reference
component "IngestionPipeline" as Pipeline
component "WebsiteAdapter" as Adapter
rectangle "RawDocument\nHTML bytes" as Raw
component "OvdInfoArticleParser" as Parser
rectangle "ParsedArticle" as Article
component "Chunker" as Chunker
rectangle "ArticleChunk[]" as Chunks
rectangle "IngestionResult" as Result
rectangle "Терминал" as Terminal

CLI --> Reference
Reference --> Pipeline : run(reference)
Pipeline --> Adapter : await fetch()
Adapter --> Raw
Pipeline --> Parser : parse(raw)
Parser --> Article
Pipeline --> Chunker : split(article)
Chunker --> Chunks
Article --> Result
Chunks --> Result
Result --> Terminal

@enduml
```

*(взято из `docs/uml/Первый вертикальный срез court-monitor.puml`)*

### 1. `WebsiteAdapter.fetch()`

`src/website_adapter.py`. Асинхронный GET (`httpx`, timeout 5s, фиксированный `User-Agent`). Возвращает `RawDocument`: `content` (сырые байты), `content_type`, `fetched_at` (UTC, момент загрузки), `url` (после редиректов — `str(response.url)`).

### 2. `OvdInfoArticleParser.parse()`

`src/article_parser.py`. HTML-парсинг через `selectolax`, селекторы жёстко заданы под вёрстку ovd.info:

- заголовок: `h1.express-text-heading`
- дата публикации: `#article_published`, формат `%d.%m.%Y, %H:%M`, таймзона `Europe/Moscow`
- параграфы текста: `.field--name-field-express-text > p`

Бросает `ValueError`, если не найден заголовок или нет ни одного параграфа — т.е. парсер жёстко завязан на структуру одного сайта (не универсальный HTML-экстрактор).

### 3. `Chunker.split()`

`src/chunker.py`. Делит `ParsedArticle.text` по `\n\n` (граница параграфов, выставленная парсером), отбрасывает пустые строки, нумерует результат как `ArticleChunk(ordinal=...)`.

```plantuml
@startuml
title Разбиение статьи на chunks

rectangle "ParsedArticle\ntext с разделителем \\n\\n" as Article
component "Chunker.split()" as Chunker
rectangle "list[ArticleChunk]" as Chunks
rectangle "ArticleChunk 0" as Chunk0
rectangle "ArticleChunk 1" as Chunk1
rectangle "ArticleChunk 2" as Chunk2

Article --> Chunker
Chunker --> Chunks
Chunks --> Chunk0
Chunks --> Chunk1
Chunks --> Chunk2

@enduml
```

*(взято из `docs/uml/разбиение статьи.puml`)*

### 4. Persistence

`IngestionPipeline.run()` передаёт `raw_document`, `parsed`, `chunks` в `IngestionPersistence.save()` — подробности на странице [Data-Model](Data-Model.md).

## Идемпотентность

`IngestionPipeline` не проверяет, была ли публикация уже загружена — идемпотентность (upsert по `external_id`, замена chunks) реализована на уровне persistence, не здесь.
