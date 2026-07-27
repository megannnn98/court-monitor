# Database Bootstrap — Диагностика

## Поток получения database_url

### 1. Settings (единственный источник истины)

```
CM_DATABASE_URL (env var)
    → .env файл (CM_DATABASE_URL=...)
    → дефолт: sqlite:///./court_monitor.db
```

Файл: `src/court_monitor/config/settings.py`

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CM_", env_file=".env", ...)
    database_url: str = "sqlite:///./court_monitor.db"
```

Приоритет: env var > .env > дефолт.

### 2. Приложение (CLI)

```
settings.database_url
    → storage/db.py: make_engine(url) → create_engine(...)
    → CLI команды используют engine
```

### 3. Alembic (миграции)

```
settings.database_url
    → migrations/env.py: config.set_main_option("sqlalchemy.url", ...)
    → alembic upgrade head
```

Импорт: `from court_monitor.config.settings import settings`

### 4. CLI init-db / migrate

```
settings.database_url
    → storage/migrations.py: upgrade_head(database_url)
    → alembic.config.Config: cfg.set_main_option("sqlalchemy.url", database_url)
    → command.upgrade(cfg, "head")
```

## Где НЕТ расхождения

- Alembic и приложение используют **один и тот же** `Settings` объект
- `CM_DATABASE_URL` env var имеет приоритет над `.env`
- `alembic.ini` **не содержит** `sqlalchemy.url` — он всегда берётся из settings

## Где МОЖЕТ быть проблема

1. **Кэширование Settings**: `lru_cache` на `get_settings()` — но при каждом запуске процесса создаётся новый объект
2. **`.env` файл**: если существует `.env` с другим URL и env var не экспортирован — используется `.env`
3. **doctor**: не проверяет наличие таблиц и версию миграций

## Рекомендации

1. Добавить `show-config` — показывать текущий database_url и его источник
2. Добавить проверки в `doctor` — таблицы, миграции, repository query
3. Интеграционный тест — end-to-end с временной SQLite
