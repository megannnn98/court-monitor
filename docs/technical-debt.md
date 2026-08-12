# Technical debt

Реестр известного долга. Каждый пункт: описание, влияние, приоритет,
предполагаемое решение, причина «почему не сейчас». Спека §24.1.

## D-001. Модели Person / Case / CourtEvent / ReviewItem / AuditLog / RfmEntry

- **Описание.** Реализованы `SourceDocument`, `ExtractedFact`, `PersonRecord`
  (заменяет `RfmEntry`/`RfmEntryHistory` из исходного наброска — дедуп по
  `source+normalized_name+birth_date` уже покрывает first/last-seen нужды),
  `MatchCandidate`, а с Etap 4/срез 1 — `ReviewItem` и `AuditLog`
  (`migrations/versions/0006_review_items.py`, `0007_audit_log.py`).
  `ReviewItem` подключён к единственной существующей точке отказа —
  `parse_and_extract` при `parser_status=parser_failed`. `AuditLog`
  подключён к `update_match_status` (confirm/reject-match) и
  `resolve_review_item`. D-011 (`source_blocked` → ReviewItem) закрыт. Ещё не
  реализованы: `Person`, `PersonAlias`, `Case`, `PersonCase`, `CourtEvent` —
  для них в `domain/models.py` уже есть неиспользуемый enum `EventType`
  (заготовка с Etap 1).
- **Влияние.** Невозможно хранить канонических (подтверждённых) людей и дела
  — Etap 6/7 (полноценный review-workflow с созданием Person из подтверждённых
  MatchCandidate) остаётся заблокирован.
- **Приоритет.** Средний (снижен: очередь проверки и аудит уже есть).
- **Решение.** Вводить таблицы поэтапно, отдельными миграциями Alembic,
  строго до реализации соответствующей фичи. Не «всё сразу». Следующий кандидат
  — `Person`/`Case`/`CourtEvent`, но им нужен либо экстрактор событий дела
  (сейчас есть только article/date/name/relevance), либо явное решение о
  политике авто-создания Person из подтверждённого `MatchCandidate`.
- **Почему не сейчас (для Person/Case/CourtEvent).** Нет ни экстрактора,
  порождающего структурированные case/event-факты, ни согласованной политики
  слияния — вводить таблицы без потребителя нарушало бы это же правило D-001.

## D-002. Парсер sudrf — один набор селекторов

- **Описание.** `parsers/sudrf_press.py` использует один список селекторов с
  fallback'ом. Реальные суды на платформе sudrf.ru различаются вёрсткой.
- **Влияние.** На реальных (не fixture) страницах отдельных судов заголовок
  или дата могут не извлечься без правки селекторов.
- **Приоритет.** Средний.
- **Решение.** Вынести селекторы в per-court конфиг (`config/sources.yaml`),
  ввести версионирование парсера (`parser_version`) и при структурном
  изменении — генерировать `ReviewItem` (спека §15, §24.2).
- **Почему не сейчас.** Нет подтверждённых рабочих URL военных судов из
  текущего окружения (Discovery §2.2); разработка идёт fixtures-first.

## D-003. Live-доступ к сайтам военных судов не подтверждён

- **Описание.** `2zovs.sudrf.ru` / `uovs.sudrf.ru` отдают 404 из тестового
  окружения. Адаптер поддерживает `backend: http`, но он не валидирован на
  реальном суде.
- **Влияние.** MVP работает только на сохранённых fixtures.
- **Приоритет.** Высокий для production, низкий для каркаса.
- **Решение.** Получить от оператора рабочий URL/путь + образец HTML,
  переключить `backend` в `http`, прогнать на живой странице.
- **Почему не сейчас.** Зависит от внешнего доступа, не от кода.

## D-004. Airtable — mock-адаптер

- **Описание.** Реальный Airtable Web API не подключён (нет PAT). Слой
  `AirtableFieldMapping` и mock-адаптер спроектированы, но не реализованы.
- **Влияние.** Etap 5 (чтение Airtable) не выполнен.
- **Приоритет.** Средний.
- **Решение.** После получения PAT: `airtable inspect` →
  `validate-mapping` → `sync --dry-run` → `diff` → подтверждение (см.
  `docs/airtable-discovery.md`).
- **Почему не сейчас.** Спека §30.5 явно разрешает mock до получения секрета.

## D-005. LLM — частично закрыт (дизамбигуация)

- **Было.** `CM_LLM_MODE=disabled`, интерфейс не реализован.
- **Сделано.** `court_monitor/llm/`: Protocol `SupportsJsonCompletion`,
  OpenAI-совместимый клиент (`LlmClient`), Pydantic-валидация ответа,
  обязательная цитата. Применено к **дизамбигуации кандидатов** —
  единственному месту, где скоринг бессилен по существу: в новостных текстах
  нет дат и мест рождения, поэтому все кандидаты имеют одинаковый score.
  CLI: `judge-matches`. Миграция `0013`.
- **Провайдер-нейтрально.** Бэкенд задаётся `CM_LLM_BASE_URL` — DeepSeek,
  локальный vLLM/Ollama или другой OpenAI-совместимый хост. Смена провайдера —
  правка настроек, не кода.
- **Границы.** Суждение модели **не решение**: не входит в `score`, не меняет
  `status`. Подтверждает по-прежнему оператор — это остаётся неизменным
  свойством системы.
- **Остаётся не сделано.** LLM-извлечение фактов из свободного текста
  (наказание, тип события, регион) и закрытие остаточной проблемы склейки
  топонима с именем (см. `known-risks-and-notes.md`). Приоритет средний.

## D-006. Дедупликация документов — только по хешу

- **Описание.** Дедуп по `(url, content_hash)`. Не реализованы canonical URL,
  similarity-дедуп между сайтом и Telegram, diff при изменении страницы
  (спека §16).
- **Влияние.** Переиздание того же текста по другому URL создаст второй
  документ.
- **Приоритет.** Средний.
- **Решение.** Добавить canonical-url resolution и отдельный
  `text_similarity_hash` (MinHash/shingles) на Etap 2/9.
- **Почему не сейчас.** Текущего дедупа достаточно для MVP-критерия (§26.3).

## D-007. Аутентификация API отсутствует

- **Описание.** FastAPI-endpoints — открытые GET. Изменяющие эндпоинты
  (`/review/*`) ещё не реализованы; когда появятся — потребуют auth.
- **Влияние.** Не для MVP (только чтение), блокирует production.
- **Приоритет.** Высокий для Etap 7/10.
- **Решение.** OAuth2-password / session / API-key + ролевая модель (спека
  §17, §27).
- **Почему не сейчас.** Изменяющих эндпоинтов пока нет.

## D-008. Планировщик фоновых задач отсутствует

- **Описание.** Сбор и парсинг запускаются вручную через CLI. APScheduler /
  Dramatiq-воркер не подключён.
- **Влияние.** Нет автоматического регулярного опроса источников.
- **Приоритет.** Средний.
- **Решение.** Etap 10: один worker-процесс (APScheduler) внутри compose,
  регулярно вызывающий `process_source` по расписанию per-court.
- **Почему не сейчас.** MVP — полуавтоматический, ручной запуск допустим.

## D-009. observability — метрики без counters

- **Описание.** Логи structlog + correlation_id есть; Prometheus-метрик
  (размер очереди, число ошибок по источникам, длительность) нет.
- **Влияние.** Нет количественной наблюдаемости (спека §22).
- **Приоритет.** Средний.
- **Решение.** `prometheus-client` или `starlette-prometheus`, expose
  `/metrics` за auth.
- **Почему не сейчас.** MVP опирается на структурированные логи.

## D-010. README заявляет команды airtable-*

- **Описание.** CLI `airtable inspect/validate-mapping/sync/diff` описан в
  `docs/airtable-discovery.md`, но не реализован (mock-режим).
- **Влияние.** Расхождение доки и кода.
- **Приоритет.** Низкий (дока явно помечает как «после получения PAT»).
- **Решение.** Реализовать вместе с Etap 5.
- **Почему не сейчас.** Зависит от секрета.

## D-011. `source_blocked` не создаёт ReviewItem — ЗАКРЫТО

- **Было.** `SudrfAdapter._fetch_http` и `TelegramChannelAdapter._load_html`
  при `FetchHealth.blocked`/`http_error`/`timeout` только логировали факт и
  молча пропускали ответ (`continue` / `return None`).
- **Решение (реализовано).** Новый тип `sources.base.FetchProblem`
  (frozen dataclass: url, health, http_status, source_id, source_name).
  `SourceAdapter.fetch_new()` теперь возвращает `Iterator[FetchResult |
  FetchProblem]` — адаптеры остаются side-effect-free (просто `yield`,
  никакого доступа к БД), а `process_source`/`process_registry_source`
  различают типы через `isinstance` и на `FetchProblem` вызывают
  `repo.upsert_review_item(item_type="source_blocked", ...)`. `304 Not
  Modified` — не проблема, пропускается как раньше. Fixture-режим
  (`live=False`, локально отсутствующая fixture) НЕ создаёт ReviewItem —
  это dev/test-особенность, а не прод-сбой.
  `repo.upsert_review_item` расширен: без `document_id`, но с `source_id`,
  дедуп идёт по `(source_id, item_type)` — повторные блокировки одного
  источника при регулярных `fetch-source` не плодят дубликаты (тот же приём,
  что и для `parser_failed` в срезе 1).
  Новое поле `SourceStats.blocked`, видно в выводе `fetch-source`/`fetch-all`.
- **Тесты.** `tests/unit/test_sudrf_http_adapter.py`,
  `tests/unit/test_telegram_adapter_http.py` (мок `HttpClient` на уровне
  адаптера — blocked/http_error/timeout/ok-но-пусто/not_modified),
  `tests/integration/test_source_blocked_review_item.py` (wiring в
  `process_source`/`process_registry_source`, идемпотентность), плюс
  расширенные тесты `upsert_review_item` на dedup по `source_id`.

## Court case monitoring — deferred correctness issues

> Эти пункты сознательно отложены. Они не являются blockers для текущего merge
> court-case-monitoring. Возвращаться к ним следует отдельным этапом после merge,
> если не появится regression, делающий один из пунктов production blocker.

### CM-KI-001 — Search not attempted can be confused with no-match

- **Описание.** Edge case: `article=None`, `person_name!=None`. `_run_press_pipeline()`
  считает что поисковые критерии потенциально есть, но `progressive_search()` может
  не создать ни одной стратегии, потому что person search используется только вместе
  с article.
- **Влияние.** 0 реальных search requests → empty result → потенциально `processed_no_match`.
- **Приоритет.** Medium.
- **Решение.** Добавить person-only search, либо explicit `insufficient_search_criteria`,
  либо `SearchOutcome.attempted`.
- **Инвариант.** `processed_no_match` допустим только если минимум один remote search
  действительно успешно выполнен.

### CM-KI-002 — Search-result truncation / incomplete result set

- **Описание.** Remote search DTO допускает limit до 20, а orchestrator исторически
  имел ограничение количества обрабатываемых case cards.
- **Влияние.** `processed_no_match` нельзя ставить если часть найденной выдачи не
  была проверена.
- **Приоритет.** Medium.
- **Решение.** Проверить pagination `sud_delo`: есть ли pager, `has_more`, total count,
  возвращается ли вся выдача одной страницей.
- **Инвариант.** `processed_no_match` = search exhaustive AND all returned candidates
  evaluated AND no card failures AND no match candidates.

### CM-KI-003 — Unknown HTTP 200 search HTML may be interpreted as empty result

- **Описание.** `parse_case_list()` сейчас в основном извлекает ссылки на карточки.
  Если сайт вернёт HTTP 200, FetchHealth.ok, но изменившийся/неожиданный HTML и case
  links не будут найдены, это может выглядеть как пустая выдача.
- **Влияние.** Structural HTML changes могут маскироваться под "ничего не найдено".
- **Приоритет.** Medium.
- **Решение.** Различать: (1) recognized empty result page, (2) recognized non-empty
  result page, (3) unrecognized/structurally changed HTML. Третий вариант должен быть
  parser/structural failure, а не no-match.
- **API.** `ParsedCaseList` с полями `recognized`, `empty_confirmed`, `has_more`, `results`,
  или typed parser exception.

### CM-KI-004 — Case-card parser lacks strong structural validation

- **Описание.** `parse_case_card()` может вернуть почти пустой `ParsedCaseCard`, если
  HTML структуры ГАС изменятся.
- **Влияние.** Невалидные карточки могут быть помечены как "успешно обработанные".
- **Приоритет.** Medium.
- **Решение.** Добавить recognition/invariants перед тем как считать карточку успешно
  evaluated. Не требовать наличие ФИО или events (могут быть скрыты), но проверять
  хотя бы признаки настоящей `sud_delo` case card.
- **Тесты.** valid real card → recognized; generic HTML → structural failure;
  search-result HTML passed as card → structural failure.

### CM-KI-005 — Court processing statistics semantics

- **Описание.** CLI counters должны однозначно различать: attempted, review_created,
  processed_no_match, temporary_failure, failed.
- **Влияние.** Особенно сценарии где `_apply_outcome_to_state()` меняет state без
  выбрасывания exception.
- **Приоритет.** Low.
- **Решение.** CLI/statistics должны отражать фактический final processing state, а не
  только факт возврата функции.

### CM-KI-006 — Case-card version update regression test

- **Описание.** Добавить E2E тест: card version 1 (result отсутствует) → card version 2
  (result = "Вынесен приговор").
- **Ожидание.** Case остаётся один, CourtEvent identity остаётся одна, mutable result
  обновляется, decision_at обновляется, новая SourceDocument version сохраняется,
  дублей нет.
- **Приоритет.** Medium.
- **Решение.** Логика уже частично реализована, но regression test отсутствует.

### CM-KI-007 — Participant structural change review

- **Описание.** Сценарий: version 1 ("Информация скрыта") → version 2 ("Иванов Иван Иванович").
  Нельзя автоматически утверждать что это один и тот же participant.
- **Влияние.** Автоматическое слияние может привести к некорректным данным.
- **Приоритет.** Medium.
- **Решение.** Создавать human-review item с diff участников, а не автоматически сливать.

### CM-KI-008 — Press crawler fixture parity / incremental traversal

- **Описание.** Отложенные проблемы press crawler:
  - fixture mode не полностью повторяет live archive traversal;
  - incremental traversal / watermark требует отдельной проверки порядка archive;
  - нельзя делать unsafe `break` на первом known ID без доказанного ordering.
- **Влияние.** Не относится напрямую к готовности текущего court-case vertical slice.
- **Приоритет.** Low/medium.

## Checklist — deferred court monitoring issues

- [ ] CM-KI-001 — Search not attempted vs no-match
- [ ] CM-KI-002 — Search-result truncation
- [ ] CM-KI-003 — Unknown HTML as empty result
- [ ] CM-KI-004 — Case-card structural validation
- [ ] CM-KI-005 — Processing statistics semantics
- [ ] CM-KI-006 — Case-card version update test
- [ ] CM-KI-007 — Participant structural change review
- [ ] CM-KI-008 — Press crawler fixture parity
