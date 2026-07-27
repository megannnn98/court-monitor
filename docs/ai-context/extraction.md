# Извлечение фактов (Extraction)

## Обзор

Модули извлечения работают на тексте документа после парсинга. Все возвращают `list[ExtractedFactDTO]` — Pydantic v2 DTO с entity, field, value, verification_status, confidence, quote, extraction_method.

## Модули

### articles.py — Статьи УК РФ

Извлекает структурированные ссылки на статьи УК:

| Паттерн | Confidence | Пример |
|---|---|---|
| point + part + article + УК | 0.98 | `п. «а» ч. 2 ст. 205 УК РФ` |
| part + article + УК | 0.95 | `ч. 2 ст. 205.1 УК РФ` |
| article + УК | 0.90 | `ст. 205.2 УК РФ` |
| prefix only (ст./статья + number) | 0.70 | `ст. 205` |
| bare number near УК | 0.60 | `205 УК РФ` |

Value: `{"article": "205", "part": "2", "point": "а", "code": "УК РФ"}`

### dates.py — Даты

Форматы: long form (`2 апреля 2026`), numeric (`02.04.2026`), ISO (`2026-04-02`).

Определяет тип даты из контекста: `verdict_date`, `ruling_date`, `hearing_date`, `effective_date`, `appeal_deadline`, `detention_date`, `arrest_date`.

Value: `{"date": "2026-04-02", "original": "2 апреля 2026", "type": "verdict_date"}`

### names.py — ФИО

Эвристический поиск 2-3 Capitalized Cyrillic токенов с фильтрацией стоп-слов (суд, прокурор, организации, география). Confidence зависит от количества токенов и наличия инициалов.

| Паттерн | Confidence | Метод |
|---|---|---|
| 3 токена (не все инициалы) | 0.95 | `regex:name:full_fio` |
| 3 токена (все инициалы) | 0.60 | `regex:name:initials_with_surname` |
| 2 токена + инициал | 0.50 | `regex:name:surname_initial` |
| 2 токена | 0.70 | `regex:name:two_tokens` |

### filtering.py — Фильтр релевантности

Документ релевантен, если совпадает **любая** статья ИЛИ **любое** ключевое слово из `config/monitoring.yaml`. Совпадение статей: точное или дочернее (`205.1` совпадёт с мониторингом `205`).

## Извлечение даты рождения (в matching/candidates.py)

Дата рождения извлекается **только из цитаты** (`fact.quote`) facts с `field="full_name_original"`. НЕ используется document-level дата (это дата заседания, не рождения).

- `"1983 года рождения"` → `BirthDateEvidence(year="1983", precision="year")`
- `"1 января 1983 года рождения"` → `BirthDateEvidence(year="1983", month="01", day="01", precision="full")`
