# Пересборка образа и выпуск

Как собрать образ `court-monitor:local` из текущего кода и перезапустить на нём сайт (API)
и мониторинг (Dagster). Всё выполняется в корне репозитория.

## Коротко

```bash
git switch main && git pull                       # что выпускаем
df -h /home                                        # нужно ≥ 15 ГБ свободного места
BUILDX_BUILDER=default docker compose -f compose.yaml -f compose.gpu.yaml \
  --profile production build                       # 1. сборка
docker compose -f compose.yaml -f compose.gpu.yaml up -d postgres   # 2. база (если сменился её образ)
docker compose -f compose.yaml -f compose.gpu.yaml --profile migrate run --rm migrate  # 3. миграции
docker compose -f compose.yaml -f compose.gpu.yaml --profile production up -d          # 4. выпуск
docker ps --filter name=ebnv --format '{{.Names}} {{.Status}}'                        # 5. проверка
```

Если нужно только перезапустить сайт без нового кода — пересборка не нужна:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml --profile production restart api
```

## Почему именно так

**Оба compose-файла.** `compose.gpu.yaml` собирает образ с группами `semantic` и `ner`
(sentence-transformers, torch с CUDA, GLiNER) и отдаёт контейнерам видеокарту. Команда без
него пересоздаст контейнеры без GPU и без семантического шага мониторинга. Чтобы не
писать `-f` каждый раз, можно добавить в `.env`:

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
| только код в `src/`, `migrations/`, `docs/wiki/` | последние слои (копирование кода) | 1–2 мин |
| `pyproject.toml` или `uv.lock` | слой зависимостей целиком (torch, CUDA, модели) | 10–20 мин, ~10 ГБ загрузки |
| `Dockerfile` | начиная с изменённой строки | по ситуации |

Поэтому зависимости без нужды не трогают. Готовый образ весит около 11 ГБ.

**Миграции — отдельным шагом.** API при старте миграции не применяет (ADR 0014). Их
запускает одноразовый сервис `migrate` (`alembic upgrade head`). Если в выпуске нет новых
миграций, шаг ничего не делает — выполнять его всегда безопасно.

**База перед миграциями.** Если в `compose.yaml` сменился образ PostgreSQL (так было с
переходом на `pgvector/pgvector:pg18-bookworm`, ADR 0018), контейнер `postgres`
пересоздаётся на том же volume `postgres_data`: данные сохраняются. Миграция
`t4u5v6w7x8y9` выполняет `CREATE EXTENSION vector` и требует именно нового образа, поэтому
сначала шаг 2, потом шаг 3. Если образ базы не менялся, шаг 2 ничего не делает.

## Перед выпуском

1. **Место на диске:** `df -h /home`. Сборка и старые слои занимают несколько ГБ.
2. **Резервная копия базы**, если в выпуске есть миграции:

   ```bash
   docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
     > var/backups/court_monitor-$(date +%F-%H%M).dump
   ```

3. **Нет ли долгой операции:** страница `/ui/operations` — не идёт ли «Докачать» или другая
   операция. Перезапуск API прерывает запущенную из консоли операцию (она станет
   `interrupted` через 5 минут).

## Проверка после выпуска

```bash
docker ps --filter name=ebnv --format '{{.Names}} {{.Status}} {{.Image}}'
docker logs --since 5m ebnv-api-1 2>&1 | grep -E "startup complete|ERROR|Traceback"
docker compose -f compose.yaml -f compose.gpu.yaml exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from alembic_version"'
```

- `ebnv-api-1` в статусе `healthy`, в логе `Application startup complete`, без `Traceback`.
- Ревизия базы совпадает с последней миграцией в `migrations/versions/`.
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

## Семантический backend

Выпуск не меняет `SEMANTIC_VECTOR_BACKEND`: по умолчанию остаётся Qdrant. Переход на
pgvector — отдельная процедура с паузой мониторинга и полной перестройкой индекса, см.
раздел «Switching a deployment (runbook)» в
[ADR 0018](../adr/0018-pgvector-vector-store.md).
