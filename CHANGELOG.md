# Changelog

Все заметные изменения проекта. Формат — Keep a Changelog; версии — SemVer.

## [Unreleased]

### Fixed — `generate-matches` всегда печатал «Документов обработано: -»
- `generate_matches` возвращал голый `dict[str, int]`, допустимые ключи
  которого жили только в докстринге. CLI спрашивал у него
  `documents_processed` — ключ, которого функция никогда не устанавливала, —
  и печатал прочерк на каждом запуске, неотличимо от настоящего нуля.
- Возвращается `MatchStats` (dataclass, как `SourceStats`/`RfmImportStats`/
  `ReprocessAllStats`); строка удалена, а не «починена» — счётчика не
  существовало. Регрессия закрыта `tests/unit/test_pipeline_stats.py`.

### Fixed — кнопка «Собрать источники» в веб-интерфейсе не читала fixtures
- Фоновая задача `fetch_all` вызывала `process_registry_source` вообще без
  `fixture_path`, поэтому при выключенном `live` адаптеру нечего было читать:
  он писал в лог `telegram.fixture.missing` и возвращал пустоту. Оператор
  нажимал кнопку и получал ноль постов из Telegram, тогда как та же операция
  из CLI работала.
- Оба входа теперь идут через `services/work.py::plan_all_work`, который
  выдаёт путь к сохранённому превью одинаково для обоих. Это то же
  расхождение CLI и фоновой задачи, ради которого в своё время появился
  `SourceStats`. Регрессионный тест —
  `test_the_job_and_the_cli_now_read_the_same_fixture`.

### Fixed — «другие упоминания» молча обрывались на 2000 строках
- `find_other_mentions` читал 2000 строк и фильтровал их в Python, потому что
  `ExtractedFact.value` — JSON и по нему нельзя искать. На корпусе больше
  2000 фактов-имён оператор видел произвольную выборку, без единого признака
  того, что список неполон.
- Добавлена индексируемая колонка `extracted_facts.normalized_value`
  (миграция `0011`, с backfill уже собранного корпуса — переразбор не нужен).
  Значение выводится в `before_insert`/`before_update` на самой модели, а не в
  репозитории: факт, созданный любым другим путём, иначе оказывался невидим
  для поиска, и ничто об этом не сообщало.

### Added — извлечение места рождения подключено к скорингу
- `_extract_place_from_fact` перестал быть заглушкой, возвращавшей `None`:
  читает «уроженец …» / «родился в …» из quote вокруг имени — тем же приёмом,
  что и дата рождения, поэтому место из чужого абзаца не прилипнет к другому
  человеку. `_place_key` приводит обе стороны к общей основе: реестр пишет
  `Г. МОСКВА`, документ — `уроженец г. Москвы`, без общего ключа они не
  совпадали никогда.
- **Практический эффект сейчас нулевой**: замер по 379 документам дал место
  рождения в 0 из них — это формулировка судебных актов, а судебные источники
  не работают. Плечо реализовано и покрыто тестами, ждёт источник.

### Changed — импорт РФМ больше не делает запрос на строку
- `import_rfm_records` читал существующие записи по одной
  (`find_person_record` на каждую из ~22k строк) и делал `flush` на вставку.
  После `--replace` все 22k запросов заведомо ничего не находили.
- Записи источника загружаются одним запросом
  (`load_person_record_index`), ключ дедупликации вынесен в
  `person_record_key`, чтобы SQL-поиск и индекс в памяти не разошлись — на это
  есть отдельный тест. Правила дедупликации не менялись.

### Changed — структурная уборка по итогам code-quality аудита
- `cli/app.py` держал собственные копии шести хелперов, уже вынесенных в
  `cli/_shared.py`, и пользовался только копиями; менеджеры сессии успели
  разойтись. Копии удалены (580 → ~500 строк).
- Мёртвый слой в конвейере: `find_existing_document` вычислял `match_kind`,
  который никто не читал, а `SourceStats` носил `already_exists_ids` (писалось,
  не читалось) и `changed_ids` (не трогалось вовсе). Докстринги обещали
  поведение, которого не было. Удалено вместе с callback-параметром.
- `run-all` и фоновая задача `fetch_all` обходили оба типа источников каждый
  сам по себе. Обход переехал в `services/work.py::plan_all_work`; у
  вызывающих осталось только отображение.
- Прочее без изменения поведения: тройной каскад подсчёта `parser_status` →
  `ParseOutcomeCounters`; пять копий суффиксного цикла в `name_normalizer` →
  одна таблица правил; ручная пересборка frozen dataclass в `config/registry`
  → `dataclasses.replace`; `confirm`/`reject` в CLI → общая функция;
  `PERSON_NAME_FIELD` переехал в `domain/models.py` (импорт из `storage.orm` в
  экстракторы нарушил бы правило слоёв, записанное там же); `overlaps` — в
  `extraction/_utils`.

### Fixed — RFM-импорт молча терял тёзок без даты рождения
- Найдено ревью коммита, добавившего `rosfinmonitoring-2.csv` (22 250
  реальных записей, новый формат без поля даты рождения): дедуп
  `PersonRecord` был завязан только на `(source, normalized_name,
  birth_date)`. При `birth_date=None` для всех строк нового формата это
  превращалось в дедуп по одному ФИО — на реальном датасете 94 различных
  человека-тёзки молча не попадали в базу (проверено: «Яковлев Александр
  Николаевич» из двух разных регионов схлопывались в одну запись).
- `repo.find_person_record`/`upsert_person_record` и unique constraint на
  `person_records` (`migrations/0010_person_records_dedup_birthplace.py`)
  теперь учитывают ещё и `birth_place`. Место рождения участвует в ключе
  **только при отсутствии даты**: при известной дате человек и так опознан,
  а место — изменяемая деталь, и безусловный ключ превращал исправление
  места в дубликат человека (ловится `test_import_refreshes_existing_record_fields`).
- Заодно: тесты нового CSV-формата замедляли весь unit-набор в ~4 раза
  (каждый тест независимо парсил весь 22 250-строчный файл заново) —
  вынесено в module-scoped fixture; локальный `import json as _json`
  поднят на уровень модуля в `fedsfm.py` и в тесте.

### Added — замена перечня РФМ вместо дозаписи
- `fetch-source fedsfm --replace [--force]`: источник публикует список
  целиком, а не приращение. Дозапись поверх CSV-загрузки задваивала людей
  (21 277 из 22 156 имён пересеклись, база росла до 43 667), и CLI об этом
  только предупреждал, не давая способа поступить иначе.
- Веб-задача импорта уже имела параметр `replace` и удаляла записи реестра
  напрямую, без всякой защиты — а удаление `PersonRecord` каскадит в
  кандидатов, то есть молча уничтожало решения оператора. Переведена на
  общий `purge_person_records`, который по умолчанию отказывается.

### Added — `reprocess-all`
- Переразбор всего корпуса текущими правилами извлечения: правка
  экстрактора живёт в коде и сама по себе не доходит до уже собранных
  данных. Защита от потери решений оператора проверяется один раз на весь
  корпус, а не по документу.

### Added — `run-all` CLI command
- `court-monitor run-all [--live] [--verbose]` — один прогон всего пайплайна:
  все sudrf-источники (`config/sources.yaml`, как `fetch-all`) + все Telegram-
  каналы из `config/source_registry.yaml` (раньше не было общей команды,
  только `fetch-source <name>` по одному) + `generate-matches`. Fixtures по
  умолчанию (без сети), `--live` — реальные HTTP-запросы.
- Явный, цветной вывод вместо голого JSON-лога: заголовки секций (cyan),
  построчный статус на источник/канал (green — чисто, yellow —
  blocked/failed/пропущено из-за отсутствующей fixture), красным — реальные
  исключения. По умолчанию INFO/WARNING-логи глушатся (виден только
  структурированный текст выше и ERROR-трейсбеки), `--verbose` возвращает
  обычный уровень логирования.

### Fixed — экстракция ФИО ловила ALL-CAPS дисклеймер как имя
- Обнаружено на реальных данных: `run-all --live` по всем Telegram-каналам
  дал десятки ложных «person»-фактов — юридический ALL-CAPS дисклеймер
  «НАСТОЯЩИЙ МАТЕРИАЛ... РАСПРОСТРАНЕН ИНОСТРАННЫМ АГЕНТОМ...» (обязателен
  для СМИ-иноагентов) и аббревиатуры (ООО, СБУ, «РБК-Украина»,
  «ЛГБТ-активистки») извлекались как ФИО.
- `extraction/names.py`: новый фильтр `_is_boilerplate_caps` — отклоняет
  кандидата, если у него есть токен (или дефисная часть токена) с более
  чем одной ALL-CAPS буквой. Реальные ФИО — всегда Title Case; инициалы
  («И.») однобуквенные и не задеваются. Не пытается перечислить варианты
  формулировки дисклеймера как стоп-слова (хрупко) — используется общий,
  генерализуемый сигнал.
- Остаточная, не исправленная в этом патче проблема того же класса:
  склейка топонима в родительном падеже с реальным именем («Владивостока
  Татьяны Намазбаевой») — не ALL-CAPS, требует геозетеера/морфоанализа
  (Etap 6), задокументирована в `known-risks-and-notes.md`.

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
