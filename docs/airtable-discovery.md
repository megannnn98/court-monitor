# Airtable Discovery

Дата: 2026-07-26
Статус: частичное исследование. Полная структура требует PAT.

---

## 1. Две существующие базы

| Назначение (предположительно) | Base ID | Shared view | Подтверждено из shared-view URL |
|---|---|---|---|
| Список статей для мониторинга | `app42KQc45WUgqx7A` | `shrEOzzOObIUY6byU` | **Да** — SPA-заголовок страницы: «Airtable - список статьей для мониторинга» |
| Основная база (люди/дела) | `apppAy5vZCrpb53wc` | `shr2RcGSNfQPtqUPs` | Нет — запрос по plain HTTP не вернул читаемого заголовка (таймаут/SPA). Назначение предположительно по контексту задачи. |

> Назначение второй базы нужно **подтвердить** после получения PAT (см.
> открытые вопросы в `docs/discovery.md`).

---

## 2. Что можно и чего нельзя узнать из shared view

Airtable shared view — это SPA на JavaScript. Plain HTTP-запрос
(`httpx`/`curl`) возвращает только каркас страницы с placeholder'ами
(наблюдается: «Drag to adjust frozen columns», «Alert / Lorem ipsum /
Okay»). Конкретные данные таблицы грузятся XHR'ом после отрисовки и
требуют выполнения JS.

Из shared-view URL **гарантированно извлекаются**:

- `base_id` (`app...`);
- `shared_view_id` (`shr...`).

**НЕ извлекаются** из shared-view без JS (и мы сознательно их не обходим):

- названия исходных таблиц (table name / `tbl...`);
- названия и типы колонок;
- скрытые поля;
- связи между таблицами;
- ID записей (`rec...`);
- формулы/вычисляемые поля;
- какие поля обязательны при создании;
- какие поля заполняются вручную vs автоматически.

Это сознательное ограничение Airtable. Спека (§4.3, §30.5) прямо
запрещает обходить его парсингом публичного HTML и требует официальный
Web API.

---

## 3. Что необходимо от оператора

Для перехода от mock к реальной интеграции:

1. **Airtable Personal Access Token (PAT)** с доступом к обеим базам.
   PAT хранится в env (`AIRTABLE_TOKEN`), **никогда** не коммитится.
2. Для каждой базы:
   - `base_id` (есть);
   - `table_id` или `table_name` (нужен оператором из UI Airtable:
     URL вида `https://airtable.com/<base>/<table>` или Developer Hub);
   - при необходимости — `view_id`.
3. Подтверждение назначения второй базы (`apppAy5vZCrpb53wc`).

---

## 4. Режим работы

```yaml
# config/sources.yaml
airtable:
  mode: read_only            # строго read-only до проверки отображения
  write_requires_confirm: true
  bases:
    monitoring_articles:
      base_id: "app42KQc45WUgqx7A"
      shared_view_id: "shrEOzzOObIUY6byU"
      table_name: null        # запросить у оператора
      purpose: "Список статей УК и ключевых слов для мониторинга"
    main_database:
      base_id: "apppAy5vZCrpb53wc"
      shared_view_id: "shr2RcGSNfQPtqUPs"
      table_name: null        # запросить у оператора
      purpose: "Основная база людей/дел (требует подтверждения)"
```

До заполнения `table_name` / получения PAT — `airtable`-адаптер работает в
режиме **mock** (возвращает заданные в fixture записи). Это позволяет
развивать `matching`/`review`/`sync`-логику параллельно.

---

## 5. Слой отображения (mapping), а не прямая связка

Спека (§4.3, §30.10): бизнес-логика не завязывается на имена колонок
Airtable. Реализуется слой `AirtableFieldMapping`:

```
internal Person.surname  <--->  airtable column "Фамилия" (table-dependent)
internal Person.region   <--->  airtable column "Регион"
internal Case.articles   <--->  airtable linked-record column → Articles
...
```

Маппинг хранится в YAML и валидируется командой
`court-monitor airtable validate-mapping`. Любое расхождение модели и
таблицы → ошибка запуска, а не тихая перезапись.

---

## 6. CLI-путь к реальной синхронизации (по спеке §30.8)

```bash
court-monitor airtable inspect           # показать таблицы/колонки (нужен PAT)
court-monitor airtable validate-mapping  # сверить маппинг с реальными полями
court-monitor airtable sync --dry-run    # только план изменений
court-monitor airtable diff              # построчный diff план vs база
# и только потом — подтверждение оператором в UI очереди
```

Любое предлагаемое изменение выводится в виде (§30.9):

```json
{
  "base": "main_database",
  "table": "<определённая при исследовании таблица>",
  "record_id": null,
  "action": "create",
  "reason": "человек не найден в существующей базе",
  "new_values": {},
  "possible_duplicates": [],
  "sources": []
}
```

---

## 7. Жёсткие ограничения (спека §4.3, §30)

- Не создавать, не изменять, не удалять записи без подтверждения.
- Сначала пишем в локальную БД и `ReviewQueue`; синхронизация — отдельный
  шаг после approve.
- Не создавать новую структуру Airtable до исследования существующих баз.
- Внутренняя модель может быть подробнее, но интеграционный слой
  адаптирует её к существующим таблицам, не заставляя пользователя
  перестраивать базы.
- `mode: read_only` до явного включения записи.
- Массовые изменения — только через `--dry-run` + подтверждение.
- Любое изменение журналируется в `AuditLog`.
