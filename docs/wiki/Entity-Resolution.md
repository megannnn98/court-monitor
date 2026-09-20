# Entity Resolution v2

Решение: [ADR 0012](../adr/0012-entity-resolution-v2.md). Модель Person и exact baseline: [Persons](Persons.md), [ADR 0005](../adr/0005-entity-resolution-strategy.md).

Главное правило: **похожий человек ≠ тот же человек**, и **одинаковое ФИО ≠ тот же человек**: `matching_key` — ключ поиска кандидатов, а не ключ identity (amendment ADR 0012, 2026-09-16). ER v2 может автоматически привязать новое упоминание к существующей Person или создать Person, но **никогда не объединяет две существующие canonical Person** — merge только явным действием ревьюера.

```plantuml
@startuml
start
:PersonIdentityInput (full_name, matching_key, surface, mention, article);
:PersonNameNormalizer → NameVariant[];
:pg_advisory_xact_lock по identity-блокам;
:CompositeCandidateGenerator\nexact matching_key (0/1/все тёзки) + alias key + pg_trgm [+ semantic];
:FeatureExtractor (по компонентам ФИО, конфликты, exact_matching_key);
:RuleBasedScorer → resolution_score;
:DecisionPolicy (пороги, top1−top2, конфликты, инициалы, тёзки);
switch (действие)
case (AUTO_LINK)
  :mention.person_id; alias только для чистой полной формы;
case (CREATE_NEW)
  :новая Person (racing-safe) + link;
case (REVIEW)
  :person_id = NULL; review_records (person_resolution);
endswitch
:person_resolution_decisions (provenance, er-v2);
stop
@enduml
```

## Модули (`src/persons/resolution/`)

| Модуль | Роль |
|---|---|
| `models.py` | `PersonIdentityInput`, `NormalizedPersonName`/`NameVariant`, `PersonResolutionCandidate`, `PersonResolutionFeatures`, `PersonResolutionScore`, `PersonResolutionDecision`, enum'ы действий и причин |
| `normalizer.py` | `PersonNameNormalizer`: регистр, `ё→е`, точки/пунктуация, инициалы, все допустимые порядки ФИО; суффиксы — только подсказки |
| `candidates.py` | `ExactKeyCandidateGenerator` (все active Person с ключом имени `exact_key` или alias `alias`, по id), `TrigramCandidateGenerator` (pg_trgm), `SemanticCandidateGenerator`, `CompositeCandidateGenerator` |
| `features.py` | `PersonResolutionFeatureExtractor`: выравнивание вариантов, `exact/typo/initial_compatible/missing/mismatch`, конфликты (RapidFuzz Levenshtein) |
| `scoring.py` | `PersonResolutionScorer`: rule-based `resolution_score` (не вероятность), конфликт ограничивает score 0.25, точная полная форма ≥ 0.85 |
| `decision.py` | `PersonResolutionDecisionPolicy`, `ResolutionThresholds` |
| `aliases.py` | `AliasPromotionPolicy`: какая форма становится alias |
| `service.py` | `PersonResolutionEngine` (только чтение, dry-run), `PersonResolutionService` (advisory lock, применение, provenance) |
| `review.py` | `PersonResolutionReviewService`: сравнение для ревьюера и действия |
| `evaluation.py`, `cli.py`, `factory.py` | evaluation, CLI, сборка зависимостей |

Интеграция: `ExtractionResolutionService.resolve_extraction_run()` — extraction → normalization → lock → кандидаты → decision. Exact fast path и `RuleBasedPersonResolver` удалены. `ResearchService` ER не делает: research читает уже canonical entities.

## Решения

| Ситуация | Действие |
|---|---|
| полное ФИО или известный alias, единственный сильный кандидат, margin > минимума (в том числе единственная Person с тем же `matching_key`) | AUTO_LINK |
| несколько active Person с тем же `matching_key` (тёзки или дубли) | REVIEW (`multiple_exact_name_matches`), при любых score; ни id, ни semantic не выбирают |
| переставленное ФИО из 3 частей (0.90) | AUTO_LINK |
| переставленное ФИО из 2 частей, опечатка, нет отчества, инициалы, одна фамилия | REVIEW |
| несколько правдоподобных кандидатов / два сильных (возможные дубли Person, или `known_distinct_persons` после keep_separate) / малый margin | REVIEW |
| конфликт (другое отчество или имя, несовместимый инициал) | не plausible → CREATE_NEW |
| нет кандидатов или все слабые | CREATE_NEW |
| semantic включён, но недоступен, и есть кандидат с той же фамилией без конфликта | REVIEW вместо CREATE_NEW |

Причины (`reasons`): `strong_unique_match`, `no_candidate`, `no_plausible_candidate`, `multiple_plausible_candidates`, `possible_duplicate_persons`, `multiple_exact_name_matches`, `known_distinct_persons`, `incomplete_name`, `initials_only`, `conflicting_identity_data`, `low_decision_margin`, `medium_confidence_match`, `semantic_source_unavailable`; `exact_matching_key` — только в старых решениях удалённого fast path.

Точная полная форма (то же имя или alias, без инициалов, не одно слово) получает score ≥ 0.85 при любом прочтении ролей: суффикс может прочитать «Дмитрий Шостакович» как имя + отчество, а у имени из 4+ слов прочтений нет.

## Примеры (`resolve-person`, корпус evaluation)

```text
$ uv run python src/main.py resolve-person "Иванов Иван Иванович"
  candidate #1: Иван Иванович Иванов   resolution_score 0.90   sources trigram
    surname: exact   given name: exact   patronymic: exact
    order: different   alias: no   semantic: n/a
    conflicts: none
  candidate #2: Иван Петрович Иванов   resolution_score 0.25
    patronymic: mismatch   conflicts: patronymic_mismatch
Decision: auto_link → person #1 (strong_unique_match, margin 0.90)

$ uv run python src/main.py resolve-person "Алексей Сергеевич Иванов"   # две active Person-тёзки
  candidate #10: Алексей Сергеевич Иванов   resolution_score 1.00   sources exact_key, trigram
  candidate #42: Алексей Сергеевич Иванов   resolution_score 1.00   sources exact_key, trigram
Decision: review (low_decision_margin, multiple_plausible_candidates, multiple_exact_name_matches, possible_duplicate_persons, margin 0.00)

$ uv run python src/main.py resolve-person "И. Иванов"
  4 кандидата (Иван Иванович, Иван Петрович, Илья, Игорь Иванов), у всех 0.58,
  given name: initial_compatible
Decision: review (medium_confidence_match, multiple_plausible_candidates, initials_only, margin 0.00)

$ uv run python src/main.py resolve-person "Александр Пертров"
  candidate #5: Александр Петров   resolution_score 0.65
    surname: typo   given name: exact   conflicts: none
Decision: review (medium_confidence_match, margin 0.65)

$ uv run python src/main.py resolve-person "Василий Голубцов"
  candidate #29: Виктор Голубев   resolution_score 0.05
    surname: mismatch   given name: mismatch
Decision: create_new (no_plausible_candidate, conflicting_identity_data)
```

`resolve-person` — всегда dry-run: ничего не пишет в БД (`--json` — план целиком).

## Provenance

`person_resolution_decisions` (уникально по `mention_id` + `resolver_version`): `method` (`er_v2`; `exact_matching_key` — старые решения fast path), `action`, `status` (`applied`/`pending_review`/`reviewed`), `selected_person_id`, `resolution_score`, `decision_margin`, `reasons`, `identity` (+ нормализованное имя), `candidates` (снимок кандидатов, признаков и score), `semantic_source`, `resolver_version = er-v2`, `review_action`, `distinct_from_person_id` (keep_separate), `reviewer_note`, `created_at`, `reviewed_at`.

«Почему mention X связан с Person Y» — строка решения X. Повторная обработка использует записанное решение (идемпотентно); упоминания, связанные до ER v2, не перерешиваются. Re-resolution новой версией — отдельная явная операция (не реализована).

## Параллельный прогон

`resolve-people --workers N` раздаёт статьи N процессам (по умолчанию 1). Дубли персон при этом не возникают: `lock_identity_blocks` берёт advisory-блокировки по токенам имени, поэтому упоминания одного человека сериализуются между воркерами, а второй воркер видит персону, созданную первым. Блокировки берутся отсортированно внутри транзакции, взаимных блокировок нет.

Режим предназначен для массового пересбора, а не для регулярной работы: статьи обрабатываются в другом порядке, поэтому решения по отдельным упоминаниям отличаются от однопроцессного прогона. Измерено на фикстуре из шести статей: 9 упоминаний из 24 привязаны к другой персоне, чем последовательно; набор персон при этом тот же. Однопроцессный прогон воспроизводим. Мониторинг и evaluation идут последовательно.

Замер на 600 статьях рабочего корпуса (11 345 статей, 19 383 упоминания персон): 1 процесс — 36 с, 4 — 16 с, 8 — 13 с; персон во всех прогонах одинаково. Порядок обработки при этом меняется, поэтому отдельные решения могут разойтись на доли процента (ссылка против review), а число созданных персон — нет.

## Human review

REVIEW оставляет `entity_mentions.person_id = NULL` (Person-заглушки нет) и создаёт pending `review_records` (`subject_type = person_resolution`, `subject_id` = id решения).

```bash
uv run python src/main.py person-resolution-reviews list
uv run python src/main.py person-resolution-reviews show 42
uv run python src/main.py person-resolution-reviews apply 42 --action link_to_person --person-id 7 --note "тот же суд"
uv run python src/main.py person-resolution-reviews apply 42 --action merge_persons --person-id 7 --source-person-id 9
uv run python src/main.py person-resolution-reviews apply 43 --action keep_separate --person-id 7 --source-person-id 9 --note "другой суд"
```

API: `GET /person-resolution/reviews`, `GET /person-resolution/reviews/{decision_id}`, `POST /person-resolution/reviews/{decision_id}/decision` (`{"action", "person_id", "source_person_id", "note"}`; 404 — нет решения, 409 — решение не pending / person не active / не хватает второй Person).

| Действие | Эффект |
|---|---|
| `link_to_person` | связать mention, person-event links, alias по `AliasPromotionPolicy` |
| `create_new_person` | создать Person, в том числе тёзку с тем же `matching_key` (лог `er_namesake_created_by_review`) |
| `merge_persons` | `merge_persons_in_session(source → target)` + `PersonMergeRecord`, связать mention с target |
| `keep_separate` | связать с `person_id` и записать `distinct_from_person_id = source_person_id` (обязателен): дальше эта пара — `known_distinct_persons`, а не `possible_duplicate_persons`; новые упоминания всё равно REVIEW |

Ревьюер видит структурированное сравнение (детерминированная диагностика, не LLM-рассуждение): компоненты ФИО, порядок, alias, trigram/semantic similarity, конфликты, правила score, источник статьи.

## AI review (ADR 0020)

Перед человеком pending-решение проходит автоматическое AI-review. LLM отвечает на один вопрос по каждому кандидату — «это тот же человек?» — и возвращает структурированный результат; решение принимает детерминированная `EntityReviewPolicy`, а применяет обычный `PersonResolutionReviewService`. Модель ничего не пишет в БД и никогда не сливает персон.

```text
ER v2 → REVIEW (pending_review)
      → AI review каждого кандидата: same_person / different_person / uncertain
      → EntityReviewPolicy
           ├── auto_accepted  → link_to_person
           ├── auto_rejected  → create_new_person
           ├── human_required → остаётся в очереди человеку
           └── failed         → остаётся в очереди человеку
```

### Как проходит один разбор

Разбор — это батч: `review_pending(limit)` берёт pending-решения **от старых к новым**
(`created_at`, затем `id`) и обрабатывает по одному. Каждое решение независимо: своя
транзакция, свой аудит, а любое исключение на нём логируется
(`event=entity_review_decision_failed`), считается как `failed` и не останавливает батч.

```plantuml
@startuml
autonumber
participant "review_pending\n(AutomatedEntityReviewService)" as Service
database "PostgreSQL" as DB
participant "EntityMatchReviewer\n(together | cli)" as Reviewer
participant "EntityReviewPolicy" as Policy
participant "PersonResolutionReviewService" as Apply

Service -> DB: pending-решения, старые первыми (limit)
loop по каждому решению
  Service -> DB: решение + контекст (короткая транзакция, без блокировок)
  alt уже есть review с тем же (input_hash, model, prompt_version)
    Service -> Service: skipped, следующее решение
  else нужно спрашивать модель
    loop по каждому кандидату (≤ 3 активных, по убыванию score)
      loop attempt = 1 .. ENTITY_REVIEW_MAX_RETRIES + 1
        Service -> Reviewer: review(mention, candidate)
        alt валидный ответ по контракту
          Reviewer --> Service: decision + confidence + evidence
        else временная ошибка (timeout, 429, недоступен, ненулевой код CLI)
          Reviewer --> Service: EntityReviewError(transient=True)
          Service -> Service: sleep 0.5 с × 2^(attempt-1), следующая попытка
        else окончательная ошибка (не JSON, контракт, auth, нет команды)
          Reviewer --> Service: EntityReviewError(transient=False)
          Service -> Service: попытки прекращены
        end
      end
    end
    alt все кандидаты ответили
      Service -> Policy: resolve(контекст решения, ответы)
      Policy --> Service: outcome + действие
      Service -> DB: FOR UPDATE решения, перепроверка pending и отсутствия review
      alt решение всё ещё pending и не разобрано
        Service -> DB: строка аудита person_resolution_ai_reviews
        opt outcome = auto_accepted | auto_rejected
          Service -> Apply: apply(link_to_person | create_new_person, note)
        end
        Service -> DB: commit
      else человек или другой воркер успел раньше
        Service -> Service: skipped, ничего не пишется
      end
    else ни одного ответа
      Service -> DB: аудит с outcome=failed, решение остаётся человеку
    end
  end
end
@enduml
```

Пошагово, с точками принятия решений:

1. **Отбор.** Только `status = pending_review`; уже применённые и разобранные человеком
   решения в выборку не попадают. В monitoring-конвейере лимит батча —
   `MONITORING_DISCOVERY_LIMIT`, в CLI — `--limit`.
2. **Сборка входа** (`ai_context.py`). Из снимка решения берутся кандидаты, сортируются
   по `resolution_score` (при равенстве — по `person_id`), обрезаются до трёх и
   фильтруются по `status = active`: деактивированная или слитая персона на review не
   выносится. К каждой стороне добавляются короткие цитаты вокруг её упоминаний (до 4 по
   600 символов), типы событий и детерминированное сравнение ER v2 (`matched_features`,
   `conflicting_features`, причины review). Отдельно считается `name_is_complete` — имя
   считается полным, если в нём есть хотя бы два токена длиннее одной буквы, то есть не
   одна фамилия и не только инициалы.
3. **Идемпотентность.** Вход хешируется (`input_hash` — SHA-256 по контексту без
   `decision_id`); если такая тройка `(input_hash, model, prompt_version)` уже разобрана
   не-`failed` результатом, решение пропускается (`skipped`) и модель не вызывается.
4. **Опрос модели.** По одному вызову на пару (mention, кандидат) — отдельный вызов
   означает, что «два кандидата прочитаны как один и тот же человек» видно policy, и что
   в аудите остаётся ответ по каждому кандидату. Вызовы идут **вне транзакции**: пул
   соединений не занят на время сети, строка решения не заблокирована.
5. **Ошибки и retry.** Временная ошибка повторяется до `ENTITY_REVIEW_MAX_RETRIES` раз
   (всего попыток на кандидата — `max_retries + 1`) с backoff 0.5 с × 2^n; окончательная
   не повторяется никогда. Временные: timeout, 429, недоступность провайдера, ненулевой
   код возврата CLI. Окончательные: ответ не по контракту (не JSON, лишнее поле,
   `confidence` вне 0..1, неизвестное `decision`), ошибка аутентификации, отклонённый
   запрос, отсутствующая команда CLI. Сколько вызовов реально стоило review, видно в
   `provider_calls`: второй кандидат и каждая повторная попытка добавляют по одному.
   Если хотя бы один кандидат не дал ответа, всё решение уходит в `failed` — половинчатых
   разборов не бывает.
6. **Решение policy.** Детерминированная функция от ответов и от причин review (таблица
   ниже). Confidence модели — лишь один вход: ни одно её значение не перебивает конфликт
   признаков ER, тёзок и возможные дубли персон.
7. **Применение.** Строка решения берётся `FOR UPDATE`, перепроверяются `pending_review`
   и отсутствие записанного review; затем в одной транзакции пишется аудит и
   применяется действие — тем же `PersonResolutionReviewService`, что и ручное review, с
   пометкой `ai review (<модель>, <версия prompt>): <причина>` в `reviewer_note`. Если
   действие больше не подходит к данным (персона деактивирована или слита),
   транзакция откатывается, отдельной транзакцией пишется `failed`, и решение остаётся
   человеку.

`merge_persons` недоступен автоматике ни при каком ответе: слияние двух канонических
персон делает только человек.

Правила policy (`src/persons/resolution/ai_policy.py`):

| Ответ AI | Условия | Итог |
|---|---|---|
| `same_person`, confidence ≥ порога | нет конфликтов признаков у кандидата, нет `multiple_exact_name_matches` / `possible_duplicate_persons` / `known_distinct_persons`, ровно один такой кандидат | `auto_accepted` → `link_to_person` |
| `different_person`, confidence ≥ порога для **всех** кандидатов | имя полное (не одна фамилия, не только инициалы) | `auto_rejected` → `create_new_person` |
| `uncertain`, confidence ниже порога, несколько `same_person`, конфликт признаков, blocking reason, неполное имя | — | `human_required` |
| невалидный ответ, timeout, исчерпанные retry, ошибка API | — | `failed` (никакого auto-merge) |

Модель видит только две сравниваемые сущности: имя, alias, matched/conflicting признаки ER, типы событий и короткие цитаты вокруг упоминаний (`ai_context.py`, 600 символов на цитату, до 4 цитат на сторону, до 3 кандидатов). Ни корпуса, ни полных статей, ни поисковой выдачи. В системной инструкции текст публикаций объявлен недоверенными данными; ответ валидируется по Pydantic-схеме, свободный текст — ошибка review, а не решение.

Аудит — `person_resolution_ai_reviews`: решение AI, confidence, explanation, supporting/conflicting evidence, все ответы по кандидатам (`candidate_reviews`), provider, модель, версия prompt, хеш входа, `provider_calls` (сколько вызовов провайдера стоило review: retry и второй кандидат добавляют по одному), длительность, итоговый статус, причина передачи человеку, применённое действие и person.

Идемпотентность — partial unique index по `(decision_id, input_hash, model, prompt_version)` с условием `outcome <> 'failed'`: повторный прогон с тем же входом, моделью и версией prompt ничего не делает (`skipped`), а вот `failed` (timeout, 429, невалидный ответ) ответа не дал и на следующем прогоне будет перепрошен. Смена `ENTITY_REVIEW_PROMPT_VERSION` даёт новую строку и сохраняет прежнюю как историю.

Транзакции: решение читается в короткой транзакции, модель вызывается **без** открытой транзакции и без блокировок, затем во второй транзакции строка решения берётся `FOR UPDATE`, перепроверяется `status = pending_review` и отсутствие уже записанного review, и только после этого пишется аудит и применяется действие. Поэтому человек, разобравший то же решение пока модель отвечала, не перезаписывается (прогон отдаёт `skipped`), а соединение пула не занято на время сетевых вызовов.

Запуск:

```bash
uv run python src/main.py person-resolution-reviews ai --limit 100
```

```text
reviewed: 42
auto_accepted: 18
auto_rejected: 17
human_required: 5
failed: 2
skipped: 0
```

В monitoring-конвейере это отдельная стадия `ai_entity_review` (Dagster asset между `person_resolution` и `persecution_classification`). Без настроенного провайдера стадия пишет `status=not_configured`, и все pending-решения идут человеку, как раньше.

| Переменная | По умолчанию |
|---|---|
| `ENTITY_REVIEW_PROVIDER` | `none` (значения: `none`, `together`, `cli`) |
| `ENTITY_REVIEW_MODEL` | не задана — берётся `TOGETHER_MODEL` |
| `ENTITY_REVIEW_AUTO_THRESHOLD` | `0.90` (допустимо 0.5..1.0) |
| `ENTITY_REVIEW_TIMEOUT_SECONDS` | `30` |
| `ENTITY_REVIEW_MAX_RETRIES` | `3` (только временные ошибки API, backoff 0.5 с × 2^n) |
| `ENTITY_REVIEW_PROMPT_VERSION` | `v1` |
| `ENTITY_REVIEW_CLI_COMMAND` | `claude -p --model sonnet` (только при `provider=cli`) |

Два backend'а одного контракта: `together` — OpenAI-совместимый HTTP-endpoint со строгим `json_schema` (сам Together, локальный Ollama через `TOGETHER_BASE_URL`, любой совместимый сервер); `cli` — уже авторизованный агентский CLI (`claude -p`, `qwen -p`), который читает промпт на stdin и печатает JSON. У CLI нет schema-режима, поэтому контракт задан в промпте и проверяется той же Pydantic-моделью; ненулевой код возврата и таймаут считаются временными ошибками (retry), мусор вместо JSON — окончательной. Цена измерена на прогоне 50 решений (`claude -p --model sonnet`): 62 вызова, 18 минут, 21 секунда на вызов в среднем (7-76 с) — поэтому CLI годится для выборочных прогонов, а не для полного разбора очереди.

Упавшее CLI-review диагностируется по хвосту его вывода: адаптер кладёт последние 500 символов stderr в лог (`event=entity_review_cli_failed … stderr=…`) и в `resolution_reason` строки аудита. Без этого лимит, потерянная сессия и незалогиненный CLI выглядят одинаково — «exited with 1» и больше ничего.

Порог стоит там, где модель отвечает чаще всего: на синтетическом прогоне 50 решений confidence кратна 0.05 и её мода — 0.85 (12 ответов) и ровно 0.90 (8). Это значит, что 0.91 почти отключит автоматику, а 0.85 резко её расширит; двигать порог без замера на живом корпусе нельзя. Порог меняется переменной окружения; чтобы перепроверить кандидатов новой версией prompt, поднимите `ENTITY_REVIEW_PROMPT_VERSION` и запустите команду снова. Ручное review никуда не исчезает: `list`/`show`/`apply` работают как раньше, а `merge_persons` остаётся только человеку.

## Concurrency

Unique-индекса по `matching_key` больше нет (тёзки), поэтому от параллельных дублей защищает только `pg_advisory_xact_lock` по identity-блокам (полные токены имени, одинаковы для переставленных форм). `ExtractionResolutionService` берёт блоки всех упоминаний run в начале (sorted), `resolve_mention` — свои перед чтением кандидатов (повторный захват — no-op). Кандидаты и решение считаются под lock: «Иван Иванов» и «Иванов Иван» (или одно имя дважды) в двух воркерах выполняются последовательно, второй видит закоммиченную Person первого и связывается с ней; CREATE_NEW пишет только после этого перечтения. `create_new_person` ревьюера берёт те же lock.

`merge_persons_in_session` блокирует source и target (`FOR UPDATE`, по возрастанию id) и перепроверяет `active`: два ревьюера, сливающие одну Person в разные target, дают ровно один `PersonMergeRecord`, второй получает 409. Self-merge запрещён.

Миграция `o9p0q1r2s3t4`: `uq_persons_matching_key_active` → `ix_persons_matching_key_active` (обычный partial `WHERE status = 'active'`), колонка `person_resolution_decisions.distinct_from_person_id`. Строки Person и связи не меняются; downgrade останавливается, пока есть active тёзки с общим ключом.

## Evaluation

```bash
EVALUATION_DATABASE_URL=postgresql+psycopg://...court_monitor_eval uv run python src/main.py evaluate-er [--sweep] [--semantic]
```

Корпус `tests/fixtures/er_v2_corpus.json`: 36 persons, 59 кейсов — 38 positive, 12 hard negatives, 8 ambiguous (в том числе две active тёзки: точное и переставленное ФИО, одна создана ревьюером), 1 indistinguishable (единственная Person с этим ФИО, но другой человек — считается отдельно как `indistinguishable_namesake_links`, не как false link); имена проходят extraction-нормализатор с обеих сторон. Результаты (2026-09-16, defaults):

| generator | recall@1 | recall@5 | recall@10 |
|---|---|---|---|
| exact (ключ имени + alias) | 0.42 | 0.42 | 0.42 |
| trigram | 0.82 | 1.00 | 1.00 |
| semantic (E5, без порога) | 0.92 | 1.00 | 1.00 |
| combined | 0.92 / 0.97 с semantic | 1.00 | 1.00 |

| метрика | значение |
|---|---|
| AUTO_LINK precision / recall | 1.00 / 0.58 |
| false links (в том числе на тёзках) | 0 |
| indistinguishable namesake links | 1 |
| false create-new (пропущенные связи) | 0 |
| review rate | 0.43 |
| лишние review / пропущенные review | 1 (`Роман Карпенков` ≈ `Карпенко`) / 0 |

Sweep: 0.85 — минимальный `ER_AUTO_LINK_MIN_SCORE` без ложных связей (при 0.80 «Илья Петрович Иванов» → «Илья Иванов»); 0.40 — максимальный `ER_REVIEW_MIN_SCORE` без пропущенных связей (при 0.50 «А. В. Новикова» становится новой Person). `ER_MIN_MARGIN` этим корпусом не различается. Semantic кандидаты не изменили ни одного решения и recall@5 — выключены по умолчанию.

## Конфигурация

| Переменная | По умолчанию |
|---|---|
| `ER_CANDIDATE_LIMIT` | `30` (2..200: меньше двух скрыло бы тёзку от policy) |
| `ER_AUTO_LINK_MIN_SCORE` | `0.85` |
| `ER_REVIEW_MIN_SCORE` | `0.40` |
| `ER_MIN_MARGIN` | `0.10` |
| `ER_SEMANTIC_CANDIDATES` | выключен; при `1` нужен `QDRANT_URL` (иначе warning и работа без semantic) |
| `ER_SEMANTIC_CANDIDATE_MIN_SCORE` | не задан (без порога, ограничено `ER_CANDIDATE_LIMIT`); только для генерации кандидатов |

## Semantic index после link/create

Link/create меняют semantic document Person (aliases, события). Индекс обновляется вручную: `rebuild-semantic-index --entity person --incremental` переиндексирует изменённые по `content_hash` документы. Автоматическое обновление — этап 6.

## Ограничения

- Корпус маленький и синтетический, пороги подобраны на нём.
- Extraction-нормализатор срезает окончания (`Анна Новикова` → `Анн Новиков`): пол теряется, имена искажаются; формы с инициалами extraction не нормализует, поэтому их score ниже.
- Нет транслитерации, уменьшительных имён без alias (`Маша`/`Мария`), фонетики, дат рождения и контекстных признаков.
- Единственная Person с тем же ФИО связывается автоматически: другой человек-тёзка неотличим без контекста (суд, регион, дата рождения не извлекаются). Вторая тёзка появляется только через `create_new_person` ревьюера; дальше каждое упоминание этого ФИО — REVIEW.
- Без unique-ключа дубли предотвращаются только для упоминаний с общим identity-блоком (полный токен); имена без общего полного токена (опечатка в каждом слове) могут параллельно создать две Person.
- Advisory lock включает токены имён и держится весь extraction run: частые имена сериализуют параллельные run.
- Опечатка никогда не даёт AUTO_LINK при порогах по умолчанию (максимум 0.80).
