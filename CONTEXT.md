# court-monitor

Универсальный source layer: обнаружение и загрузка статей из нескольких источников (ОВД-Инфо, SOTA — sota.vision, публичные Telegram-каналы, пресс-службы судов на движке sudrf.ru), их разбора и сохранения; из них — новые уголовные дела, сверка с Росфинмониторингом и опознание безымянных фигурантов.

## Language

**SourceReference**:
Ссылка на публикацию до загрузки — внешний идентификатор и URL, переданные вызывающей стороной (CLI).
_Avoid_: URL, ссылка

**RawDocument**:
Сырой HTTP-ответ по `SourceReference` — байты содержимого, content-type и время загрузки, ещё не разобранные.
_Avoid_: HTML, ответ

**ParsedArticle**:
Статья после разбора HTML: заголовок, дата публикации, полный очищенный текст. Одна статья соответствует одному `RawDocument`. `text` — единственный source of truth содержимого статьи; разбиение на фрагменты (chunking) в доменную модель и в БД не входит — если конкретному алгоритму понадобятся фрагменты, они вычисляются временно в памяти (`split(article.text)`) и не сохраняются.
_Avoid_: документ, статья (без уточнения стадии)

**Source**:
Источник публикаций верхнего уровня (например, ovd.info) — имя и базовый URL. Хранится в таблице `sources`.

**SourceDocument**:
Запись о загруженной публикации в БД: внешний ID, канонический URL, сырое содержимое. Персистентный аналог `RawDocument`, связан с `Source`.
_Avoid_: документ (без уточнения — используй только когда стадия ясна из контекста)

**SourceAdapter**:
Протокол источника: `discover(limit)` находит ссылки на статьи (листинг + pagination) → `list[SourceReference]`; `fetch(reference)` (унаследовано от `DocumentFetcher`) загружает одну статью → `RawDocument`. Один источник = один `SourceAdapter` + один `ArticleParser`, зарегистрированные в `sources/source_registry.py`.

**SourceIngestion**:
Оркестратор пакетной загрузки: `SourceAdapter.discover` → по каждой ссылке `IngestionPipeline.run`. Ошибка одной статьи (`IngestionError`) не прерывает остальные — попадает в `SourceIngestionResult.failures`.

**IngestionPipeline**:
Оркестратор одной статьи: `DocumentFetcher.fetch` → `ArticleParser.parse` → `IngestionPersistence.save`. Результат — `IngestionResult`. Источник-агностичен — конкретный fetcher/parser передаются снаружи.

**MonitoringRun**:
Один прогон automated monitoring (ADR 0013) по источнику (`scope = source:<name>`) или только derived-этапов (`scope = derived`): статус, счётчики, метрики этапов, упавшие объекты (`monitoring_run_items`). Orchestration state, не доменные данные; одновременно не больше одного `running` на scope.
_Avoid_: job, задача

**MonitoringFinding**:
Факт «в monitoring workflow появился actionable результат» — Person впервые удовлетворил критерию monitoring (MVP: `political_persecution_not_in_rf` / `enbv-v1`). Дедуплицируется по типу, Person и версии критерия; хранит `first_seen_run_id` и `active`. Не статус Person.
_Avoid_: alert, кандидат (кандидат — результат `CandidateQueryService` в моменте)

**Operator console**:
Локальный web-интерфейс для оператора, который управляет живым pipeline и очередями
ревью на приватной базе: запускает рабочие стадии, видит состояние данных, разбирает
review queue и проверяет факты через evidence. Не является полным web-аналогом CLI:
developer/evaluation/golden-corpus команды остаются инструментами разработки.
_Avoid_: admin panel, dashboard, полный web CLI
