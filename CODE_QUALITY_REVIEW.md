# Thermo-Nuclear Code Quality Review

Дата ревью: 2026-09-27
Дата исправлений: 2026-09-28
Диапазон: `8c361e1..28f4698`
Ветка: `main`
Вердикт: **RESOLVED**

## Статус исправлений

Все четыре findings закрыты:

1. Phantom semantic contract удалён из нового ER runtime. Исторический
   `semantic_source` читается только на legacy boundary; новые решения записывают
   обязательное для старой schema значение `disabled`.
2. Evaluation corpus перенесён в `src/evaluation/person_resolution/` и переименован в
   `ErEvaluationCorpus`.
3. README, актуальный ADR, Data Model и комментарии синхронизированы с текущими
   workflows; тестовый DB helper получил нейтральное имя `DatabaseSeeder`.
4. `unsupported_report_claim` удалён из allowlist, а regression test подтверждает, что
   schema отвергает это устаревшее значение.

Повторное статическое ревью исправленного diff новых блокирующих findings не выявило.

Проверки после исправлений:

- targeted pytest: `203 passed, 149 skipped`;
- full pytest: `954 passed, 509 skipped`;
- Ruff check: passed;
- Ruff format check: 375 files already formatted;
- `git diff --check`: passed;
- `mypy --strict src tests`: 19 ошибок в 9 тестовых файлах; это существующий baseline,
  не затрагивающий изменённый production contract.

## Границы проверки

Выполнено только статическое ревью: Git diff, CodeGraph, трассировка callers и поиск
остаточных ссылок на удалённые подсистемы. Сборка, тесты и runtime-проверки не
запускались. Код во время ревью не изменялся.

Дифф содержит 342 изменённых файла, 735 добавленных и 178641 удалённую строку. Ни один
изменённый Python-файл не пересёк порог 1000 строк. `src/monitoring/service.py`
уменьшился с 925 до 836 строк.

## Findings

### High — удалённый semantic retrieval всё ещё пронизывает ER

**Где:**

- `src/persons/resolution/factory.py:22`
- `src/persons/resolution/models.py:103`
- `src/persons/resolution/candidates.py:42`
- `src/persons/resolution/candidates.py:240`
- `src/persons/resolution/decision.py:86`
- `src/persons/resolution/cli.py:118`

**Симптом:** production composition root создаёт только `ExactKeyCandidateGenerator` и
`TrigramCandidateGenerator`, но runtime продолжает содержать:

- `CandidateSource.SEMANTIC`;
- `semantic_similarity` в candidate и feature models;
- `SemanticSourceStatus`;
- обработку недоступного Qdrant;
- semantic ordering и merge;
- недостижимую policy-ветку semantic outage;
- постоянно бесполезный CLI-вывод `semantic: n/a`.

**Root cause:** удалён semantic generator, но не удалён его сквозной контракт. В результате
несуществующая возможность остаётся частью models, orchestration, policy, persistence и UI.

**Когда проявляется:** при любом изменении ER maintainer вынужден учитывать статусы и ветки,
которые новый runtime не способен создать. Это увеличивает число состояний системы,
затрудняет reasoning и создаёт риск случайно восстановить часть удалённого поведения без
полного контракта.

**Как проверить гипотезу:** `build_generators()` возвращает только exact-key и trigram
generators. Следовательно, `SemanticSourceStatus.OK`, `SemanticSourceStatus.UNAVAILABLE` и
semantic scores недостижимы для новых production decisions.

**Минимальный patch:**

1. Удалить semantic source/error/status/similarity из новых runtime models, generator,
   policy, scoring и CLI.
2. Оставить в `CandidateGenerationResult` только `candidates` и `counts`.
3. Сохранить чтение исторического `semantic_source` только на persistence/read boundary.
4. До отдельной schema migration записывать в legacy DB-колонку константу `disabled`, если
   колонка пока обязательна.

Это блокирующая структурная находка: подсистема удалена физически, но её ментальная модель
остаётся распределённой по основному ER flow.

### Medium — evaluation fixture помещён в production namespace

**Где:**

- `src/persons/resolution/corpus.py:1`
- `src/persons/resolution/corpus.py:69`
- `src/persons/resolution/evaluation.py:34`

**Симптом:** новый `src/persons/resolution/corpus.py` содержит 219 строк моделей и seeding-кода
для disposable evaluation DB. Его единственный consumer — ER evaluation. Модель при этом
сохранила устаревшее имя `EntityRetrievalCorpus`, хотя entity retrieval удалён.

**Root cause:** код перенесён из удаляемого semantic package в ближайший живой package без
пересмотра ownership и терминологии.

**Когда проявляется:** production ER package выглядит владельцем corpus seeding и тестовой DB
схемы. Maintainer не может понять по package boundary, относится модуль к runtime или только
к evaluation tooling.

**Как проверить гипотезу:** CodeGraph показывает, что `CorpusPerson`,
`EntityRetrievalCorpus` и `seed_corpus` вызываются только из
`src/persons/resolution/evaluation.py`.

**Минимальный patch:** перенести модуль в `src/evaluation/person_resolution/corpus.py` либо
другой явно evaluation-only package; переименовать модель в `ErEvaluationCorpus`.

### Medium — canonical описание продукта противоречит реализации

**Где:**

- `README.md:12`
- `src/monitoring/dagster/jobs.py:10`
- `src/web/candidate_rows.py:42`
- `src/web/candidate_rows.py:214`
- `src/candidates/models.py:28`
- `docs/wiki/Data-Model.md:205`

**Симптом:** README утверждает, что Together AI используется только для преобразования
natural-language вопроса в structured query. Research workflow удалён; Together теперь
обслуживает AI-review решений ER. В коде и документации также остались упоминания Qdrant
outage, удалённого Telegram bot, research layer и удалённого `PgVectorStore` как действующих
потребителей или механизмов.

**Root cause:** feature deletion проверяла imports и routes, но не завершила очистку
архитектурного vocabulary и canonical документации.

**Когда проявляется:** новый разработчик проектирует изменение или настраивает deployment по
ложному описанию текущих границ системы.

**Как проверить гипотезу:** research routes, CLI и packages, Telegram bot и semantic vector
runtime удалены в рассматриваемом диапазоне; текущий Together client используется ER review.

**Минимальный patch:**

1. Обновить README: Together AI используется для optional ER review.
2. Удалить stale comments про Qdrant, Telegram bot и research consumer.
3. В Data Model оставить краткое описание legacy schema; подробности удалённого runtime
   перенести в соответствующий ADR либо явно оформить как исторические.

### Low — obsolete safety kind остаётся частью актуальной evaluation schema

**Где:**

- `src/evaluation/final/corpus.py:15`
- `tests/evaluation/test_final_evaluation.py:28`

**Симптом:** текущая corpus schema продолжает разрешать `unsupported_report_claim`, хотя
research reports и соответствующий safety gate удалены.

**Root cause:** удаление report evaluation не зачистило enum-like allowlist и тест,
закрепляющий старый контракт.

**Когда проявляется:** новый corpus может принять known-limitation kind, который ни один
текущий evaluation stage не способен произвести или проверить.

**Минимальный patch:** удалить `unsupported_report_claim` из `DANGEROUS_KINDS` и обновить
fixture/test.

## Что сделано хорошо

- Из codebase удалено значительно больше сложности, чем добавлено.
- Research, semantic search, MCP, Telegram bot, channel feed и GLiNER удалены крупными
  цельными слоями, а не скрыты за feature flags.
- Ни один изменённый файл не пересёк границу 1000 строк.
- `MonitoringStage.SEMANTIC_INDEXING` явно помечен как compatibility-only для старых run
  items. Это оправданная persistence compatibility, в отличие от phantom semantic flow в ER.
- Worktree после ревью оставался чистым; `main` совпадал с `origin/main` на `28f4698`.

## Рекомендуемый порядок исправлений

1. Удалить phantom semantic contract из нового ER runtime, сохранив legacy DB compatibility
   на persistence boundary.
2. Перенести и переименовать ER evaluation corpus.
3. Синхронизировать README, comments и Data Model с фактическими двумя workflows.
4. Удалить obsolete evaluation safety kind.
