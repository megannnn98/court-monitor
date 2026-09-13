# Getting Started с court-monitor

## Требования

- Python 3.13+
- PostgreSQL (уже запущен на порту 5433)
- uv (менеджер пакетов Python)

## Установка

### 1. Клонируйте репозиторий и установите зависимости

```bash
cd /home/b/Documents/ebnv
uv sync
```

### 2. Настройте переменные окружения

Создайте файл `.env` или экспортируйте переменные:

```bash
# База данных для разработки
export DATABASE_URL="postgresql://court_monitor:court_monitor_dev@localhost:5433/court_monitor"

# База данных для тестов
export TEST_DATABASE_URL="postgresql://court_monitor:court_monitor_dev@localhost:5433/court_monitor_test"
```

Или добавьте в `~/.bashrc` / `~/.zshrc`:

```bash
echo 'export DATABASE_URL="postgresql://court_monitor:court_monitor_dev@localhost:5433/court_monitor"' >> ~/.zshrc
echo 'export TEST_DATABASE_URL="postgresql://court_monitor:court_monitor_dev@localhost:5433/court_monitor_test"' >> ~/.zshrc
source ~/.zshrc
```

### 3. Примените миграции базы данных

```bash
# Применить все миграции
uv run alembic upgrade head

# Проверить текущую версию
uv run alembic current
```

Ожидаемый вывод:
```
j4k5l6m7n8o9 (head)
```

### 4. Проверьте установку

```bash
# Запустить тесты
uv run pytest -v

# Проверить типы
uv run mypy --strict src tests

# Проверить стиль кода
uv run ruff check src tests
```

Ожидаемый результат: **239 tests passed**

## Первый запуск

### Вариант 1: Загрузка реальных статей

```bash
# Загрузить 10 статей из ОВД-Инфо
uv run python src/main.py discover-and-ingest --source ovd-info --limit 10
```

### Вариант 2: Использование тестовых данных

Если хотите быстро проверить pipeline без загрузки реальных статей, используйте end-to-end тест:

```bash
uv run pytest tests/test_end_to_end.py -v -s
```

## Полный pipeline

### Шаг 1: Загрузка статей

```bash
# Загрузить статьи из ОВД-Инфо
uv run python src/main.py discover-and-ingest --source ovd-info --limit 50

# Или загрузить конкретную статью
uv run python src/main.py ingest https://ovd.info/news/example-article
```

### Шаг 2: Извлечение сущностей

```bash
# Извлечь сущности из всех статей
uv run python src/main.py extract-entities

# Или из конкретной статьи
uv run python src/main.py extract-entities --article-id 1
```

Результат: извлеченные упоминания людей, организаций, судов, мест, правовых ссылок и событий.

### Шаг 3: Разрешение персон

```bash
# Создать канонические персоны из упоминаний
uv run python src/main.py resolve-people
```

Результат: канонические персоны с алиасами, связями с событиями.

### Шаг 4: Классификация преследований

```bash
# Классифицировать всех персон
uv run python src/main.py classify-persecution
```

Результат: классификация каждой персоны (political/non_political/uncertain) с уверенностью и причинами.

### Шаг 5: Импорт Росфинмониторинга

```bash
# Импортировать из файла
uv run python src/main.py import-rosfinmonitoring --file path/to/rosfin.xml

# Или из URL
uv run python src/main.py import-rosfinmonitoring --url https://rosfinmonitoring.gov.ru/list
```

Результат: snapshot Росфинмониторинга с записями.

### Шаг 6: Сопоставление с Росфинмониторингом

```bash
# Сопоставить всех персон с snapshot'ом
uv run python src/main.py match-rosfinmonitoring --snapshot-id 1
```

Результат: для каждой персоны определен статус (matched/not_matched/ambiguous).

### Шаг 7: Получение списка кандидатов

```bash
# Получить политически преследуемых, отсутствующих в Росфинмониторинге
uv run python src/main.py list-candidates --snapshot-id 1 --output-path candidates.json
```

Результат: JSON файл со списком кандидатов.

## Использование API

### Запуск API сервера

```bash
uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
```

API документация: http://localhost:8000/docs

### Примеры запросов

```bash
# Получить список всех персон
curl http://localhost:8000/persons?limit=10

# Получить информацию о персоне
curl http://localhost:8000/persons/1

# Получить классификацию преследования
curl http://localhost:8000/persons/1/persecution

# Получить список кандидатов
curl "http://localhost:8000/candidates?snapshot_id=1&min_confidence=0.8"

# Получить snapshot'ы Росфинмониторинга
curl http://localhost:8000/rosfinmonitoring/snapshots
```

## Проверка данных

### Через psql

```bash
# Подключиться к базе данных
PGPASSWORD=court_monitor_dev psql -h localhost -p 5433 -U court_monitor -d court_monitor

# Количество статей
SELECT COUNT(*) FROM parsed_articles;

# Количество персон
SELECT COUNT(*) FROM persons;

# Классификации преследований
SELECT status, COUNT(*) FROM persecution_classifications GROUP BY status;

# Последние статьи
SELECT id, title, published_at FROM parsed_articles ORDER BY id DESC LIMIT 5;
```

### Через Python

```python
from sqlalchemy import create_engine, text

engine = create_engine("postgresql://court_monitor:court_monitor_dev@localhost:5433/court_monitor")

with engine.connect() as conn:
    result = conn.execute(text("SELECT COUNT(*) FROM parsed_articles"))
    print(f"Количество статей: {result.scalar()}")
```

## Оценка качества

```bash
# Оценить качество извлечения сущностей
uv run python src/main.py evaluate-extraction

# Оценить качество разрешения персон
uv run python src/main.py evaluate-er --dataset tests/fixtures/er_golden_dataset.json

# Оценить качество классификации
uv run python src/main.py evaluate-persecution --dataset tests/fixtures/persecution_golden_dataset.json
```

## Полезные команды

### Просмотр логов

```bash
# Показать последние 10 статей
PGPASSWORD=court_monitor_dev psql -h localhost -p 5433 -U court_monitor -d court_monitor \
  -c "SELECT id, title, published_at FROM parsed_articles ORDER BY id DESC LIMIT 10;"

# Показать персоны с их классификацией
PGPASSWORD=court_monitor_dev psql -h localhost -p 5433 -U court_monitor -d court_monitor \
  -c "SELECT p.id, p.canonical_name, pc.status, pc.confidence
      FROM persons p
      LEFT JOIN persecution_classifications pc ON p.id = pc.person_id
      ORDER BY p.id;"
```

### Очистка данных

```bash
# Удалить все данные (осторожно!)
PGPASSWORD=court_monitor_dev psql -h localhost -p 5433 -U court_monitor -d court_monitor \
  -c "TRUNCATE TABLE rosfin_matches, rosfinmonitoring_entries, rosfinmonitoring_snapshots,
      persecution_classifications, person_event_links, person_aliases, persons,
      event_entity_mentions, extracted_events, entity_mentions, article_extraction_runs,
      parsed_articles, source_documents, sources RESTART IDENTITY CASCADE;"
```

### Откат миграций

```bash
# Откатить последнюю миграцию
uv run alembic downgrade -1

# Откатить все миграции
uv run alembic downgrade base

# Применить все миграции заново
uv run alembic upgrade head
```

## Тестирование

```bash
# Все тесты
uv run pytest

# Конкретный тест
uv run pytest tests/test_end_to_end.py -v

# С покрытием
uv run pytest --cov=src --cov-report=html

# Открыть отчет о покрытии
open htmlcov/index.html
```

## Структура проекта

```
src/
├── main.py                    # CLI интерфейс
├── api.py                     # FastAPI API
├── models.py                  # Domain модели
├── orm_models.py              # SQLAlchemy модели
├── database.py                # Подключение к БД
├── person_*.py                # Работа с персонами
├── persecution_*.py           # Классификация преследований
├── rosfinmonitoring_*.py      # Росфинмониторинг
├── extraction_*.py            # Извлечение сущностей
└── *_evaluation.py            # Оценка качества

tests/
├── test_*.py                  # Тесты
└── fixtures/                  # Тестовые данные

docs/
├── wiki/                      # Документация
└── adr/                       # Архитектурные решения
```

## Решение проблем

### Проблема: "DATABASE_URL environment variable is not set"

**Решение:**
```bash
export DATABASE_URL="postgresql://court_monitor:court_monitor_dev@localhost:5433/court_monitor"
```

### Проблема: "relation does not exist"

**Решение:**
```bash
uv run alembic upgrade head
```

### Проблема: "No module named '...'"

**Решение:**
```bash
uv sync
```

### Проблема: API не запускается

**Решение:**
```bash
# Проверьте, что переменные окружения установлены
echo $DATABASE_URL

# Проверьте, что база данных доступна
PGPASSWORD=court_monitor_dev psql -h localhost -p 5433 -U court_monitor -d court_monitor -c "SELECT 1;"

# Запустите API с явным указанием хоста
uvicorn src.api:app --host 127.0.0.1 --port 8000
```

## Следующие шаги

1. **Изучите документацию**: `docs/wiki/`
2. **Прочитайте ADR**: `docs/adr/` для понимания архитектурных решений
3. **Попробуйте API**: запустите `uvicorn src.api:app` и откройте http://localhost:8000/docs
4. **Запустите pipeline**: загрузите статьи и пройдитесь по всем шагам
5. **Изучите тесты**: `tests/test_end_to_end.py` показывает полный pipeline

## Дополнительные ресурсы

- [Pipeline документация](docs/wiki/Pipeline.md)
- [Person Resolution](docs/wiki/Persons.md)
- [Persecution Classification](docs/wiki/Persecution-Classification.md)
- [Rosfinmonitoring](docs/wiki/Rosfinmonitoring.md)
- [API документация](http://localhost:8000/docs) (после запуска API)

---

**Готово!** Теперь вы можете работать с court-monitor. Начните с загрузки статей и прохождения всех шагов pipeline.
