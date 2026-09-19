"""Telegram HTML messages. Everything that comes from the news or from a user is escaped."""

from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from telegram_bot.message_splitter import split_blocks
from telegram_bot.models import PeopleFromNewsResult, PersonFromNews
from telegram_bot.update_service import UpdateAlreadyRunning, UpdateStarted, UpdateStatus

START = """<b>Court Monitor</b>

/update — докачать и обработать новые публикации
/status — показать состояние последней докачки
/people YYYY-MM-DD YYYY-MM-DD — показать людей из новостей за период
/export YYYY-MM-DD YYYY-MM-DD — выгрузить этих людей в Excel
/help — справка"""

HELP = """<b>Команды</b>

/update — запускает инкрементальную докачку: загрузка новых публикаций по всем источникам, \
извлечение людей и событий, разрешение сущностей, классификация, сверка с перечнем РФМ \
и обновление находок. Ответ приходит сразу, работа идёт в фоне.

/status — состояние последней докачки: этап, счётчики и ошибки.

/people YYYY-MM-DD YYYY-MM-DD [limit] — люди из новостей, опубликованных за период. \
Обе даты включаются. Время — {timezone}.

/export YYYY-MM-DD YYYY-MM-DD — та же выборка файлом .xlsx: все найденные люди и \
все их статьи, по строке на статью.

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

В файл попадают все найденные люди и все их статьи, limit не задаётся."""

# A name and a title are shortened so that one line always fits into one message:
# a link spread over two messages would leave an unclosed tag, which Telegram rejects.
MAX_NAME = 150
MAX_TITLE = 300

UNKNOWN_COMMAND = "Неизвестная команда. /help — список команд."
NOT_AUTHORIZED = "Доступ закрыт. Обратитесь к администратору бота."
UNEXPECTED_ERROR = "Внутренняя ошибка. Попробуйте позже; подробности записаны в журнал сервера."


def help_message(timezone: ZoneInfo) -> str:
    return HELP.format(timezone=escape(str(timezone)))


def people_format_error(reason: str) -> str:
    return f"{escape(reason.capitalize())}.\n\n{PEOPLE_FORMAT}"


def export_format_error(reason: str) -> str:
    return f"{escape(reason.capitalize())}.\n\n{EXPORT_FORMAT}"


def export_empty(result: PeopleFromNewsResult) -> str:
    return (
        f"За {result.date_from.isoformat()} — {result.date_to.isoformat()} "
        "людей в новостях не найдено, выгружать нечего."
    )


def export_ready(result: PeopleFromNewsResult) -> str:
    rows = sum(max(len(person.articles), 1) for person in result.people)
    lines = [
        f"<b>Выгрузка {result.date_from.isoformat()} — {result.date_to.isoformat()}</b>",
        f"Людей: {result.total}",
        f"Строк в файле: {rows}",
    ]
    if len(result.people) < result.total:
        lines.append(
            f"Файл вмещает не всё: показаны первые {len(result.people)} человек. "
            "Разбейте период на части."
        )
    return "\n".join(lines)


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


def people_messages(result: PeopleFromNewsResult) -> list[str]:
    """The answer, split into messages Telegram accepts, newest people first."""
    header = (
        f"<b>Люди из новостей {result.date_from.isoformat()} — {result.date_to.isoformat()}</b>\n"
        f"Найдено всего: {result.total}\n"
        f"Показано: {len(result.people)}"
    )
    if not result.people:
        return [f"{header}\n\nЗа этот период людей в новостях не найдено."]
    blocks = [header]
    blocks += [
        _person_block(position, person) for position, person in enumerate(result.people, start=1)
    ]
    return split_blocks(blocks)


def _person_block(position: int, person: PersonFromNews) -> str:
    lines = [
        f"{position}. <b>{escape(_shorten(person.canonical_name, MAX_NAME))}</b>",
        f"   Публикаций: {person.article_count}",
        f"   Последняя: {person.latest_published_at.date().isoformat()}",
        f"   Источники: {escape(', '.join(person.sources))}",
    ]
    if person.articles:
        lines.append("   Статьи:")
        lines += [
            f'   • <a href="{escape(article.url, quote=True)}">'
            f"{escape(_shorten(article.title, MAX_TITLE))}</a>"
            for article in person.articles
        ]
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
