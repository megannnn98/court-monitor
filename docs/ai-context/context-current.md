# Контекст — 2026-07-30

## Что сделано

Etap 0–3 закрыты: discovery источников, каркас пакета, вертикальный срез (конфиг → домен → хранилище → источники → парсеры → экстракция → сервисы → CLI → API), структурированная экстракция (статьи УК, даты, ФИО) и фильтр релевантности по `monitoring.yaml`.

Etap 4, срез 1: `ReviewItem` (очередь ручной проверки) и `AuditLog` (append-only аудит решений), миграции `0006`/`0007`. `Person`/`Case`/`CourtEvent` намеренно не введены — см. `technical-debt.md` D-001.

D-011 закрыт: адаптеры возвращают `Iterator[FetchResult | FetchProblem]` вместо молчаливого пропуска при блокировке/ошибке/таймауте; на `FetchProblem` создаётся `ReviewItem(item_type="source_blocked")`.

Etap 5: операторский веб-интерфейс (`web/`) — обзор, документы, очередь совпадений с confirm/reject, очередь проверки, реестр с поиском, журнал аудита и фоновые операции. Подробности в `architecture.md`.

Состояние на сегодня — 354 теста, ruff + mypy чистые.

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
| Замена перечня РФМ + ключ дедупа | `HEAD` | `--replace` для `fetch-source fedsfm` (источник публикует список целиком, дозапись задваивала людей); `birth_place` в ключе при отсутствии даты (миграция `0010`); веб-задача сносила решения оператора без всякой защиты — переведена на `purge_person_records` |

Ключевой факт для планирования: **`fedsfm.ru` доступен из Казахстана без VPN**, мешал только сертификат УЦ Минцифры.

## Следующий шаг

**Судебные источники — главный пробел.** Проект называется court-monitor, но суды не мониторит: 375 документов из Telegram против 4 из fixture. Разведка 2026-07-30 показала, что это не устаревшие URL, а исчезнувший интеграционный слой — детали и таблица доступности в `known-risks-and-notes.md`. Развилка:

1. `bsr.sudrf.ru` (Банк судебных решений) недоступен по TCP, при этом соседние адреса той же подсети открываются за 0.09 с. Похоже на блокировку хоста — стоит проверить через VPN. Одна проверка отвечает, имеет ли смысл вся ветка.
2. ~~`vsrf.ru` как альтернатива~~ — **проверено через playwright, не подходит**: ВС РФ публикует обезличенные постановления Пленума и обзоры практики, а найденные ФИО оказались списком судей-докладчиков в фильтре поиска. Приговоры с датами рождения выносит первая инстанция, и это те же недоступные источники. Детали в `known-risks-and-notes.md`.

Пока этот пробел не закрыт, скоринг по дате и месту рождения остаётся мёртвым, а все кандидаты — с `0.50`.


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
- `src/court_monitor/web/jobs.py` — фоновые операции, один воркер, восстановление зависших
- `src/court_monitor/web/deps.py` — `require_operator`, CSRF, сессия (точка входа для будущей auth)
- `config/sources.yaml` — адаптеры sudrf-источников
- `config/monitoring.yaml` — статьи УК и ключевые слова
- `config/source_registry.yaml` — реестр Telegram-каналов
- `config/ca/` — цепочка доверия fedsfm.ru + провенанс и ротация в README
- `README.md` — пошаговый сценарий работы оператора (установка → 5 шагов цикла)
- `docs/cli-reference.md` — справочник команд и флагов
- `docs/configuration.md` — переменные `CM_*`, веб, JSON API, Docker, TLS-пиннинг
