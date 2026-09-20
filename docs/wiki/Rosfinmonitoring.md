# Rosfinmonitoring

## Зачем это нужно

Rosfinmonitoring stage отвечает на вопрос: есть ли активная Person в выбранном
snapshot списка террористов и экстремистов. Для продукта важен статус
`not_matched`: политически преследуемый человек с подтверждённым отсутствием в
snapshot попадает в candidates.

## Быстрый сценарий

```bash
# посмотреть snapshots через API
curl http://127.0.0.1:8001/rosfinmonitoring/snapshots

# сматчить persons к конкретному snapshot
uv run python src/main.py match-rosfinmonitoring --snapshot-id 1 --limit 20000

# получить candidates после classification + RF match
uv run python src/main.py list-candidates --snapshot-id 1 --min-confidence 0.7
```

`snapshot-id 1` заменить на реальный id. Если snapshot отсутствует, RF stage и
candidate list пропускаются.

## Что происходит внутри

```plantuml
@startuml
title Rosfinmonitoring matching

actor Operator
database "PostgreSQL" as DB
component "RuleBasedRosfinmonitoringMatcher" as Matcher
component "CandidateQueryService" as Candidates

Operator -> Matcher : match-rosfinmonitoring --snapshot-id N
Matcher -> DB : select active Persons
Matcher -> DB : select entries of snapshot N
Matcher -> DB : write rosfin_matches
Operator -> Candidates : list-candidates --snapshot-id N
Candidates -> DB : latest political classification + RF status
Candidates --> Operator : political + not_matched Persons
@enduml
```

Match statuses:

- `matched` — уверенное совпадение с entry;
- `not_matched` — кандидатов в snapshot не найдено; это подтверждённое
  отсутствие для product query;
- `ambiguous` / `needs_review` — есть похожие entries, автоматического вывода нет;
- `insufficient_data` — имя Person слишком бедное для надежного поиска;
- no match record — matching для этой Person/snapshot не запускался.

## Пример

```json
{
  "person_id": 42,
  "snapshot_id": 7,
  "status": "not_matched",
  "confidence": 0.8,
  "matched_entry_id": null,
  "reasons": ["no plausible entries found"]
}
```

Смысл: Person была проверена против snapshot 7, совпадений не найдено. Если у
нее latest persecution classification = `political`, она может попасть в
`/ui/candidates`.

## Кодовые точки входа

| Сценарий | Код |
|---|---|
| parsing/import | `src/rosfinmonitoring/` |
| matching | `src/rosfinmonitoring/matcher.py` |
| snapshot lookup | `src/rosfinmonitoring/snapshot_lookup.py` |
| CLI | `src/rosfinmonitoring/cli.py` |
| candidate query | `src/candidates/service.py` |
| API | `src/web/routers/rosfinmonitoring.py` |

## Данные и артефакты

- `rosfinmonitoring_snapshots` — immutable snapshot metadata and raw content hash.
- `rosfinmonitoring_entries` — entries inside snapshot.
- `rosfin_matches` — result per Person + snapshot.
- `candidates` are not stored as a table; `CandidateQueryService` reads latest
  classification and `rosfin_matches`.

## Команды и API

```bash
uv run python src/main.py match-rosfinmonitoring --snapshot-id 1 --person-id 42
uv run python src/main.py match-rosfinmonitoring --snapshot-id 1 --limit 1000
uv run python src/main.py list-candidates --snapshot-id 1 --min-confidence 0.7
```

```bash
curl http://127.0.0.1:8001/rosfinmonitoring/snapshots
curl http://127.0.0.1:8001/rosfinmonitoring/snapshots/1
curl "http://127.0.0.1:8001/rosfinmonitoring/snapshots/1/entries?limit=100"
curl "http://127.0.0.1:8001/candidates?snapshot_id=1&min_confidence=0.7"
```

## Проверка

```bash
uv run pytest tests/rosfinmonitoring tests/candidates
```

Проверка на живой базе:

```sql
select status, count(*)
from rosfin_matches
where snapshot_id = 1
group by status
order by status;
```

## Ограничения и типичные ошибки

- `not_matched` означает “matching был выполнен и не нашёл кандидатов”, а не
  “в таблице нет записи”.
- `NO_MATCH_RECORD`/missing row не включается в candidates по умолчанию.
- `ambiguous`, `needs_review`, `insufficient_data` не считаются подтверждённым
  отсутствием.
- Match надо повторять для нового snapshot.
- Candidate page показывает выбранный snapshot; другой snapshot может дать
  другой результат.
