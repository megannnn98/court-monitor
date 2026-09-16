# Local Web UI

Локальная веб-морда живёт в том же FastAPI-приложении, что и JSON API.
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

- `/ui/person-resolution/reviews` — очередь ER-ревью.
- `/ui/person-resolution/reviews/{decision_id}` — сравнение входящего упоминания
  с кандидатами и явные действия ревьюера.
- `/ui/persons/{person_id}` — карточка Person: алиасы, классификация,
  статус Росфинмониторинга и события.
- `/ui/articles/{article_id}` — полный текст ParsedArticle; ссылки из карточки
  человека подсвечивают evidence span.
- `/ui/search` — lexical search по статьям через существующий
  `PostgresLexicalSearch`.

## Границы

- UI не запускает pipeline stages и не меняет extraction/classification/RF matching.
- Все факты карточки человека должны вести к span в `ParsedArticle.text`.
- JSON endpoints остаются основным contract для автоматизации:
  `/persons/{id}/detail`, `/persons/{id}/events`, `/articles/{id}`,
  `/search/articles`.
