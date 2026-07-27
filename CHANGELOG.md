# Changelog

Все заметные изменения проекта. Формат — Keep a Changelog; версии — SemVer.

## [Unreleased]

### Added — Etap 0: Discovery
- `docs/discovery.md` — исследование сред исполнения и источников (sudrf.ru, военные суды, Росфинмониторинг, Airtable, Telegram); fixtures-first архитектурное решение; модель данных и план первых трёх этапов.
- `docs/airtable-discovery.md` — исследование двух существующих баз Airtable (`app42KQc45WUgqx7A`, `apppAy5vZCrpb53wc`); слой `AirtableFieldMapping`; CLI-путь к безопасной синхронизации.
- Obsidian vault проекта.

### Added — Etap 1: каркас + вертикальный срез
- Каркас пакета `src/court_monitor/` (config, domain, storage, sources, parsers, extraction, services, cli, api, observability).
- Доменные enum'ы (`VerificationStatus`, `EventType`, `ParserStatus`, `SourceType`) и Pydantic v2 DTO `ExtractedFact` (validation_status, confidence, quote, source, extraction_method).
- SQLAlchemy 2 ORM-модели `SourceDocument`, `ExtractedFact`; Alembic с начальной миграцией.
- `SourceAdapter` ABC + `FetchResult`; generic-адаптер платформы sudrf.ru (режимы `fixture` и `http`), httpx-клиент (UA, timeout, delay, retry через tenacity).
- Парсер пресс-релиза sudrf (заголовок, дата, текст) на selectolax; экстракторы: статьи УК, даты (русский формат), ФИО (эвристика); фильтр релевантности по `config/monitoring.yaml`.
- Pipeline-сервис: ingest (дедупликация по `(url, content_hash)`) → parse → запись `ExtractedFact`.
- CLI на Typer: `init-db`, `migrate`, `doctor`, `fetch-source`, `fetch-all`, `parse-pending`, `reprocess-document`, `show-stats`, `show-document`.
- FastAPI: `GET /health`, `GET /documents`, `GET /documents/{id}`, `GET /stats` + OpenAPI (`/docs`).
- Структурированные JSON-логи (structlog) + correlation_id.
- Dockerfile + `docker-compose.yml` (postgres + app); GitHub Actions CI (ruff/format/mypy/pytest на матрице 3.12–3.14, docker build, gitleaks).
- Unit-тесты (статьи/даты/фильтр/DTO) и integration-тест (полный путь пресс-релиз → SourceDocument → факты + дедупликация).
- Синтетический (обезличенный) fixture пресс-релиза суда.

### Notes
- Airtable работает через mock-адаптер до предоставления PAT.
- LLM отключена; экстракция только на regex/правилах.
- Live-доступ к сайтам военных судов из тестового окружения недоступен (404); всё работает на fixtures.
