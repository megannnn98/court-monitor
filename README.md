# court-monitor — восстановленная версия

Это восстановленный первый вертикальный срез проекта после случайного удаления файлов.

Полный pipeline для идентификации политически преследуемых лиц, отсутствующих в перечне Росфинмониторинга.

## Возможности

### 1. Загрузка статей (Source Layer)

Универсальный source layer: discover → fetch → parse → PostgreSQL. Два источника: ОВД-Инфо (`ovd-info`) и SOTA (`sota-vision`).

### 2. Извлечение сущностей (Extraction)

Deterministic extraction: извлечение упоминаний людей, организаций, судов, мест, правовых ссылок и событий.

### 3. Канонические персоны (Person Resolution)

Разрешение упоминаний в канонические персоны с отслеживанием алиасов и слияний.

### 4. Классификация преследований (Persecution Classification)

Rule-based классификация политического преследования на основе извлеченных сущностей и событий.

### 5. Росфинмониторинг (Rosfinmonitoring)

Импорт перечня Росфинмониторинга в виде snapshot'ов с версионированием.

### 6. Сопоставление (Matching)

Сопоставление канонических персон с записями Росфинмониторинга.

### 7. Главный запрос (Main Product Query)

Идентификация политически преследуемых лиц, отсутствующих в перечне Росфинмониторинга.

## Архитектура

```text
SourceAdapter.discover(limit)
→ list[SourceReference]
→ SourceIngestion.run()
    → DocumentFetcher.fetch()
    → RawDocument
    → ArticleParser.parse()
    → ParsedArticle
    → IngestionPersistence.save()

Extraction:
ParsedArticle
→ ExtractionDocument
→ RuleBasedEntityExtractor
→ RawMention[]
→ RuleBasedMentionNormalizer
→ NormalizedMention[]
→ RuleBasedEventExtractor
→ SqlAlchemyExtractionPersistence

Person Resolution:
NormalizedMention[]
→ RuleBasedPersonResolver
→ Person
→ PersonAlias[]
→ PersonEventLink[]

Persecution Classification:
Person
→ PersecutionClassificationService
→ PersecutionClassification

Rosfinmonitoring:
RosfinmonitoringSnapshot
→ RosfinmonitoringEntry[]
→ RosfinmonitoringMatcher
→ RosfinMatchResult

Main Product Query:
Person
→ PersecutionClassification
→ RosfinMatchResult
→ CandidateQueryService
→ list[Candidate]
```

## Установка

```bash
# Клонировать репозиторий
git clone <repository-url>
cd ebnv

# Установить зависимости
uv sync

# Создать базу данных PostgreSQL
createdb court_monitor

# Применить миграции
DATABASE_URL="postgresql://user:password@localhost:5432/court_monitor" uv run alembic upgrade head

# Настроить переменные окружения
export DATABASE_URL="postgresql://user:password@localhost:5432/court_monitor"
```

## Использование

### Загрузка статей

```bash
# Загрузить статьи из ОВД-Инфо
uv run python src/main.py discover-and-ingest --source ovd-info --limit 100

# Загрузить конкретную статью
uv run python src/main.py ingest https://ovd.info/news/example
```

### Извлечение сущностей

```bash
# Извлечь сущности из всех статей
uv run python src/main.py extract-entities

# Извлечь из конкретной статьи
uv run python src/main.py extract-entities --article-id 123

# Оценить качество извлечения
uv run python src/main.py evaluate-extraction
```

### Разрешение персон

```bash
# Разрешить все упоминания в канонические персоны
uv run python src/main.py resolve-people

# Разрешить для конкретной статьи
uv run python src/main.py resolve-people --article-id 123

# Оценить качество разрешения
uv run python src/main.py evaluate-er --dataset tests/fixtures/er_golden_dataset.json
```

### Классификация преследований

```bash
# Классифицировать все персоны
uv run python src/main.py classify-persecution

# Классифицировать конкретную персону
uv run python src/main.py classify-persecution --person-id 42

# Оценить качество классификации
uv run python src/main.py evaluate-persecution --dataset tests/fixtures/persecution_golden_dataset.json
```

### Росфинмониторинг

```bash
# Импортировать перечень из файла
uv run python src/main.py import-rosfinmonitoring --file rosfin.xml

# Импортировать из URL
uv run python src/main.py import-rosfinmonitoring --url https://rosfinmonitoring.gov.ru/list

# Показать все snapshot'ы
uv run python src/main.py list-rosfinmonitoring-snapshots

# Оценить качество сопоставления
uv run python src/main.py evaluate-rosfinmatch --dataset tests/fixtures/rosfinmatch_golden_dataset.json
```

### Сопоставление с Росфинмониторингом

```bash
# Сопоставить все персоны с snapshot'ом
uv run python src/main.py match-rosfinmonitoring --snapshot-id 1

# Сопоставить конкретную персону
uv run python src/main.py match-rosfinmonitoring --snapshot-id 1 --person-id 42
```

### Главный запрос

```bash
# Получить список кандидатов (политически преследуемые, не в Росфинмониторинге)
uv run python src/main.py list-candidates --snapshot-id 1

# С фильтром по уверенности
uv run python src/main.py list-candidates --snapshot-id 1 --min-confidence 0.8

# Вывести в файл
uv run python src/main.py list-candidates --snapshot-id 1 --output-path candidates.json
```

### End-to-end тестирование

```bash
# Запустить полный pipeline тест
uv run pytest tests/test_end_to_end.py -v

# Запустить все тесты
uv run pytest

# Проверить типы
uv run mypy --strict src tests

# Проверить стиль кода
uv run ruff check src tests
```

## Документация

Подробная документация доступна в [docs/wiki](docs/wiki/):

- [Overview](docs/wiki/Overview.md) — общий обзор проекта
- [Pipeline](docs/wiki/Pipeline.md) — полный pipeline обработки данных
- [Persons](docs/wiki/Persons.md) — модель канонических персон
- [Persecution-Classification](docs/wiki/Persecution-Classification.md) — классификация преследований
- [Rosfinmonitoring](docs/wiki/Rosfinmonitoring.md) — интеграция с Росфинмониторингом
- [Extraction](docs/wiki/Extraction.md) — извлечение сущностей
- [Evaluation](docs/wiki/Evaluation.md) — оценка качества

Архитектурные решения в [docs/adr](docs/adr/):

- [ADR 0001](docs/adr/0001-dual-search-backend.md) — dual search backend
- [ADR 0002](docs/adr/0002-drop-dense-hybrid-search.md) — отказ от dense/hybrid search
- [ADR 0003](docs/adr/0003-rule-based-extraction-baseline.md) — rule-based extraction baseline
- [ADR 0004](docs/adr/0004-canonical-person-model.md) — модель канонических персон
- [ADR 0005](docs/adr/0005-entity-resolution-strategy.md) — стратегия разрешения сущностей
- [ADR 0006](docs/adr/0006-persecution-classification-strategy.md) — стратегия классификации преследований
- [ADR 0007](docs/adr/0007-rosfinmonitoring-snapshot-model.md) — модель snapshot'ов Росфинмониторинга

## Тестирование

```bash
# Unit tests
uv run pytest tests/test_person_models.py -v
uv run pytest tests/test_persecution_classifier.py -v
uv run pytest tests/test_rosfinmonitoring_matcher.py -v

# Integration tests
uv run pytest tests/test_end_to_end.py -v

# Evaluation
uv run pytest tests/test_er_evaluation.py -v
uv run pytest tests/test_persecution_evaluation.py -v
uv run pytest tests/test_rosfin_match_evaluation.py -v

# All tests
uv run pytest

# Coverage
uv run pytest --cov=src --cov-report=html
```

## Требования

- Python 3.13+
- PostgreSQL 14+
- uv (Python package manager)

## Лицензия

Проект находится в разработке.
