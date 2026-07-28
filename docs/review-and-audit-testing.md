# Как проверить ReviewItem + AuditLog (Etap 4)

Этот документ покрывает две таблицы и CLI-команды, добавленные в двух
последовательных срезах:

- `ReviewItem` — очередь проверки оператора. Два producer'а: сбой парсера
  (`parser_status=parser_failed`, срез 1) и блокировка/ошибка источника
  (`FetchHealth.blocked`/`http_error`/`timeout`, `item_type=source_blocked`,
  срез 2 / D-011).
- `AuditLog` — append-only аудит операторских решений (кто/что/когда
  изменил): пишется при confirm/reject-match и при resolve-review-item.

Ниже — план ручной проверки на чистой БД, без сети (всё на fixtures/моках).
Автоматические тесты см. в конце.

## 0. Подготовка

```bash
git checkout source-blocked-review-item-d011
make install            # или: uv sync
rm -f court_monitor.db  # если есть старая БД от master — начать с чистой
uv run court-monitor init-db
uv run court-monitor doctor   # должно быть "✓ All checks passed."
```

`doctor` также подтверждает, что дублирование вывода (settings_source /
"All checks passed") больше не печатается дважды.

## 1. Миграции 0006/0007 — up и down

```bash
uv run alembic current                 # текущая голова
uv run alembic history | head -10      # 0006_review_items, 0007_audit_log видны

# Проверить таблицы
sqlite3 court_monitor.db ".tables"     # должны быть review_items и audit_log

# Откатить и накатить обратно (проверка downgrade)
uv run alembic downgrade -2
sqlite3 court_monitor.db ".tables"     # review_items и audit_log должны исчезнуть
uv run alembic upgrade head
sqlite3 court_monitor.db ".tables"     # снова на месте
```

**Ожидаемо:** ни на одном шаге не должно быть исключений/трейсбеков.

## 2. ReviewItem: сбой парсера создаёт запись в очереди

Проще всего воспроизвести сбой парсера — временно подсунуть заведомо
проблемный HTML через существующий fixture-режим `sudrf` с fetch-source,
либо (быстрее) напрямую через Python:

```bash
uv run python - <<'EOF'
from sqlalchemy.orm import sessionmaker
from court_monitor.storage.db import make_engine
from court_monitor.services import ingest_fetch_result, parse_and_extract
from court_monitor.sources.base import FetchResult
from court_monitor.domain.models import SourceType
from court_monitor.config.loader import load_monitoring
from unittest.mock import patch
import court_monitor.services as services_module

engine = make_engine()
session = sessionmaker(bind=engine, expire_on_commit=False)()

result = FetchResult.from_content(
    url="https://test/broken-demo",
    content="<html><body>irrelevant</body></html>",
    source_type=SourceType.sudrf,
    source_name="demo-court",
    source_id="demo-court-id",
)
doc, _ = ingest_fetch_result(session, result)
session.commit()

def _boom(_html):
    raise ValueError("демо: парсер сломан")

with patch.object(services_module, "parse_press_release", _boom):
    parse_and_extract(session, doc, load_monitoring())
    session.commit()

print("document_id:", doc.id, "status:", doc.parser_status)
EOF
```

Теперь проверить очередь:

```bash
uv run court-monitor list-review-items
# Ожидаемо: одна строка, item_type=parser_failed, priority=high, статус=pending

uv run court-monitor list-review-items --status pending
uv run court-monitor list-review-items --type parser_failed
```

### 2а. Идемпотентность — повторный сбой не плодит дубликаты

⚠️ `court-monitor reprocess-document <id>` здесь **не подойдёт** — он
использует настоящий `parse_press_release`, который на этом fixture-контенте
не падает (проверено), так что реального повторного сбоя не будет. Чтобы
воспроизвести именно повторный сбой того же документа, повторите блок
Python из шага 2 **для того же `doc.id`** (замените
`ingest_fetch_result(...)` на `repo.get_document(session, <id>)`) — например:

```bash
uv run python - <<'EOF'
from sqlalchemy.orm import sessionmaker
from court_monitor.storage.db import make_engine
from court_monitor.services import parse_and_extract
from court_monitor.storage import repository as repo
from court_monitor.config.loader import load_monitoring
from unittest.mock import patch
import court_monitor.services as services_module

engine = make_engine()
session = sessionmaker(bind=engine, expire_on_commit=False)()
doc = repo.get_document(session, <id-документа-из-шага-2>)

def _boom(_html):
    raise ValueError("демо: парсер снова сломан")

with patch.object(services_module, "parse_press_release", _boom):
    parse_and_extract(session, doc, load_monitoring())
    session.commit()
EOF

uv run court-monitor list-review-items --type parser_failed
# Ожидаемо: всё ещё 1 запись (та же), не 2 — только data_json/время обновились
```

(На реальном проде `reprocess-document` — правильный инструмент для
документа, который действительно ломает парсер; здесь мы вручную
симулируем сбой, так как подобрать HTML, гарантированно валящий
`parse_press_release`, для демо непрактично.)

## 3. Resolve / dismiss review item

```bash
uv run court-monitor resolve-review-item <id> --comment "починил селектор"
uv run court-monitor list-review-items --status resolved

# Или отклонить как нерелевантный:
uv run court-monitor resolve-review-item <id2> --dismiss --comment "ложное срабатывание"
uv run court-monitor list-review-items --status dismissed
```

**Проверить, что после resolve новый такой же сбой открывает новую запись**
(а не переиспользует закрытую) — повторить шаг 2 для того же документа
после resolve и убедиться, что `list-review-items` показывает 2 записи
(1 resolved + 1 новая pending).

## 4. AuditLog: confirm/reject-match

Нужен хотя бы один MatchCandidate — быстрее всего через существующий
demo-флоу RFM + fixture-документ:

```bash
uv run court-monitor fetch-source fedsfm --file tests/fixtures/rfm/persons_match.xml
uv run court-monitor fetch-source 2zovs   # или другой источник с fixture, дающий person-факт
uv run court-monitor parse-pending
uv run court-monitor generate-matches
uv run court-monitor list-matches
```

Если кандидат создался (`score >= threshold`):

```bash
uv run court-monitor confirm-match <id> --comment "проверено вручную" --operator "ваше-имя"
```

Проверить аудит напрямую через sqlite (пока нет CLI-команды `list-audit-log`):

```bash
sqlite3 court_monitor.db "SELECT id, actor, action, object_type, object_id, old_value_json, new_value_json, correlation_id FROM audit_log ORDER BY id DESC LIMIT 5;"
```

**Ожидаемо:** запись с `action=match_status_change`, `actor` = переданный
`--operator` (или ваш логин, если флаг не передавали — по умолчанию
`getpass.getuser()`), `old_value_json={"status": "pending"}`,
`new_value_json={"status": "confirmed", "comment": "..."}`.

Повторить с `reject-match` на другом кандидате — должна появиться вторая
запись с `action=match_status_change`, `new_value_json.status=rejected`.

### 4а. `--operator` по умолчанию

Выполнить `confirm-match`/`reject-match` **без** `--operator` — в
`audit_log.actor` должно оказаться имя текущего пользователя ОС
(`getpass.getuser()`), не `"unknown"` (если только вы явно не в окружении
без имени пользователя).

## 4б. ReviewItem при блокировке источника (D-011)

`SudrfAdapter`/`TelegramChannelAdapter` при `FetchHealth.blocked`/
`http_error`/`timeout`/пустом теле теперь создают
`ReviewItem(item_type="source_blocked")` вместо молчаливого пропуска.
Поскольку `HttpClient` создаётся внутри адаптера без точки внедрения,
воспроизвести это без реальной сети проще всего тем же способом, что и
шаг 2 — через мок:

```bash
uv run python - <<'EOF'
from sqlalchemy.orm import sessionmaker
from court_monitor.storage.db import make_engine
from court_monitor.services import process_source
from court_monitor.config.loader import SourceConfig, load_monitoring
from court_monitor.domain.models import SourceBackend, SourceType, FetchHealth
from court_monitor.sources.http_client import HttpResponse
from unittest.mock import patch
import court_monitor.sources.sudrf as sudrf_module

engine = make_engine()
session = sessionmaker(bind=engine, expire_on_commit=False)()

class _FakeClient:
    def __init__(self, resp): self._resp = resp
    def get(self, url): return self._resp
    def __enter__(self): return self
    def __exit__(self, *a): pass

resp = HttpResponse(
    status=403,
    text="проверка безопасности",
    url="https://2zovs.sudrf.ru/modules.php?name=press",
    health=FetchHealth.blocked,
)
config = SourceConfig(
    name="2zovs-demo", type=SourceType.sudrf, backend=SourceBackend.http,
    base_url="https://2zovs.sudrf.ru", paths=("/modules.php?name=press",),
)

with patch.object(sudrf_module, "HttpClient", lambda: _FakeClient(resp)):
    stats = process_source(session, config, load_monitoring())
    session.commit()

print("stats:", stats)
EOF

uv run court-monitor list-review-items --type source_blocked
# Ожидаемо: 1 запись, item_type=source_blocked, priority=high, документ=- (нет document_id)
```

**Идемпотентность:** повторить блок ещё раз (тот же `config.name`) — записей
должна остаться **1**, только с обновлённым `data_json` (проверено).

**Не должно создавать ReviewItem:** `health=FetchHealth.not_modified`
(304 — нет новых данных, это не сбой) и отсутствие локальной fixture для
Telegram-адаптера в `live=False` режиме (dev/test-особенность, не прод-сбой).

## 5. Что явно НЕ покрыто (ожидаемое поведение)

- Нет команды `court-monitor list-audit-log` — просмотр только через sqlite
  напрямую (см. шаг 4). Если это нужно оператору уже сейчас — дайте знать,
  добавим в следующий срез.
- `Person`/`Case`/`CourtEvent` не введены — см. D-001.

## 6. Автоматические тесты

```bash
make test                                                   # весь набор (208 тестов)
uv run pytest tests/unit/test_review_and_audit.py -v        # repo-функции ReviewItem/AuditLog
uv run pytest tests/unit/test_sudrf_http_adapter.py -v       # FetchProblem: SudrfAdapter
uv run pytest tests/unit/test_telegram_adapter_http.py -v    # FetchProblem: TelegramChannelAdapter
uv run pytest tests/integration/test_source_blocked_review_item.py -v  # wiring source_blocked
uv run pytest tests/integration/test_pipeline.py -v -k review_item     # wiring parser_failed
uv run pytest tests/unit/test_matching.py -v -k audit_log    # audit-запись при confirm/reject
uv run ruff check .
uv run mypy src
```

Все пункты должны проходить без ошибок на чистом чекауте ветки.
