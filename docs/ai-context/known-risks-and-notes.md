# Известные риски и заметки

## Текущий статус

- MVP-каркас + вертикальный срез (Etap 0–3). Источники работают на сохранённых fixtures.
- Live-доступ и запись в Airtable отключены до подтверждения маппинга.
- LLM отключена (`CM_LLM_MODE=disabled`); regex-экстракция детерминирована.
- Telegram: `process_registry_source` + `TelegramChannelAdapter` полностью подключены к pipeline (fetch fixture/`--live` → ingest → parse → extract). Ранее `parse_and_extract` безусловно прогонял контент через sudrf-специфичный `parse_press_release`, из-за чего в текст для экстракции подмешивался Telegram UI-мусор (имя канала, "VIEW IN TELEGRAM", плейсхолдеры медиа) — исправлено: для source_type != sudrf используется уже очищенный `doc.text`, sudrf-парсер вызывается только для sudrf-документов. Regression-тесты: `tests/integration/test_telegram_pipeline.py`.

## Ограничения

- **Airtable** — `read_only` режим; PAT не предоставлен. Shared-view читается из fixture.
- **Live HTTP** — тестирование на удалённых судах недоступно из тестового окружения (404/блокировки).
- **Экстракция ФИО** — эвристика, не полный морфологический анализ. Возможны пропуски и ложные срабатывания.
- **Нормализация дат рождения** — year-only precision не fabrication month/day, но сравнение less precise.
- **CAPTCHA/блокировки** — не обходятся; создаётся `ReviewItem` оператору.
- **SSL** — не отключается; оператор скачивает файлы вручную при необходимости.
- **Birthplace-скоринг мёртв на практике** — `_extract_place_from_fact` в `matching/candidates.py` всегда возвращает `None`, так что `_score_birthplace` (протестирован юнит-тестами напрямую) не участвует в реальных кандидатах, пока не появится реальный источник места рождения из документа.
- **`birth_date_conflict` почти недостижим** — в `matching/score.py::_score_birth_date` ветка "тот же год, разная полная дата" переоценена комментарием: при совпадающем годе всегда срабатывает более ранняя проверка `birth_year_match`, независимо от дня/месяца. Реальный конфликт по дате возможен только когда дата записи нераспознаваема. Задокументировано тестами (`test_birth_year_match_currently_masks_day_month_difference`, `test_birth_date_conflict_on_unparseable_record_date`), поведение не менялось — scoring-логика ревьюится отдельно.

## Технический долг

- ~~Дублирование логики `doctor` в `cli/app.py`~~ — исправлено (двойной блок problems/settings_source удалён).
- ~~`process_registry_source` содержит `print("already_exists")`~~ — заменено на structured log (`pipeline.registry_source.already_exists`).
- Тесты matching/ и fedsfm расширены edge-case покрытием (birthplace scoring, birth date parsing edge cases, DBF/ZIP format detection, CSV skip-without-FIO, dedup_key).
