# Типы параметров операций: план для решения

Статус: выбран урезанный вариант, он реализован без внутреннего union. `_command_for`
стал таблицей builders по операциям с одним helper для `--selected-source`; argv
закреплён тестом `tests/app/test_operation_command_line.py`. Запуск шага из legacy-форм
идёт через один `_start_form_step`, а `start_step` собирает `OperationParameters` один
раз. Union ниже отложен: он нужен, только если параметры операций продолжат расти.

Исходное предложение. База анализа — `origin/dev` (`818116c6`).
Перечитаны `orchestrator/operations.py`, `web/ui/management.py` и вызывающие.
Цель — выразить допустимые команды типами и сократить сборку параметров,
сохранив команды, порядок проверок, HTTP-ответы и чтение истории запусков.

## Предлагаемые границы и типы

Сохранить публичный `OperationParameters` как плоский совместимый DTO для
`OperationRegistry`, сохранённого JSON и выдачи истории. Внутри после текущего
слияния defaults переводить его в типизированную команду. Не заменять DTO сразу
на Pydantic discriminated union: это изменит сериализацию, валидацию и старые записи.

Целевой внутренний union по операции и режиму:

- `MonitorLoadParams`: режим `load` либо отдельный вариант для `mode=None`
  (совмещённая загрузка и разрешение); выбор источников, даты, optional limit.
  Эти режимы дают разные CLI-аргументы, их нельзя объединять с потерей признака.
- `MonitorResolveParams`: режим `resolve`, непустая последовательность выбранных
  новостных источников; нет одиночного `source` и параметров загрузки.
- `WholeDatabaseParams`: режим `purge | entities | rosfin | figurants | political`;
  источников и дат загрузки нет. Закреплённые модели передаются в CLI только для
  `ENTITY_MODEL_MODES`, как сейчас; цепочка переносит их и через остальные шаги.
- Отдельные параметры `IngestParams`, `ExtractParams`, `ResolvePeopleParams`,
  `ClassifyParams` для остальных четырёх операций: требуемый источник/лимит,
  optional источник и эффективное число workers по действующим правилам.
- Выбор источников загрузки: `AllSources | SingleSource | SelectedNewsSources`.
  `SingleSource` проверяется через текущий реестр; не ужесточать его до NEWS:
  сейчас это ограничение действует именно для `sources`. Выбранные источники
  дедуплицируются с сохранением порядка; resolve требует непустой список.
- `StepMode` для запуска из UI — существующие шесть режимов, включая отдельный
  `rosfin`. `CycleMode` — только пять элементов `MONITOR_CYCLE`.
  Общие опции (`chain`, `after`, закреплённые модели) собрать в `StartOptions`.
  Запрос шага — `LoadStepRequest(load: LoadParams, options: StartOptions)` либо
  `WholeDatabaseStepRequest(mode: WholeDatabaseMode, options: StartOptions)`.
  `start_step(registry, request)` собирает `OperationParameters` один раз.

Внутренние значения предпочтительно immutable dataclass; доверять типам только
после адаптера на границе. Диапазоны Field и ограничения идентификаторов моделей
по-прежнему принадлежат DTO. Невозможные сочетания отвергать в адаптере с прежним
`ValueError`, текстом и моментом отказа, до сохранения запуска/старта subprocess.
Поля, которые текущий builder молча игнорирует, пока игнорировать: запрещать их
теперь означало бы менять поведение.

`_command_for` становится диспетчером `{operation: builder}`, а builder monitor
разбирает свой union; неизвестное имя сохраняет `OperationNotFoundError`.
Один helper выдаёт пары `--selected-source` после общей проверки/дедупликации.
`next_in_chain` использует типизированную таблицу переходов; внешний адаптер
по-прежнему возвращает None для неизвестного режима и `rosfin`/`resolve`/None.

В management общий helper выполняет `_shown → start_step → StepRefused/_refused
→ RedirectResponse`. Разбор body/дат остаётся в `_start`; whole-database adapter
не получает LoadParams. Сначала сохранить нынешнюю очередность: stale chain,
проверка периода, out_of_turn, затем распознавание режима. Перенос проверки mode
вперёд способен заменить прежний 409 на 400 — отдельно согласуемое изменение.

## Вызывающие и читатели, которые нужно проверить

Пути ниже относительны `apps/court-monitor/src/`, кроме явно указанных:

| Место | Зависимость |
| --- | --- |
| `packages/court-orchestrator/src/orchestrator/operations.py` | Definitions/defaults, prepare/start, _with_defaults, stop_chain_after, _continue_chain, _to_run, сериализация истории |
| `web/ui/management.py` | start_step, оба HTTP-адаптера, отображение параметров и дат |
| `web/routers/cycle.py` | POST /api/v1/cycle/start вызывает start_step; чтение последней загрузки |
| `web/ui/entities.py` | Прямой registry.start для entities с показанными моделями |
| `web/ui/airtable.py`, `web/routers/reference_lists.py` | Прямой запуск rosfin |
| `dagster_location/cycle.py` | Старт purge с chain=True, синхронное исполнение цепочки |
| `web/routers/operations.py`, `web/ui/run_cards.py`, `web/ui/pipeline.py`, `web/ui/logs.py`, `web/ui/spend.py` | Читатели параметров/режима; сохраняют плоский DTO |
| `tests/app/test_operation_runs.py` | Команды, defaults, ошибки, история, остановка и продолжение цепочки |
| `tests/app/test_management_page.py`, `test_cycle_page.py`, `test_run_tail.py`, `test_logs_page.py` | Запуски через формы, порядок отказов, отображение |
| `tests/app/test_v1_api_contract.py`, `test_entity_model_api.py` | Контракт API и закрепление показанных моделей |
| `tests/monitoring/test_monitoring_cycle.py`, `tests/support/pipeline_runs.py` | Dagster-цикл, прямые конструкторы параметров и фикстуры |
| `frontend/src/pages/WorkPage.tsx`, `WorkPage.test.tsx` | Потребители CycleStartRequest, показываемые модели и период |

Перед реализацией повторить `rg 'OperationParameters|start_step\(|next_in_chain'`
по всему репозиторию; читателей `.parameters.mode` искать отдельно. CLI получает
готовые argv: его синтаксис не меняется, прямых конструкторов DTO в CLI не найдено.

## API, OpenAPI и история

Рекомендуемый вариант полностью внутренний: `CycleStartRequest`, JSON ответов,
303 legacy-форм, 400/409 с прежними текстами и порядок полей/флагов CLI сохраняются.
`OperationRunResponse.parameters` сейчас `dict[str, object]`; внутренний union не
должен появляться в OpenAPI. История хранит плоский JSON, включая None/defaults:
чтение старых/неизвестных операций должно работать без новых обязательных полей.
Не валидировать исторические записи как новые команды при простом чтении.
Миграция БД и изменения frontend не нужны. Проверить точное равенство app.openapi()
до/после, filtered `frontend/openapi/openapi.json` и регенерацию клиента на копии.

Публичный discriminated union или вложенный LoadParams в HTTP/сохранённом JSON —
отдельный вариант с изменением контракта: потребуется решение о совместимости,
адаптер старой истории, обновление схемы и клиента штатным генератором. В этот
план реализации без отдельного согласования такой вариант не включать.

## Порядок отдельных коммитов после решения

1. Таргетно зафиксировать argv, defaults и порядок ошибок для допустимых режимов,
   конфликтов source/sources, пустого resolve, whole-database sources, неизвестного
   режима/операции; чтение старого JSON. Каждый новый/изменённый тест проверить
   соответствующей мутацией на отдельной копии.
2. Ввести внутренние типы и адаптер после `_with_defaults`; оставить внешний DTO
   и хранение прежними. Затем отдельным коммитом вынести builders и таблицу
   диспетчеризации, общий helper выбранных источников.
3. Типизировать переходы цепочки без изменения условий: partial load продолжается
   только при прежней проверке БД, interruption останавливает; перенос моделей прежний.
4. Ввести запрос шага/LoadParams в management и адаптировать cycle router;
   объединить HTTP-обвязку форм, сохранить порядок всех отказов и редиректы.
5. Проверить всех прямых вызывающих и читателей, точное равенство OpenAPI/клиента;
   финальные ruff check, ruff format --check, mypy --strict затронутых файлов.
   Схему/сгенерированные файлы не коммитить, если они идентичны.

Проверки только таргетные из таблицы; полный pytest не запускать. Интеграционные
проверки — исключительно `ebnv-pgvector-test:5434`, без платных CLI.

## Риски и решения перед началом

Основные риски: изменение merge defaults/explicit None, 400/409 и порядка отказов;
потеря совместимости старых записей; забытый standalone resolve/rosfin; перестановка
argv или источников; изменение фиксации моделей/частичного продолжения цепочки.
Типизированная таблица heterogeneous builders не должна прятать ошибки за Any/cast:
предпочтителен общий адаптер команды и явные сигнатуры builders.

Предлагаемое решение: внутренний union + совместимый плоский DTO, без изменений
HTTP/истории. До одобрения варианта код задачи C не изменять.
