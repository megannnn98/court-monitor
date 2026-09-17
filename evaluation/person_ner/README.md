# Person NER evaluation

Три скрипта, отвечающие на один вопрос: лучше ли специализированная модель, чем
capitalized-word эвристики `RuleBasedEntityExtractor`, решает, что является именем
человека. Ничего отсюда не импортируется production-кодом.

## Подготовка

```bash
uv sync --group ner
set -a; source .env; set +a
```

Нужен загруженный raw-корпус в `var/real_world/raw/` (`build-real-world-corpus`) и golden
разметка в `evaluation/real_world/golden/articles/`.

## Порядок запуска

Скрипты образуют цепочку: каждый следующий читает то, что записал предыдущий. Промежуточные
файлы лежат в `var/person_ner/` и не коммитятся.

**1. Сравнение экстракторов** — пишет `var/person_ner/comparison.json`:

```bash
uv run python evaluation/person_ner/compare_person_extraction.py --limit 45 --device cuda
```

Прогоняет обе реализации по golden-статьям, проверяет инвариант
`article.text[start:end] == surface_text` на каждом спане и складывает спаны, тексты и
golden-разметку в один файл. `--device cpu` для замера без GPU.

**2. Разбор расхождений** — читает `comparison.json`, пишет `disagreements_DRAFT.json`:

```bash
uv run python evaluation/person_ner/analyze_comparison.py --examples 25
```

Делит расхождения на «только правила», «только модель» и «границы», печатает их с
контекстом и выгружает в DRAFT-датасет для ручной проверки.

**3. Влияние на entity resolution** — читает `comparison.json`, пишет `downstream.json`:

```bash
EVALUATION_DATABASE_URL="postgresql+psycopg://…/court_monitor_eval" \
  uv run python evaluation/person_ner/downstream_impact.py
```

Прогоняет те же статьи через extraction и resolution по разу на стратегию на одноразовой
базе и сравнивает, сколько получилось canonical persons и решений на ревью. База обязана
оканчиваться на `_eval` или `_test` — иначе скрипт откажется её чистить.

## Статус метрик

Единственная разметка person spans в проекте — `annotation_status: DRAFT`,
`annotation_origin: agent_draft`. Она сделана агентом, не человеком.

Поэтому все precision/recall/F1 в выводе помечены `PRELIMINARY` и служат указателем, куда
смотреть, а не оценкой качества. `disagreements_DRAFT.json` существует именно для того,
чтобы человек прошёл по расхождениям и превратил их в проверенные факты.

## Модельные тесты

Контракт распознавателя проверяется отдельно и по желанию:

```bash
PERSON_NER_MODEL_TESTS=1 uv run pytest -m person_ner_model
```

Без переменной они пропускаются, и обычный прогон тестов не скачивает модель.
