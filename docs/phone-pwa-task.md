# Задание: court-monitor целиком на Android-телефоне (Termux) + embeddings через OpenRouter

Репозиторий: `/home/b/Documents/ebnv` (публичный: https://github.com/megannnn98/court-monitor),
ветка от `main` (сейчас `d9b3900`). Python 3.13, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL.
Отвечай и пиши сообщения коммитов в стиле репозитория (см. `git log`).

## Цель

Сейчас сайт «Следователь» работает на компьютере в Docker (`compose.yaml` + `compose.gpu.yaml`):
FastAPI (`src/api.py`, UI в `src/web/ui/`), PostgreSQL, загрузка 74 новостных источников
(70 — Telegram через `https://t.me/s/<канал>`, 4 сайта), шаги конвейера 1–5 (кнопки на
«Управлении», `src/web/ui/pipeline.py`; консоль запускает их подпроцессом
`sys.executable src/main.py <команда>`, `src/operator_console.py:557`), DeepSeek через
OpenRouter (`src/entities/llm.py`), отсев мусора на embeddings (модель на GPU).

Нужно: **тот же сайт целиком на телефоне пользователя, без компьютера**. Всё, что считалось на
видеокарте, — через API OpenRouter.

## Решения (приняты пользователем, не пересматривать)

| Вопрос | Решение |
|---|---|
| Где работает | весь сервер на Android через **Termux** (из F-Droid): Python + PostgreSQL + pgvector; сайт в браузере телефона по `localhost`; значок на рабочем столе (PWA-манифест) |
| GPU | embeddings — через **OpenRouter embeddings API** тем же ключом `OPENROUTER_API_KEY` |
| DeepSeek | как сейчас, OpenRouter |
| Копии | **только телефон**: базу один раз переносим с компьютера дампом (~15 МБ), синхронизации нет |
| Телефон | пользователя; ориентир Android 9+, 8 ГБ RAM — проверить `termux-info` при установке |
| Запуск шагов | **вручную**, как сейчас, кнопками «Управления»; во время обработки `termux-wake-lock` |
| Бэкап | после каждого успешного шага 5 — `pg_dump -Fc` в облако через **rclone** (по умолчанию Google Drive — облако пользователь ещё не подтвердил, сделать настраиваемым), хранить 14 последних |
| Установка/обновление | `phone/install.sh` (один раз) и `phone/update.sh` (git pull → зависимости → `alembic upgrade head` → перезапуск) |
| Мобильная сеть | шаг 1 разрешён, но перед запуском на мобильной сети — предупреждение с примерным объёмом трафика |
| Перечень РФМ | как сейчас, напрямую с `fedsfm.ru` (`src/entities/rf_check.py:40`); из Казахстана открывается без VPN (проверено: снимки 16.09 и 25.09); при неудаче шаг 5 уже сверяет по последнему снимку |

## Факты о коде (проверены)

- Embeddings спрятаны за протоколом `TextEmbedder` (`src/semantic_retrieval/embeddings.py:29`,
  методы `embed_query`, `embed_documents`, свойства `model_id`, `dimension`); сейчас одна
  реализация — `SentenceTransformerEmbedder` (локальная модель, torch).
- Отсев мусора: `src/monitoring/junk_screen.py` (логистическая регрессия поверх embedding
  «заголовок. первые 1 500 символов»; файл модели `src/monitoring/junk_screen_model.json` —
  обучен на `intfloat/multilingual-e5-base`, порог 0.5202). Включается `JUNK_SCREEN=1`
  (в `compose.yaml` сейчас 1). Включённый и сломанный — падает до удаления.
- Замер и переобучение отсева: `src/evaluation/junk_screen/` (`corpus.py`, `labels.py`,
  `measure.py` — `run(models)` сравнивает способы, `export(model_id)` пишет файл модели);
  метки — `evaluation/junk_screen/labels.json`; сырые данные (`var/junk_screen/sample.jsonl`
  с текстами, `labels.jsonl`) — локально, `var/` в .gitignore; отчёт —
  `reports/junk_screen_2026-09-27.md`. Выборка — из дампа
  `var/backups/court_monitor-2026-09-18-pre-registry.dump` (развёрнут в тестовом Postgres как
  база `junk_eval`, контейнер `ebnv-pgvector-test`, порт 5434).
- OpenRouter embeddings: `POST https://openrouter.ai/api/v1/embeddings`, модели (проверено
  27.09): `intfloat/multilingual-e5-large` ($0.01/1M), `baai/bge-m3` ($0.01/1M),
  `qwen/qwen3-embedding-8b` ($0.01/1M), `qwen/qwen3-embedding-4b` ($0.02/1M),
  `openai/text-embedding-3-small` ($0.02/1M), `openai/text-embedding-3-large`, бесплатные
  (`nvidia/nemotron-3-embed-1b:free`, `liquid/lfm-2.5-embedding-350m:free`). Нашей
  `multilingual-e5-base` там **нет** → отсев переобучить на выбранной модели.
- Семантический поиск (pgvector/Qdrant) в UI не используется (`src/web/ui` его не вызывает),
  на проде выключен (`SEMANTIC_VECTOR_BACKEND` пуст). Но миграция
  `migrations/versions/t4u5v6w7x8y9_add_pgvector_semantic_vectors.py` делает
  `CREATE EXTENSION vector`, а `pg_trgm` — `n8o9p0q1r2s3`, `r2s3t4u5v6w7`; дамп содержит
  векторные таблицы → на телефоне нужен pgvector (есть ли пакет в Termux — **не проверено**;
  если нет — собрать из исходников в Termux).
- Распознавание имён нейросетью (GLiNER) выключено: `PERSON_EXTRACTION_STRATEGY=rule_based`
  по умолчанию (`compose.gpu.yaml`); на телефоне оставить `rule_based`.
- Ключи — только в `.env` (не читать и не печатать; на телефоне пользователь вводит ключ сам).

## Шаги (после каждого — короткий отчёт пользователю и вопрос, продолжать ли)

### Шаг 1. Embeddings через OpenRouter (на компьютере)
- Класс `OpenRouterEmbedder(TextEmbedder)`: батчи, повтор при временных ошибках, учёт
  стоимости (как `Spend` в `src/entities/llm.py`, ответ содержит `usage`), таймауты; префиксы
  «query: »/«passage: » — только для моделей E5 (`embedding_profile` уже знает профили —
  расширить).
- Выбор реализации из окружения (например, `EMBEDDING_PROVIDER=openrouter` +
  `EMBEDDING_MODEL_ID`), локальная `SentenceTransformerEmbedder` остаётся для совместимости.
- Замер: `measure.run([...])` с API-embeddings для e5-large, bge-m3, qwen3-embedding-8b,
  text-embedding-3-small на той же выборке и том же делении по дате (калибровка до
  2026-08-29, проверка после; порог — только на калибровке). Выбрать лучшую по проверке
  (главное — сколько пропущенных дел спасено при точности удержанного ≥ ~0.5), `export` —
  новый `junk_screen_model.json` с `model_id` провайдера. Отчёт дополнить таблицей.
- `junk_screen.screen_from_env` строит эмбеддер по провайдеру из файла модели/окружения.
- Проверки: тесты с поддельным HTTP (без сети), `ruff`, `mypy --strict`, полный прогон.
  Каждый регрессионный тест проверить мутацией.

### Шаг 2. Код под Termux
- Запуск без Docker: переменные окружения из `.env`, `uvicorn api:app --host 127.0.0.1`,
  PostgreSQL из Termux; команды шагов уже идут через `sys.executable` — проверить, что
  ничего не завязано на `/app`, `/opt/venv`, Docker-сеть, GPU.
- `phone/install.sh`: пакеты Termux (python, postgresql, clang/make для pgvector при
  необходимости, git, rclone), клон репозитория, venv + зависимости без групп
  `semantic`/`ner` (torch не нужен), initdb, создание БД и пользователя, pgvector,
  восстановление дампа с компьютера, `.env` по шаблону (ключ вводит пользователь),
  ярлык запуска (Termux:Widget/`termux-open-url`), `termux-wake-lock` на время обработки.
- `phone/update.sh`: `git pull` → зависимости → `alembic upgrade head` → перезапуск сервера;
  понятный откат (`git checkout <предыдущий>` + описание в вики).
- PWA: `manifest.webmanifest` и значок, чтобы сайт с `localhost` ставился на рабочий стол
  (service worker — только если нужен для установки; офлайн-режим не требуется).
- Шаг 1 на мобильной сети: предупреждение с оценкой объёма (оценку взять из замера объёма
  одного круга загрузки — измерить на компьютере по байтам ответов).
- Бэкап после успешного шага 5: `pg_dump -Fc` → rclone remote (имя remote и путь — из
  окружения), ротация 14; сбой бэкапа не роняет шаг, а видно в логе и на карточке.
- Не ломать работу на компьютере в Docker.

### Шаг 3. Инструкция и установка
- Страница вики `docs/wiki/Phone.md`: что поставить (F-Droid, Termux, Termux:API,
  Termux:Widget), шаги установки, перенос дампа с компьютера, настройка rclone, обновление,
  откат, ограничения (Android усыпляет фон; телефон должен быть на зарядке при долгих шагах).
- Установку на телефон выполняет пользователь по инструкции; агент готовит и проверяет всё,
  что можно проверить без телефона.

### Шаг 4. Проверка на телефоне (с пользователем)
- Полный круг шагов 1–5, время и трафик, открытие досье/«Результата», бэкап в облако.

## Правила репозитория
- Не читать и не печатать `.env`. Прод на компьютере не трогать без явной просьбы
  (контейнеры `ebnv-api-1`, `ebnv-postgres-1`); не мигрировать прод, не пересобирать образ.
- Не коммитить `CONTEXT.md`, `PLAN_*.md`, `TASK_*.md`, `WORK_REPORT_*.md`,
  `analytical-ui-redesign.bundle`. Коммиты в свою ветку, не пушить без просьбы.
- Тесты: `TEST_DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_test@127.0.0.1:5434/court_monitor_test`
  (`docker start ebnv-pgvector-test`; миграции тестовой БД: `DATABASE_URL=<тот же>
  .venv/bin/alembic upgrade head`), `uv run pytest`, `uv run ruff format --check src tests
  migrations`, `uv run ruff check src tests migrations`, `uv run mypy --strict src`.
- Расходы OpenRouter на замер — центы; держать в пределах $1, отчитаться о фактической сумме.
- Минимальные изменения, стиль окружающего кода (докстринги на английском в том же тоне,
  интерфейс по-русски).

## Открытые вопросы к пользователю
- Облако для бэкапа (Google Drive по умолчанию — подтвердить).
- Модель телефона (проверить `termux-info`; если слабее ориентира — вернуться к решению).

## Результат
Коммиты в ветке, зелёный полный прогон, отчёт по каждому шагу: что сделано, что проверено
(в т. ч. мутацией), фактические метрики отсева на новой модели и стоимость, что не
проверено и почему, команды для пользователя.
