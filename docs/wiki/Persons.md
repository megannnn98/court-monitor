# Persons

## Зачем это нужно

Person — каноническая карточка человека. Extraction находит mentions в статьях,
а person resolution решает, к какой Person относится mention или нужна новая
Person. Все дальнейшие решения — events, persecution classification,
Rosfinmonitoring matching, candidates — работают уже вокруг Person.

## Быстрый сценарий

```bash
uv run python src/main.py extract-entities --limit 20000
uv run python src/main.py resolve-people --limit 20000
uv run python src/main.py person-resolution-reviews list
```

Если список review пуст, все обработанные mentions либо связаны, либо получили
auto/create decisions. Если review есть, открыть `/ui/person-resolution/reviews`
или использовать CLI apply.

## Что происходит внутри

ER v2 строит кандидатов по exact `matching_key`, alias, pg_trgm и optional
semantic search; затем считает features/score и принимает одно из решений:

- `AUTO_LINK` — связать mention с existing Person;
- `CREATE_NEW` — создать новую Person;
- `REVIEW` — оставить mention без `person_id` и создать pending review.

`matching_key` — только ключ поиска кандидатов, не identity proof. Активные
тёзки могут иметь одинаковый key. ER v2 никогда не merge existing persons
автоматически.

Подробности: [Entity Resolution](Entity-Resolution.md), ADR 0012.

## Пример

```text
mention: "Иванова И. И."
candidates:
  #42 Иван Иванов, score=0.61
  #87 Илья Иванов, score=0.58
decision: REVIEW, reason=multiple_plausible_candidates
```

Оператор выбирает link/create/merge/keep separate. После apply mention получает
`person_id`, events связываются с Person, review decision становится `reviewed`.

## Кодовые точки входа

| Сценарий | Код |
|---|---|
| persistence Persons/Aliases | `src/persons/persistence.py` |
| ER orchestration | `src/persons/resolution/service.py` |
| candidates | `src/persons/resolution/candidates.py` |
| scoring/decision | `src/persons/resolution/features.py`, `src/persons/resolution/decision.py` |
| manual review | `src/persons/resolution/review.py`, `src/web/ui/reviews.py` |
| CLI | `src/persons/resolution/cli.py` |
| API/UI cards | `src/web/routers/persons.py`, `src/web/ui/persons.py` |

## Данные и артефакты

- `persons` — canonical person, status, `matching_key`.
- `person_aliases` — surface/normalized aliases and origin.
- `person_event_links` — event/person role links.
- `person_merge_records` — audited merges.
- `person_resolution_decisions` — ER decisions and review status.

## Команды и API

```bash
uv run python src/main.py resolve-people --limit 100
uv run python src/main.py resolve-person "Иван Иванов"
uv run python src/main.py person-resolution-reviews list
uv run python src/main.py person-resolution-reviews show 42
uv run python src/main.py evaluate-er
```

UI:

```text
/ui/persons/{person_id}
/ui/person-resolution/reviews
/ui/person-resolution/reviews/{decision_id}
```

JSON API:

```bash
curl http://localhost:8001/persons/42/detail
curl http://localhost:8001/persons/42/events
```

## Проверка

```bash
uv run pytest tests/persons
uv run python src/main.py evaluate-er
```

## Ограничения и типичные ошибки

- Transliteration, birth dates and rich biographical context are not extracted.
- Diminutives work only when alias evidence exists.
- Same `matching_key` is not enough for auto-link when there are namesakes.
- Pending ER review is expected behavior, not pipeline failure.
- Manual `keep_separate` teaches ER that two active Persons are distinct.
