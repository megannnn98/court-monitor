# court-monitor — восстановленная версия

Это восстановленный первый вертикальный срез проекта после случайного удаления файлов.

Универсальный source layer (этап 9): discover → fetch → parse (целиком, без chunking) → PostgreSQL → lexical search. Два источника на одной архитектуре: ОВД-Инфо (`ovd-info`) и SOTA (`sota-vision`). Подробнее — [docs/wiki](docs/wiki/Home.md), особенно [Ingestion](docs/wiki/Ingestion.md) и [Setup](docs/wiki/Setup.md) (CLI-примеры).

Текущий поток данных:

```text
SourceAdapter.discover(limit)
→ list[SourceReference]
→ SourceIngestion.run() -> для каждой ссылки:
    DocumentFetcher.fetch()   (retry на transient-ошибках)
    → RawDocument
    → ArticleParser.parse()
    → ParsedArticle
    → IngestionPersistence.save()
```
