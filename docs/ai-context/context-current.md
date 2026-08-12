# Контекст — 2026-08-12

## Что сделано

**2026-08-12: Реализован мониторинг судебных дел (Etап 5).** Проект теперь действительно мониторит суды:
- ✅ Исправлены URL судебных источников (добавлен региональный поддомен `.msk`, `.ros`)
- ✅ Реализован `SudrfPressCrawler` для обхода архивов пресс-релизов с инкрементальным сбором
- ✅ Реализован парсер карточек дел `sud_delo` с извлечением ФИО, статей, дат, судей
- ✅ Добавлены модели БД: `Case`, `PersonCase`, `CourtEvent` (миграция 0014)
- ✅ Реализован explainable matching для связывания пресс-релизов с карточками дел
- ✅ 472 теста проходят, полное покрытие pipeline

Подробности: `docs/ai-context/court-case-monitoring-implementation.md`, `docs/sudrf-live-discovery.md`

**2026-08-09:** ручной UX очереди совпадений стал операторским workflow вместо сырой debug-таблицы. `/matches` теперь описывает себя как «Очередь решений», показывает сравнение «В документе → В реестре», человекочитаемые теги однофамильцев/упоминаний, явную подсказку про случаи, где совпали только фамилия и имя, и ссылку «Принять решение». Карточка кандидата сначала показывает сводку «Что нужно решить», а score-разбор перенесён в диагностический блок. README обновлён под этот ручной сценарий; LLM остаётся только необязательной подсказкой. Добавлен подробный операторский мануал `docs/manual-match-review.md` с тремя примерами ручного решения.

Etap 0–3 закрыты: discovery источников, каркас пакета, вертикальный срез (конфиг → домен → хранилище → источники → парсеры → экстракция → сервисы → CLI → API), структурированная экстракция (статьи УК, даты, ФИО) и фильтр релевантности по `monitoring.yaml`.

Etap 4, срез 1: `ReviewItem` (очередь ручной проверки) и `AuditLog` (append-only аудит решений), миграции `0006`/`0007`. `Person`/`Case`/`CourtEvent` намеренно не введены — см. `technical-debt.md` D-001.

D-011 закрыт: адаптеры возвращают `Iterator[FetchResult | FetchProblem]` вместо молчаливого пропуска при блокировке/ошибке/таймауте; на `FetchProblem` создаётся `ReviewItem(item_type="source_blocked")`.

Etap 5: операторский веб-интерфейс (`web/`) — обзор, документы, очередь совпадений с confirm/reject, очередь проверки, реестр с поиском, журнал аудита и фоновые операции. Подробности в `architecture.md`.

Состояние на сегодня — 472 теста, ruff + mypy чистые.

### Что закрыто в этой сессии

| Изменение | Коммит | Суть |
|---|---|---|
| Порядок токенов в двусловных ФИО | `68d62a1` | `_simple_parse` клал имя в поле `surname`, из-за чего матчинг давал **0 кандидатов**. Детали — `matching.md` |
| `doctor` ловит отставание миграций | `0b82c24` | Отставшая БД проходила все проверки и падала позже как `no such column` |
| spaCy NER (opt-in) | `2942490` | Выше precision, чем regex, но в 255 раз медленнее. Детали — `extraction.md` |
| Дедуп regex ↔ NER | `024adcb` | 17 строк кандидатов на 12 уникальных → 12/12 |
| Принуждение FK в SQLite | `cd56ea4` | `PRAGMA foreign_keys=OFF` обесценивал все объявленные каскады |
| Пиннинг цепочки fedsfm.ru | `ee1f4c3` | Единственная преграда к живому доступу была TLS, не геоблок |
| Живой парсер перечня РФМ | `3fb5172` | 21 511 физлиц из 23 220 записей HTML-страницы |
| `fetch-source fedsfm --live` | `6da5f5c` | Заменил заглушку «Live mode not yet implemented» |
| Проверка схемы перед работой | `5c98254`, `05dae34` | `run-all`/`generate-matches`/`fetch-source` падали сырым `OperationalError` после впустую потраченной сети |
| Веб-интерфейс оператора | `a041701` | Очередь ревью в браузере вместо CLI |
| Фоновые операции из веба | `fd82134` | Таблица `jobs`, один воркер, закрывает D-008 для ad-hoc запуска |
| Реестр, аудит, `list-audit-log` | `784ac4e` | Поиск по перечню и просмотр решений в вебе и CLI |
| Фикс блокировки SQLite | `c277869` | WAL + busy_timeout: фоновая задача больше не запирает UI |
| Контекст для оператора | `2d2dab3` | Однофамильцы в реестре и другие упоминания; попутно починен импорт из Airtable |
| Фикс класса символов в ФИО | `00d7ee0` | `A-Я` с латинской `A` покрывал 1006 точек; мусор в именах 48 → 0 |
| Structural review, 7 находок | `99dc0e8`…`918b179` | `SourceStats` владеет счётчиками (чинит потерянный `blocked`), общий цикл fetch, таблица проходов в статьях, разбитый scorer, doctor возвращает проблемы, CLI 1625 → 559 строк |
| README как пошаговый сценарий | `5f1e9be` | Справочник вынесен в `docs/cli-reference.md` и `docs/configuration.md`; попутно найден и починен `--dry-run` без негативной формы — `import-source-registry` не мог записать реестр |
| `reprocess-all` + прогон корпуса | `6eeca36` | Фикс `00d7ee0` жил в коде, но не в данных. 57 мусорных имён (`Жители Сочи`, `Власти Грузии`) и 32 недостающих реальных. После прогона с NER: фактов 672 → 736, имён 501 → 565, кандидатов 7 → 14 |
| Составные ссылки на статьи | `HEAD` | `ч. 1 ст. 30, ч. 2 ст. 205.5 УК РФ` давало только ст. 30 — спан первой статьи поглощал вторую. Терактовое дело отсеивалось фильтром. Релевантных 46 → 47, фактов 736 → 748 |
| Замена перечня РФМ + ключ дедупа | `e39b055` | `--replace` для `fetch-source fedsfm` (источник публикует список целиком, дозапись задваивала людей); `birth_place` в ключе при отсутствии даты (миграция `0010`); веб-задача сносила решения оператора без всякой защиты — переведена на `purge_person_records` |

Ключевой факт для планирования: **`fedsfm.ru` доступен из Казахстана без VPN**, мешал только сертификат УЦ Минцифры.

## Следующий шаг

**Интеграция case matching в UI и CLI.** Matching реализован, но ещё не интегрирован в операторский workflow:
1. CLI команда `find-case` для ручного поиска карточек дел
2. Отображение результатов matching в web UI review queue
3. Автоматическое создание `CourtEvent` при подтверждении совпадения
4. Person name matching через `PersonRecord` (сейчас упрощён)

**Автоматизация case card crawling.** Сейчас парсинг карточек дел работает, но их загрузка требует JavaScript. Варианты:
- Playwright для автоматизации формы поиска `sud_delo`
- Прямые HTTP-запросы с эмуляцией формы (если возможно)

**Мониторинг жизненного цикла дел.** После создания `Case` и `CourtEvent` можно отслеживать изменения в карточках дел (новые заседания, приговоры, апелляции).


- **`run-all` (CLI) не импортирует реестр РФМ** — на чистой машине матчить не с чем, пока вручную не выполнить `fetch-source fedsfm`.
- Airtable write-back — заблокировано отсутствием PAT.
- Etap 4, срез 2 (кандидат): `Person`/`Case`/`CourtEvent`. Нужен либо экстрактор структурированных case/event-фактов, либо политика авто-создания `Person` из подтверждённого `MatchCandidate`. `EventType` уже есть в `domain/models.py`, но не используется.
- `_extract_place_from_fact` всегда возвращает `None` — birthplace-скоринг протестирован, но в реальных кандидатах не участвует.
- **Планировщик расписания** — D-008 закрыт только для ad-hoc запуска из веба; регулярного опроса источников по-прежнему нет.
- **Отмена запущенной операции** из веба невозможна — задача доработает до конца.
- Осиротевшие `MatchCandidate` после `reprocess` — с включённым FK-каскадом проблема снята, но старые базы, наполненные до `cd56ea4`, могут содержать висячие строки.

## Блокировки

- нет

## Ключевые файлы

### Court case monitoring (Etап 5)
- `src/court_monitor/sources/sudrf.py` — `SudrfPressCrawler`, обход архивов пресс-релизов
- `src/court_monitor/parsers/sud_delo.py` — парсер карточек дел
- `src/court_monitor/parsers/sudrf_press.py` — парсер пресс-релизов (обновлённые селекторы)
- `src/court_monitor/services/case_search.py` — поиск дел по критериям
- `src/court_monitor/matching/case_matching.py` — explainable matching пресс-релизов с делами
- `src/court_monitor/storage/orm.py` — модели `Case`, `PersonCase`, `CourtEvent`
- `migrations/versions/0014_add_case_personcase_courtevent.py` — миграция для новых моделей
- `tests/fixtures/sudrf-live/` — реальные HTML fixtures судов
- `docs/sudrf-live-discovery.md` — результаты live discovery судебных сайтов
- `docs/ai-context/court-case-monitoring-implementation.md` — полное описание реализации

### Core pipeline
- `src/court_monitor/cli/app.py` — CLI entrypoint, все команды
- `src/court_monitor/services/__init__.py` — pipeline: fetch → parse → extract → match, `_dedupe_against`
- `src/court_monitor/extraction/names.py` — ФИО через regex-эвристику
- `src/court_monitor/extraction/ner_names.py` — ФИО через spaCy NER (opt-in, `CM_NER_MODE=spacy`)
- `src/court_monitor/extraction/articles.py` — статьи УК
- `src/court_monitor/extraction/dates.py` — даты (русский формат)
- `src/court_monitor/extraction/filtering.py` — фильтр релевантности
- `src/court_monitor/matching/name_normalizer.py` — нормализация `ru-name-v2` + порядок токенов
- `src/court_monitor/matching/score.py` — веса скоринга, `BirthDateEvidence`
- `src/court_monitor/matching/candidates.py` — генерация кандидатов
- `src/court_monitor/sources/fedsfm.py` — парсер файлов XML/DBF/ZIP/CSV
- `src/court_monitor/sources/fedsfm_live.py` — живая загрузка и HTML-парсер перечня
- `src/court_monitor/sources/http_client.py` — вежливый HTTP-клиент, `verify=` для своего CA
- `src/court_monitor/sources/base.py` — `FetchResult`/`FetchProblem`, `SourceAdapter`
- `src/court_monitor/storage/orm.py` — ORM-модели
- `src/court_monitor/storage/db.py` — engine, `PRAGMA foreign_keys=ON`
- `src/court_monitor/storage/migrations.py` — `upgrade_head`, `revision_status`
- `src/court_monitor/domain/models.py` — доменные enum'ы
- `src/court_monitor/api/app.py` — FastAPI read-only JSON
- `src/court_monitor/web/app.py` — операторский UI (маршруты и страницы)
- `src/court_monitor/web/templates/matches.html` — ручная очередь решений по совпадениям
- `src/court_monitor/web/templates/match_detail.html` — карточка кандидата и форма решения
- `src/court_monitor/web/static/app.css` — стили очереди решений и диагностического блока
- `src/court_monitor/web/jobs.py` — фоновые операции, один воркер, восстановление зависших
- `src/court_monitor/web/deps.py` — `require_operator`, CSRF, сессия (точка входа для будущей auth)
- `docs/manual-match-review.md` — простой операторский гайд по ручному разбору совпадений
- `config/sources.yaml` — адаптеры sudrf-источников
- `config/monitoring.yaml` — статьи УК и ключевые слова
- `config/source_registry.yaml` — реестр Telegram-каналов
- `config/ca/` — цепочка доверия fedsfm.ru + провенанс и ротация в README
- `README.md` — пошаговый сценарий работы оператора (установка → 5 шагов цикла)
- `docs/cli-reference.md` — справочник команд и флагов
- `docs/configuration.md` — переменные `CM_*`, веб, JSON API, Docker, TLS-пиннинг
