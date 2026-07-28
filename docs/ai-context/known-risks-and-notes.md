# Известные риски и заметки

## Текущий статус

- MVP-каркас + вертикальный срез (Etap 0–3). Источники работают на сохранённых fixtures.
- Live-доступ и запись в Airtable отключены до подтверждения маппинга.
- LLM отключена (`CM_LLM_MODE=disabled`); regex-экстракция детерминирована.
- Telegram: `process_registry_source` + `TelegramChannelAdapter` полностью подключены к pipeline (fetch fixture/`--live` → ingest → parse → extract). Ранее `parse_and_extract` безусловно прогонял контент через sudrf-специфичный `parse_press_release`, из-за чего в текст для экстракции подмешивался Telegram UI-мусор (имя канала, "VIEW IN TELEGRAM", плейсхолдеры медиа) — исправлено: для source_type != sudrf используется уже очищенный `doc.text`, sudrf-парсер вызывается только для sudrf-документов. Regression-тесты: `tests/integration/test_telegram_pipeline.py`.
- Etap 4, срез 1: добавлены `ReviewItem` (generic очередь проверки оператора) и `AuditLog` (append-only аудит решений) — миграции `0006_review_items`, `0007_audit_log`. `ReviewItem` создаётся при `parser_status=parser_failed`; `AuditLog` пишется из `update_match_status` (confirm/reject-match) и `resolve_review_item`. CLI: `list-review-items`, `resolve-review-item --dismiss`, `--operator` флаг у `confirm-match`/`reject-match`/`resolve-review-item`. `Person`/`Case`/`CourtEvent` намеренно не введены в этом срезе — см. `technical-debt.md` D-001.
- D-011 (закрыт): `SudrfAdapter`/`TelegramChannelAdapter` теперь возвращают `Iterator[FetchResult | FetchProblem]` вместо молчаливого `continue`/`return None` при блокировке/ошибке/таймауте. `process_source`/`process_registry_source` на `FetchProblem` создают `ReviewItem(item_type="source_blocked")` через `repo.upsert_review_item`, дедуп по `source_id` (не по `document_id`, которого для source-level проблемы нет).

## Ограничения

- **Airtable** — `read_only` режим; PAT не предоставлен. Shared-view читается из fixture.
- **Live HTTP** — тестирование на удалённых судах недоступно из тестового окружения (404/блокировки).
- **Экстракция ФИО** — эвристика, не полный морфологический анализ. Возможны пропуски и ложные срабатывания. Обнаружено на реальных live-данных (`run-all --live` по всем Telegram-каналам): юридический ALL-CAPS дисклеймер про «иностранного агента» и аббревиатуры (ООО, СБУ, «РБК-Украина», «ЛГБТ-активистки») массово ловились как ФИО — исправлено в `extraction/names.py::_is_boilerplate_caps`: отклоняется любой токен (или дефисная часть токена) с более чем одной ALL-CAPS буквой, поскольку настоящие ФИО всегда Title Case, а инициалы («И.») — однобуквенные и под фильтр не попадают. **Остаточная (не исправленная) проблема того же класса** — склейка топонима/институциональной фразы в родительном падеже с настоящим именем («Владивостока Татьяны Намазбаевой», «Армении Николом Пашиняном», «Житель Ингушетии Ибрагим») — требует геозетеера/морфоанализа (Etap 6), не эвристики стоп-слов.
- **Нормализация дат рождения** — year-only precision не fabrication month/day, но сравнение less precise.
- **CAPTCHA/блокировки** — не обходятся; при `FetchHealth.blocked`/`http_error`/`timeout` создаётся `ReviewItem(item_type="source_blocked")` (D-011, закрыт). `304 Not Modified` и отсутствие локальной fixture (dev/test) ReviewItem не создают — это не сбой источника.
- **SSL** — не отключается; оператор скачивает файлы вручную при необходимости.
- **Birthplace-скоринг мёртв на практике** — `_extract_place_from_fact` в `matching/candidates.py` всегда возвращает `None`, так что `_score_birthplace` (протестирован юнит-тестами напрямую) не участвует в реальных кандидатах, пока не появится реальный источник места рождения из документа.
- **`birth_date_conflict` почти недостижим** — в `matching/score.py::_score_birth_date` ветка "тот же год, разная полная дата" переоценена комментарием: при совпадающем годе всегда срабатывает более ранняя проверка `birth_year_match`, независимо от дня/месяца. Реальный конфликт по дате возможен только когда дата записи нераспознаваема. Задокументировано тестами (`test_birth_year_match_currently_masks_day_month_difference`, `test_birth_date_conflict_on_unparseable_record_date`), поведение не менялось — scoring-логика ревьюится отдельно.

## Технический долг

- ~~Дублирование логики `doctor` в `cli/app.py`~~ — исправлено (двойной блок problems/settings_source удалён).
- ~~`process_registry_source` содержит `print("already_exists")`~~ — заменено на structured log (`pipeline.registry_source.already_exists`).
- Тесты matching/ и fedsfm расширены edge-case покрытием (birthplace scoring, birth date parsing edge cases, DBF/ZIP format detection, CSV skip-without-FIO, dedup_key).
