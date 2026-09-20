# Local Web UI / Operator console

## Зачем это нужно

Локальная веб-морда живёт в том же FastAPI-приложении, что и JSON API. Это
Operator console: рабочий интерфейс для очередей ревью, evidence-проверки,
поиска и routine pipeline operations. Это не полный web-аналог CLI.
Аутентификации нет: это соответствует ADR 0014, где API публикуется только на
`127.0.0.1`.

## Быстрый сценарий

```bash
set -a; source .env; set +a
docker compose up -d postgres
docker compose run --rm migrate
docker compose --profile api up -d
```

Открыть: <http://127.0.0.1:8001/ui>.

Типовой путь оператора:

1. Открыть `/ui/monitoring`, убедиться, что свежий run не упал.
2. Открыть `/ui/person-resolution/reviews`, разобрать pending ER.
3. Открыть `/ui/candidates`, проверить кандидатов и evidence.
4. Открыть `/ui/channel`, если нужен черновик публикации для @enbv2022.

## Что есть

- `/ui` — вход в Operator console.
- `/ui/person-resolution/reviews` — очередь ER-ревью.
- `/ui/person-resolution/reviews/{decision_id}` — сравнение входящего упоминания
  с кандидатами и явные действия ревьюера.
- `/ui/persons/{person_id}` — карточка Person: алиасы, классификация,
  статус Росфинмониторинга и события.
- `/ui/articles/{article_id}` — полный текст ParsedArticle; ссылки из карточки
  человека подсвечивают evidence span.
- `/ui/search` — lexical search по статьям через существующий
  `PostgresLexicalSearch`.
- `/ui/candidates` — политически классифицированные люди со статусом РФМ
  `not_matched`; можно выбрать snapshot, minimum confidence и открыть карточку
  Person с evidence.
  Кнопки `Скачать CSV` и `Скачать PDF` выгружают то, что показывает страница:
  те же snapshot, minimum confidence, период новостей (`date_from`, по умолчанию
  последние 45 дней), флаг административных дел и тот же `limit`; PDF — таблица
  landscape A4 с кириллицей. `Export to Excel` намеренно игнорирует `limit`:
  файл содержит всех найденных кандидатов.
- `/ui/channel` — очередь для канала @enbv2022 (ADR 0017): политически
  преследуемые люди, которых канал ещё не публиковал, с черновиком поста; любой
  статус РФМ (в черновике — предупреждение). Уже опубликованных находит по
  публичной ленте канала (раз в час). Ниже — новости без имени и возможное имя
  из другого источника за ±3 дня (подсказка, около 3 из 4 верны).
- `/ui/operations` — preview/confirm запуск routine operations.
- `/ui/operations/runs/{run_id}` — состояние operation run и вывод команды.
  Runs хранятся в PostgreSQL (`operator_operation_runs`, см. [Data-Model](Data-Model.md)):
  переживают перезапуск API, видны всем процессам, одна операция не запускается
  дважды одновременно, пропавший процесс даёт статус `interrupted`.
- `/ui/monitoring` — последние monitoring runs и active findings.
- `/ui/wiki` — встроенный справочник проекта; страницы читаются из `docs/wiki`.
  Кнопка «Скачать вики в PDF» (на оглавлении и на странице Home,
  `/ui/wiki/export.pdf`) собирает все страницы в один PDF через WeasyPrint:
  оглавление с номерами страниц, Home первой, каждая страница с нового листа,
  диаграммы векторные и вписаны в лист. Сборка занимает ~15 с (PlantUML
  рисует диаграммы заново).
  Fenced-блоки `plantuml` рендерятся в SVG прямо на странице; остальные code
  blocks показываются как исходный текст.

Имена людей на страницах кандидатов, очереди для канала, в карточке человека и в
выгрузках пишутся фамилией вперёд («Иванов Иван», «Ярош Сергей Васильевич»),
как в таблице заказчицы (`_surname_first` в `web/candidate_rows.py`).

## Пример

ER-review:

```text
incoming: "А. П. Иванов"
candidate #42: Алексей Петров Иванов
candidate #87: Андрей Павлов Иванов
action: link_to_person #42
```

После применения решения запись исчезает из `/ui/person-resolution/reviews`,
потому что `person_resolution_decisions.status` становится `reviewed`. Это не
значит, что Person исчезает из `/ui/candidates`: candidates зависят от
classification, РФМ, статуса Person и фильтров страницы.

Для диаграмм в образ ставятся `plantuml` и `graphviz`: без `dot` рисуются только
диаграммы последовательностей. Локально (`uv run`) нужны те же пакеты:
`sudo pacman -S plantuml graphviz`.

Каждая страница содержит короткую контекстную подсказку: зачем она нужна и какое
следующее действие ожидается от оператора.

### Что такое ER-ревью

ER (Entity Resolution) связывает упоминание человека из статьи с конкретной
`Person` в базе. Одно лицо может встречаться под полным именем, инициалами,
псевдонимом или с опечаткой. Когда система не уверена, она создаёт pending
решение и показывает несколько кандидатов.

Пример: в статье найдено `А. П. Иванов`. Система предлагает Person 42 «Алексей
Петров Иванов» и Person 87 «Андрей Павлов Иванов». Оператор открывает source и
evidence span, сравнивает город, дату рождения, алиасы и контекст, затем выбирает:

- **Связать** — упоминание относится к выбранной Person.
- **Отдельная персона** — похожий кандидат найден, но это другой человек.
- **Создать новую** — подходящего кандидата нет.

ER-ревью не доказывает факт преследования и не меняет текст статьи. Оно меняет
идентификационную связь упоминания с Person, от которой зависят события,
классификация и последующий RF-matching.

## Кодовые точки входа

| Сценарий | Код |
|---|---|
| сборка FastAPI app | `src/web/app.py`, `src/api.py` |
| общий layout UI | `src/web/ui/layout.py` |
| ER review UI | `src/web/ui/reviews.py`, `src/web/routers/reviews.py` |
| Person card | `src/web/ui/persons.py`, `src/web/routers/persons.py` |
| candidates page/export | `src/web/ui/candidates.py`, `src/web/candidate_rows.py`, `src/web/exports.py` |
| channel queue | `src/web/ui/channel.py`, `src/channel_feed/` |
| operation runs | `src/web/ui/operations.py`, `src/operator_console.py` |
| wiki renderer | `src/web/wiki.py`, `docs/wiki/` |

## Данные и артефакты

- UI читает и пишет PostgreSQL через те же сервисы, что CLI.
- Routine operations хранятся в `operator_operation_runs`.
- ER decisions хранятся в `person_resolution_decisions`.
- Candidate exports строятся из строк страницы; CSV/PDF применяют `limit`, XLSX
  намеренно выгружает все найденные строки.

## Границы

- Read-only pages открываются сразу.
- Mutating или долгие операции запускаются только через preview + explicit
  confirm, см. ADR 0015.
- Operation run создаётся отдельно от HTTP request; страница run detail показывает
  status/output, см. ADR 0016.
- Все факты карточки человека должны вести к span в `ParsedArticle.text`.
- Developer/evaluation/golden-corpus команды остаются CLI-only, пока не станут
  routine operator workflow.
- JSON endpoints остаются основным contract для автоматизации:
  `/persons/{id}/detail`, `/persons/{id}/events`, `/articles/{id}`,
  `/search/articles`, `/operations/runs`.
