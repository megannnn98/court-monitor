# Changelog

Все заметные изменения проекта. Формат — Keep a Changelog; версии — SemVer.

## [Unreleased]

### Added — `run-all` CLI command
- `court-monitor run-all [--live]` — один прогон всего пайплайна: все
  sudrf-источники (`config/sources.yaml`, как `fetch-all`) + все Telegram-
  каналы из `config/source_registry.yaml` (раньше не было общей команды,
  только `fetch-source <name>` по одному) + `generate-matches`. Fixtures по
  умолчанию (без сети), `--live` — реальные HTTP-запросы.

### Added — D-011: source_blocked → ReviewItem
- `sources.base.FetchProblem` — новый тип рядом с `FetchResult`. Адаптеры
  (`SudrfAdapter`, `TelegramChannelAdapter`) теперь возвращают
  `Iterator[FetchResult | FetchProblem]`: при `FetchHealth.blocked`/
  `http_error`/`timeout`/пустом теле — `yield FetchProblem(...)` вместо
  молчаливого `continue`/`return None`. `304 Not Modified` и отсутствующая
  локальная fixture (dev/test) по-прежнему не считаются проблемой.
- `process_source`/`process_registry_source` на `FetchProblem` создают
  `ReviewItem(item_type="source_blocked")` через `repo.upsert_review_item`.
  Новое поле `SourceStats.blocked`, выводится в `fetch-source`/`fetch-all`.
- `repo.upsert_review_item`: дедуп теперь и по `source_id` (когда нет
  `document_id`) — повторные блокировки одного источника при регулярных
  `fetch-source` не плодят дубликаты pending review items.

### Added — Etap 4, срез 1: ReviewItem + AuditLog
- `ReviewItem` — generic очередь проверки оператора (`migrations/0006_review_items.py`).
  Создаётся автоматически в `parse_and_extract` при `parser_status=parser_failed`.
  CLI: `list-review-items [--status] [--type]`, `resolve-review-item <id> [--dismiss] [--comment] [--operator]`.
- `AuditLog` — append-only аудит операторских решений (`migrations/0007_audit_log.py`).
  Пишется атомарно из `repo.update_match_status` (confirm/reject-match) и
  `repo.resolve_review_item`, с `actor`/`correlation_id`.
- `confirm-match`/`reject-match` получили флаг `--operator` (default: `getpass.getuser()`).
- Person/PersonAlias/Case/PersonCase/CourtEvent намеренно не введены в этом
  срезе — см. `docs/technical-debt.md` D-001 (нужен либо экстрактор
  case/event-фактов, либо политика авто-создания Person из подтверждённого
  MatchCandidate).

### Fixed
- `parse_and_extract` больше не прогоняет non-sudrf документы (Telegram) через
  sudrf-специфичный структурный HTML-парсер — для них используется уже
  очищенный `doc.text`, заполненный адаптером при ingest. Раньше это
  подмешивало Telegram UI-мусор (имя канала, "VIEW IN TELEGRAM", плейсхолдеры
  медиа) в текст, используемый для relevance/article/date/name-экстракции.
  Regression-тесты: `tests/integration/test_telegram_pipeline.py`.
- CLI `doctor`: убрано дублирование блока problems-check/settings_source
  (печаталось дважды).
- `process_registry_source`: `print("already_exists")` заменён на
  structured log (`pipeline.registry_source.already_exists`).

### Added — тесты
- Расширено покрытие `matching/`: birthplace scoring, `BirthDateEvidence`
  парсинг (ISO/DD.MM.YYYY/year-only/unparseable), full-name-vs-initial
  matching, invariant-тест на недостижимость порога кандидата при surname
  mismatch.
- Расширено покрытие `fedsfm`: DBF/ZIP edge cases (пустой/битый/
  mislabeled-as-xml архив), автодетект формата по magic byte/содержимому,
  YYYYMMDD-даты, CSV-строки без ФИО, `PersonRow.dedup_key`.

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
