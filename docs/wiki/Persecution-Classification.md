# Persecution Classification

## Зачем это нужно

Classification отвечает на продуктовый вопрос: есть ли у Person признаки
политического преследования. Это не юридический приговор, а rule-based
операционный сигнал для списка candidates и monitoring findings.

## Быстрый сценарий

```bash
uv run python src/main.py classify-persecution --limit 1000
uv run python src/main.py list-candidates --snapshot-id 1 --min-confidence 0.7
```

`snapshot-id 1` заменить на реальный snapshot Росфинмониторинга. Без RF match
`list-candidates` не покажет финальный список.

## Что происходит внутри

`PersecutionClassificationService` собирает evidence только вокруг mentions/events
конкретной Person. Он не читает весь текст статьи как общий контекст для всех:
иначе один человек в статье мог бы унаследовать чужое обвинение.

Сигналы:

- политические статьи УК/КоАП;
- политические ключевые слова;
- события типа `case_opened`, `charge`, `arrest`, `sentence`, `detention`;
- source-specific evidence, например карточки реестра «Мемориала».

Результат:

```text
status = political | non_political | uncertain
confidence = 0.0..1.0
reasons = human-readable list
```

## Пример

```json
{
  "person_id": 42,
  "status": "political",
  "confidence": 0.95,
  "reasons": [
    "Политическая статья: ч. 2 ст. 205.2 УК РФ",
    "Событие: charge"
  ]
}
```

Смысл: Person получает latest political classification. Если RF status по
snapshot = `not_matched`, она может попасть в `/ui/candidates`.

## Кодовые точки входа

| Сценарий | Код |
|---|---|
| classification service | `src/persecution/classification_service.py` |
| rule-based classifier | `src/persecution/classifier.py` |
| queries/latest ids | `src/persecution/queries.py` |
| CLI | `src/persecution/cli.py` |
| candidates | `src/candidates/service.py` |
| UI candidates | `src/web/ui/candidates.py`, `src/web/candidate_rows.py` |

## Данные и артефакты

- `persecution_classifications` — все версии classification по Person.
- Latest result выбирается по `person_id`, classifier version и времени/id.
- Candidate query берет latest classification with `status = political` and
  `confidence >= min_confidence`.

## Команды и API

```bash
uv run python src/main.py classify-persecution --person-id 42
uv run python src/main.py classify-persecution --limit 1000
uv run python src/main.py list-candidates --snapshot-id 1 --min-confidence 0.7
```

UI:

```text
/ui/persons/{person_id}
/ui/candidates
```

API:

```bash
curl http://localhost:8001/persons/42/persecution
curl "http://localhost:8001/candidates?snapshot_id=1&min_confidence=0.7&limit=100"
```

## Проверка

```bash
uv run pytest tests/persecution tests/candidates
```

Для продуктовой проверки нужен полный путь: extraction -> ER -> classification
-> RF matching -> candidates.

## Ограничения и типичные ошибки

- Classification зависит от Person-event links. Pending ER может задержать
  появление Person в candidates.
- Political keywords alone can be weak evidence; смотреть `reasons`.
- Candidate page дополнительно фильтрует период новости и административные дела.
- Classification не проверяет Росфинмониторинг; это отдельная стадия.
