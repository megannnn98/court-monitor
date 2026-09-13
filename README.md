# court-monitor — восстановленная версия

Это восстановленный первый вертикальный срез проекта после случайного удаления файлов.

Универсальный source layer (этап 9): discover → fetch → parse (целиком, без chunking) → PostgreSQL → lexical search. Этап 10 добавляет deterministic extraction: из полного текста статьи извлекаются упоминания людей, организаций, судов, мест, правовых ссылок и базовых событий. Два источника на одной архитектуре: ОВД-Инфо (`ovd-info`) и SOTA (`sota-vision`). Подробнее — [docs/wiki](docs/wiki/Home.md), особенно [Ingestion](docs/wiki/Ingestion.md), [Extraction](docs/wiki/Extraction.md) и [Setup](docs/wiki/Setup.md) (CLI-примеры).

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

Extraction-поток:

```text
ParsedArticle
→ ExtractionDocument
→ RuleBasedEntityExtractor
→ RawMention[]
→ RuleBasedMentionNormalizer
→ NormalizedMention[]
→ RuleBasedEventExtractor
→ SqlAlchemyExtractionPersistence
```

CLI:

```bash
uv run python src/main.py extract-entities --article-id 123
uv run python src/main.py extract-entities --source ovd-info --limit 100
uv run python src/main.py evaluate-extraction
```
