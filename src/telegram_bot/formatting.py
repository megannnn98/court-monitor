"""Telegram HTML messages. Everything that comes from the news or from a user is escaped."""

from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from telegram_bot.candidates import CandidatesResult
from telegram_bot.message_splitter import split_blocks
from telegram_bot.update_service import UpdateAlreadyRunning, UpdateStarted, UpdateStatus
from web.candidate_rows import _CANDIDATE_CATEGORIES, CandidateRow, _news_day, _surname_first

START = """<b>Court Monitor — кандидаты</b>

/people — кандидаты за период
/export — кандидаты за период файлом Excel
/update — обновить данные кандидатов
/status — состояние обновления
/help — справка"""

HELP = """<b>Команды</b>

Кандидат — человек с политической классификацией и подтверждённым отсутствием в \
последнем загруженном перечне Росфинмониторинга: та же выборка, что на странице \
«Кандидаты» сайта, с её настройками по умолчанию.

/people YYYY-MM-DD YYYY-MM-DD [limit] — кандидаты, о которых есть новость за период. \
Обе даты включаются, день новости — по московскому времени. Без дат бот предложит \
выбрать период кнопками.

/export YYYY-MM-DD YYYY-MM-DD — те же кандидаты файлом .xlsx, как «Export to Excel» \
на сайте: все найденные, limit не действует.

/update — обновить данные кандидатов: докачать новые публикации и пересчитать \
выборку. Ответ приходит сразу, работа идёт в фоне.

/status — состояние последнего обновления: этап, счётчики и ошибки.

Примеры:
<code>/people 2026-09-01 2026-09-19</code>
<code>/people 2026-09-01 2026-09-19 100</code>
<code>/export 2026-09-01 2026-09-19</code>"""

PEOPLE_FORMAT = """Формат:
<code>/people YYYY-MM-DD YYYY-MM-DD [limit]</code>

Пример:
<code>/people 2026-09-01 2026-09-19</code>"""

EXPORT_FORMAT = """Формат:
<code>/export YYYY-MM-DD YYYY-MM-DD</code>

Пример:
<code>/export 2026-09-01 2026-09-19</code>

В файл попадают все найденные кандидаты, limit не задаётся."""

NO_SNAPSHOT = (
    "Перечень Росфинмониторинга ещё не загружен: без него нельзя подтвердить, что "
    "человека в перечне нет, поэтому кандидатов нет."
)

# A name is shortened so that one block always fits into one message: a link spread
# over two messages would leave an unclosed tag, which Telegram rejects.
MAX_NAME = 150

PERIOD_CANCELLED = "Выбор периода отменён."
PERIOD_EXPIRED = "Кнопка устарела. Отправьте команду ещё раз: /people или /export."

UNKNOWN_COMMAND = "Неизвестная команда. /help — список команд."
NOT_AUTHORIZED = "Доступ закрыт. Обратитесь к администратору бота."
UNEXPECTED_ERROR = "Внутренняя ошибка. Попробуйте позже; подробности записаны в журнал сервера."


def help_message(timezone: ZoneInfo) -> str:
    return HELP.format(timezone=escape(str(timezone)))


def people_format_error(reason: str) -> str:
    return f"{escape(reason.capitalize())}.\n\n{PEOPLE_FORMAT}"


def choose_period(action: str) -> str:
    what = "выгрузки кандидатов" if action == "export" else "кандидатов"
    return (
        f"<b>Период {what}</b>\n"
        "Выберите готовый период или откройте календарь. "
        "Можно и текстом: <code>/people 2026-09-01 2026-09-19</code>."
    )


def export_format_error(reason: str) -> str:
    return f"{escape(reason.capitalize())}.\n\n{EXPORT_FORMAT}"


def export_empty(result: CandidatesResult) -> str:
    if result.snapshot_id is None:
        return NO_SNAPSHOT
    return (
        f"За {result.date_from.isoformat()} — {result.date_to.isoformat()} "
        "кандидатов не найдено, выгружать нечего."
    )


def export_ready(result: CandidatesResult) -> str:
    return "\n".join(
        [
            f"<b>Кандидаты {result.date_from.isoformat()} — {result.date_to.isoformat()}</b>",
            f"Кандидатов в файле: {result.total}",
        ]
    )


def export_filename(result: CandidatesResult) -> str:
    return f"candidates-{result.date_from.isoformat()}-{result.date_to.isoformat()}.xlsx"


def update_started(started: UpdateStarted) -> str:
    sources = escape(", ".join(started.sources)) if started.sources else "все"
    return (
        "Обновление запущено.\n\n"
        f"Run: #{started.run.id}\n"
        f"Источники: {sources}\n"
        "Режим: incremental\n\n"
        "Проверить состояние: /status"
    )


def update_already_running(already: UpdateAlreadyRunning) -> str:
    started_at = already.run.started_at or already.run.created_at
    return (
        "Обновление уже выполняется.\n\n"
        f"Run: #{already.run.id}\n"
        f"Статус: {escape(already.run.status.value)}\n"
        f"Запущено: {started_at.astimezone().strftime('%H:%M')}"
    )


def update_status(status: UpdateStatus | None, timezone: ZoneInfo) -> str:
    if status is None:
        return "Обновление ещё ни разу не запускалось. /update — запустить."
    run = status.run
    lines = [
        f"<b>Run #{run.id}</b>",
        f"Статус: {escape(run.status.value)}",
        f"Начало: {_moment(run.started_at or run.created_at, timezone)}",
        f"Окончание: {_moment(run.finished_at, timezone)}",
        "",
        f"Источников обработано: {status.sources_done} из {status.sources_total}"
        + (f" · сейчас: {escape(status.current_source)}" if status.current_source else ""),
        f"Общая обработка: {'готово' if status.derived_done else 'ожидает'}",
        f"Обнаружено публикаций: {status.documents_discovered}",
        f"Загружено публикаций: {status.documents_ingested}",
        f"Обработано статей: {status.articles_extracted}",
        f"Извлечено событий: {status.events_created}",
        f"Создано людей: {status.persons_created}",
        f"Связано упоминаний: {status.persons_linked}",
        f"Отправлено на review: {status.reviews_created}",
        f"Ошибок: {status.error_count}",
    ]
    for failure in status.failures:
        kind = "повторяемая" if failure.kind.value == "retryable" else "постоянная"
        lines.append(
            f"• {escape(failure.stage)}: {failure.count} × {escape(failure.message)} ({kind})"
        )
    return "\n".join(lines)


def people_messages(result: CandidatesResult, limit: int) -> list[str]:
    """The candidates of the period in the page's order, split into accepted messages."""
    if result.snapshot_id is None:
        return [NO_SNAPSHOT]
    shown = result.rows[:limit]
    header = (
        f"<b>Кандидаты {result.date_from.isoformat()} — {result.date_to.isoformat()}</b>\n"
        f"Найдено всего: {result.total}\n"
        f"Показано: {len(shown)}"
    )
    if not shown:
        return [f"{header}\n\nЗа этот период кандидатов не найдено."]
    blocks = [header]
    blocks += [_candidate_block(position, row) for position, row in enumerate(shown, start=1)]
    return split_blocks(blocks)


def _candidate_block(position: int, row: CandidateRow) -> str:
    candidate, news = row
    name = escape(_shorten(_surname_first(candidate.canonical_name), MAX_NAME))
    lines = [f"{position}. <b>{name}</b>"]
    if news is not None:
        details = []
        if news.published_at is not None:
            details.append(_news_day(news.published_at).strftime("%d.%m.%Y"))
        if news.event_type:
            details.append(_CANDIDATE_CATEGORIES.get(news.event_type, news.event_type))
        if details:
            lines.append(f"   {escape(' · '.join(details))}")
        if news.url.startswith(("http://", "https://")):
            lines.append(f'   <a href="{escape(news.url, quote=True)}">Новость</a>')
    return "\n".join(lines)


def _shorten(text: str, limit: int) -> str:
    """Cut at a word when the text is longer than `limit`."""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    space = head.rfind(" ")
    return f"{head[:space] if space > limit // 2 else head}…"


def _moment(value: datetime | None, timezone: ZoneInfo) -> str:
    return "—" if value is None else value.astimezone(timezone).strftime("%Y-%m-%d %H:%M")
