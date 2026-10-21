# Пересборка образа и выпуск

## Зачем это нужно

Как собрать образ `court-monitor:local` из текущего кода и перезапустить на нём сайт (API)
и мониторинг (Dagster). Всё выполняется в корне репозитория.

Это operator runbook: команды рассчитаны на локальный production-like compose,
где API и Dagster используют один образ, а миграции запускаются отдельным шагом.

## Быстрый сценарий

```bash
git switch main && git pull                       # что выпускаем
df -h /home                                        # нужно ≥ 15 ГБ свободного места (GPU-режим)
export BUILD_COMMIT=$(git rev-parse --short HEAD) BUILD_TIME=$(date -u +%FT%TZ) BUILD_TAG=$(git describe --tags --always)
BUILDX_BUILDER=default docker compose -f compose.yaml \
  --profile production build                       # 1. сборка
docker compose -f compose.yaml up -d postgres   # 2. база (если сменился её образ)
docker compose -f compose.yaml --profile migrate run --rm migrate  # 3. миграции
docker compose -f compose.yaml --profile production up -d          # 4. выпуск
docker ps --filter name=ebnv --format '{{.Names}} {{.Status}}'                        # 5. проверка
```

Перед шагом 4 — проверка, что никто не работает: см. «Перед выпуском», пункт 3. Пересоздание
контейнеров обрывает запущенную операцию, и она остаётся `interrupted`.

### Один раз: выпуск, в котором база уходит с образа pgvector (ADR 0028)

В этом выпуске порядок шагов 2 и 3 **обратный**: сначала миграции на ещё работающей
старой базе, потом смена её образа. Миграция `m4n5o6p7q8r9` снимает векторные таблицы и
расширение `vector`; таблицу с векторным индексом нельзя удалить на сервере, где
библиотеки pgvector уже нет.

```bash
git switch main && git pull
export BUILD_COMMIT=$(git rev-parse --short HEAD) BUILD_TIME=$(date -u +%FT%TZ) BUILD_TAG=$(git describe --tags --always)
BUILDX_BUILDER=default docker compose -f compose.yaml \
  --profile production build                       # 1. сборка
# 2. миграции на СТАРОЙ базе. --no-deps: не трогать контейнер postgres — иначе compose
#    пересоздаст его на новом образе раньше времени.
docker compose -f compose.yaml --profile migrate run --rm --no-deps migrate
docker compose -f compose.yaml up -d postgres   # 3. теперь смена образа базы
docker compose -f compose.yaml --profile production up -d          # 4. выпуск
docker ps --filter name=ebnv --format '{{.Names}} {{.Status}}'                        # 5. проверка
```

В логе миграции может стоять «The vector extension is kept: …» — расширение оставлено,
потому что его ставила другая роль или от него зависит что-то вне удаляемых таблиц. Это
не ошибка: миграция прошла, но база по-прежнему требует образа с pgvector, и шаг 3
делать нельзя, пока причина не разобрана.

Если нужно только перезапустить сайт без нового кода — пересборка не нужна:

```bash
docker compose -f compose.yaml --profile production restart api
```

Перед любым повторным `up` или `restart` — та же проверка, что и перед выпуском: в
`operator_operation_runs` не должно быть запусков `pending`/`running` (пункт 3 раздела
«Перед выпуском»).

## Почему именно так

**Один compose-файл или два.** Режим выбирает, что нужно отсеву мусора.

| режим | когда | что собирается | видеокарта |
|---|---|---|---|
| **прод (по умолчанию)** | `JUNK_SCREEN=jev` — отсев спрашивает модель через OpenRouter | базовый образ, без extra `semantic` | не нужна |
| **GPU** | `JUNK_SCREEN=1` — отсев считает эмбеддинги локальной моделью | образ с extra `semantic` (sentence-transformers и torch с CUDA) | нужна |

Команды выше — прод-режим: только `compose.yaml`. GPU-режим отличается одной строкой
`-f compose.gpu.yaml` в каждой команде (`build`, `migrate`, `up`), и требует
`nvidia-container-toolkit` на машине (см. начало `compose.gpu.yaml`):

```bash
docker compose -f compose.yaml -f compose.gpu.yaml --profile production build
```

Чтобы не писать `-f` каждый раз, можно добавить в `.env`:

```
COMPOSE_FILE=compose.yaml:compose.gpu.yaml
```

**`BUILDX_BUILDER=default`.** На машине есть и другой builder (`buildroot-builder`, для
другого проекта). Если compose уйдёт в него, кэша слоёв там нет: сборка заново скачает torch и CUDA
(~10 ГБ) и может съесть место на диске. `default` — встроенный builder Docker, где лежит
кэш этого проекта.

**Сколько идёт сборка.** Образ собирается слоями (см. `Dockerfile`):

| что изменилось | что пересобирается | время |
|---|---|---|
| только код в `packages/`, `apps/`, `migrations/`, `docs/wiki/` | последние слои (копирование кода) | 1–2 мин |
| `pyproject.toml` любого участника workspace или `uv.lock` | слой зависимостей целиком (torch, CUDA, модели) | 10–20 мин, ~10 ГБ загрузки (GPU-режим) |
| `Dockerfile` | начиная с изменённой строки | по ситуации |

Поэтому зависимости без нужды не трогают. В GPU-режиме готовый образ весит около 11 ГБ;
в прод-режиме (`compose.yaml` без `compose.gpu.yaml`) — 1.6 ГБ, torch и CUDA там нет.

**Миграции — отдельным шагом.** API при старте миграции не применяет (ADR 0014). Их
запускает одноразовый сервис `migrate` (`alembic upgrade head`). Если в выпуске нет новых
миграций, шаг ничего не делает — выполнять его всегда безопасно.

**База перед миграциями.** Если в `compose.yaml` сменился образ PostgreSQL, контейнер
`postgres` пересоздаётся на том же volume `postgres_data`: данные сохраняются. Так было
при переходе на образ с pgvector (ADR 0018) и при возврате на обычный
`postgres:18.6-bookworm` (ADR 0028) — это одна и та же сборка PostgreSQL 18.6. Возврат —
единственный случай, когда миграции идут раньше смены образа: см. отдельный блок выше.
Проверено на одноразовой базе: под обычным образом пустые таблицы и само расширение
снимаются и без библиотеки pgvector, а таблица с векторным индексом (`hnsw`, `ivfflat`)
— нет: «could not access file "vector"». Если образ базы не менялся, шаг 2 ничего не
делает.

## Перед выпуском

1. **Место на диске:** `df -h /home`. Сборка и старые слои занимают несколько ГБ.
2. **Резервная копия базы**, если в выпуске есть миграции:

   ```bash
   docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
     > var/backups/court_monitor-$(date +%F-%H%M).dump
   ```

3. **Нет ли живых запусков.** Пересоздание контейнеров обрывает запущенную операцию, и она
   остаётся `interrupted`. Проверьте прямо в базе — ноль означает «никто не работает»:

   ```bash
   docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
     "SELECT count(*) FROM operator_operation_runs WHERE status IN ('"'"'pending'"'"','"'"'running'"'"')"'
   ```

   То же — перед любым повторным `up -d` или `restart`. Страница `/ui/operations` показывает
   то же, но глазом.

## Проверка после выпуска

```bash
docker ps --filter name=ebnv --format '{{.Names}} {{.Status}} {{.Image}}'
docker logs --since 5m ebnv-api-1 2>&1 | grep -E "startup complete|ERROR|Traceback"
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from alembic_version"'
curl -s http://127.0.0.1:8001/api/v1/about
```

- `ebnv-api-1` в статусе `healthy`, в логе `Application startup complete`, без `Traceback`.
- Ревизия базы совпадает с последней миграцией в `migrations/versions/`.
- **Версия в API равна тегу** (`version` и `tag` в ответе `/api/v1/about` — тот самый
  `BUILD_TAG`, что был при сборке). «неизвестно» означает, что build-args не дошли до
  сборки: переменные `BUILD_*` не были выставлены, и образ собран без них. Пересоберите
  с `export BUILD_COMMIT=… BUILD_TIME=… BUILD_TAG=…` (см. «Быстрый сценарий»).
- В браузере: `http://127.0.0.1:8001/health/ready` → `"status": "ready"` (или `degraded`
  с понятной причиной), затем `/ui/candidates` и `/ui/operations`.

Если API не стал `healthy` — `docker logs ebnv-api-1`. Частая причина: база не на
последней миграции (readiness сообщает `schema` не `ok`) — выполнить шаг 3.

## Уборка места

После выпуска старые слои образа остаются в кэше сборки:

```bash
docker image prune -f                                            # образы без тега
docker builder prune --builder default -a -f --filter until=72h  # кэш сборки старше 3 дней
docker builder du --builder default | tail -1                    # сколько осталось
```

Без `-a` фильтр `until` у builder `default` ничего не находит. Полная очистка кэша
(`docker builder prune --builder default -a -f` без фильтра) освободит больше, но следующая
сборка тогда заново скачает зависимости (~10 ГБ, 10–20 мин).

## Откат

Предыдущий код — `git switch --detach <коммит>`, затем те же шаги 1 и 4. Миграции назад
(`alembic downgrade <ревизия>`) — только если новая миграция мешает старому коду; перед
этим — резервная копия из «Перед выпуском».

## Кодовые точки входа

- `Dockerfile` — образ API/Dagster/migrate, `ARG BUILD_COMMIT`/`BUILD_TIME`/`BUILD_TAG`.
- `compose.yaml` — базовые сервисы и production profile; якорь `x-build` передаёт
  `BUILD_*` в сборку.
- `compose.gpu.yaml` — зависимости отсева мусора, GPU.
- `migrations/` — Alembic шаг `migrate`.
- `apps/court-monitor/src/web/build_info.py` — что показывает «О системе» о сборке.
- `apps/court-monitor/src/web/health.py`, `apps/court-monitor/src/web/routers/health.py` — readiness/liveness.

## Ограничения и типичные ошибки

- Не пропускать `migrate`: API сам миграции не применяет.
- GPU-режим (`compose.gpu.yaml`) нужен только для `JUNK_SCREEN=1`. При `JUNK_SCREEN=jev`
  отсев спрашивает модель через OpenRouter, и образ без sentence-transformers и видеокарты
  работает.
- Не собирать без `export BUILD_COMMIT=… BUILD_TIME=… BUILD_TAG=…`: образ получится, но
  «О системе» покажет «неизвестно», и по работающему контейнеру нельзя понять, какой
  это код.
- Проверять живые запуски (`operator_operation_runs`) перед `up -d` и `restart`:
  пересоздание контейнеров обрывает операцию.
- Проверять свободное место до сборки: в GPU-режиме зависимости CUDA и старые слои
  занимают много места.
- Откат к старому коду не всегда значит downgrade БД; downgrade только если
  новая схема мешает старому коду.
