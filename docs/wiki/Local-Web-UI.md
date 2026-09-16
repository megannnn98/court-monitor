# Local Web UI / Operator console

Локальная веб-морда живёт в том же FastAPI-приложении, что и JSON API. Это
Operator console: рабочий интерфейс для очередей ревью, evidence-проверки,
поиска и routine pipeline operations. Это не полный web-аналог CLI.
Аутентификации нет: это соответствует ADR 0014, где API публикуется только на
`127.0.0.1`.

## Запуск

```bash
set -a; source .env; set +a
docker compose up -d postgres
docker compose run --rm migrate
docker compose --profile api up -d
```

Открыть: <http://127.0.0.1:8001/ui>.

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
  Кнопки `Скачать CSV` и `Скачать PDF` выгружают текущую выборку с теми же
  фильтрами; PDF — таблица landscape A4 с кириллицей.
- `/ui/operations` — preview/confirm запуск routine operations.
- `/ui/operations/runs/{run_id}` — состояние operation run и вывод команды.
- `/ui/monitoring` — последние monitoring runs и active findings.
- `/ui/wiki` — встроенный справочник проекта; страницы читаются из `docs/wiki`.

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
