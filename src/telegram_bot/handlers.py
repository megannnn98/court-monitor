"""Telegram command handlers: parse, call a service, answer. No SQL, no pipeline here.

A handler never blocks on the pipeline: `/update` records the run and returns. The
blocking service calls (they talk to PostgreSQL through SQLAlchemy) run in a worker
thread, so the polling loop keeps answering while a query is in flight.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from telegram_bot import formatting
from telegram_bot.authorization import Authorization
from telegram_bot.config import TelegramBotSettings
from telegram_bot.excel import people_filename, people_xlsx
from telegram_bot.people_service import PeopleQuery, PeopleQueryError, PeopleService
from telegram_bot.period_keyboard import (
    EXPORT,
    PEOPLE,
    CallbackError,
    Keyboard,
    calendar_keyboard,
    parse_callback,
    preset_keyboard,
)
from telegram_bot.update_service import UpdateAlreadyRunning, UpdateService

logger = logging.getLogger(__name__)


class BlockingRunner(Protocol):
    """Runs a blocking service call off the event loop (a thread by default)."""

    async def __call__[T](self, work: Callable[[], T]) -> T: ...


@dataclass(frozen=True)
class Document:
    filename: str
    content: bytes


@dataclass(frozen=True)
class Answer:
    """What the bot sends back: messages in order, optionally with a file or buttons.

    `edit` replaces the message the button was pressed on instead of sending a new one.
    """

    messages: list[str]
    document: Document | None = None
    keyboard: Keyboard | None = None
    edit: bool = False

    @classmethod
    def of(cls, message: str) -> Answer:
        return cls(messages=[message])


class CommandHandlers:
    def __init__(
        self,
        *,
        settings: TelegramBotSettings,
        authorization: Authorization,
        people: PeopleService,
        updates: UpdateService,
        run_blocking: BlockingRunner | None = None,
    ) -> None:
        self._settings = settings
        self._authorization = authorization
        self._people = people
        self._updates = updates
        self._run_blocking = run_blocking or _in_thread

    async def handle(self, command: str, arguments: Sequence[str], user_id: int | None) -> Answer:
        """Every command is closed by the allowlist, `/people` included."""
        if not self._authorization.check(command, user_id):
            return Answer.of(formatting.NOT_AUTHORIZED)
        try:
            return await self._dispatch(command, arguments)
        except Exception:
            # The server log keeps the traceback; the user gets a sentence.
            logger.exception(
                "event=telegram_command command=%s telegram_user_id=%s result=error",
                command,
                user_id,
            )
            return Answer.of(formatting.UNEXPECTED_ERROR)

    async def _dispatch(self, command: str, arguments: Sequence[str]) -> Answer:
        if command == "start":
            return Answer.of(formatting.START)
        if command == "help":
            return Answer.of(formatting.help_message(self._settings.timezone))
        if command == "update":
            return await self._update()
        if command == "status":
            return await self._status()
        if command == "people":
            # Without arguments the bot offers the period instead of explaining a format.
            if not arguments:
                return self._ask_period(PEOPLE)
            return await self._people_in_period(arguments)
        if command == "export":
            if not arguments:
                return self._ask_period(EXPORT)
            return await self._export(arguments)
        return Answer.of(formatting.UNKNOWN_COMMAND)

    async def handle_callback(self, data: str, user_id: int | None) -> Answer:
        """A pressed button: the same commands, with the period chosen by tapping."""
        if not self._authorization.check("button", user_id):
            return Answer.of(formatting.NOT_AUTHORIZED)
        try:
            choice = parse_callback(data, self._today())
        except CallbackError:
            logger.warning("event=telegram_callback result=unreadable telegram_user_id=%s", user_id)
            return Answer(messages=[formatting.PERIOD_EXPIRED], edit=True)
        try:
            if choice.noop:
                # A label, not a choice: the message stays exactly as it is.
                return Answer(messages=[])
            if choice.cancelled:
                return Answer(messages=[formatting.PERIOD_CANCELLED], edit=True)
            if choice.period is None:
                return Answer(
                    messages=[formatting.choose_period(choice.action)],
                    keyboard=calendar_keyboard(
                        choice.action,
                        choice.month or self._today().replace(day=1),
                        choice.anchor,
                        self._today(),
                    ),
                    edit=True,
                )
            query = self._people.period_query(choice.period.date_from, choice.period.date_to)
            if choice.action == EXPORT:
                return await self._export_query(query)
            return await self._people_query(query)
        except Exception:
            logger.exception("event=telegram_callback result=error telegram_user_id=%s", user_id)
            return Answer.of(formatting.UNEXPECTED_ERROR)

    def _ask_period(self, action: str) -> Answer:
        return Answer(
            messages=[formatting.choose_period(action)],
            keyboard=preset_keyboard(action, self._today()),
        )

    def _today(self) -> date:
        return datetime.now(tz=self._settings.timezone).date()

    async def _update(self) -> Answer:
        outcome = await self._run_blocking(self._updates.start_update)
        if isinstance(outcome, UpdateAlreadyRunning):
            return Answer.of(formatting.update_already_running(outcome))
        return Answer.of(formatting.update_started(outcome))

    async def _status(self) -> Answer:
        status = await self._run_blocking(self._updates.last_update)
        return Answer.of(formatting.update_status(status, self._settings.timezone))

    async def _export(self, arguments: Sequence[str]) -> Answer:
        try:
            query = self._people.parse_export(arguments)
        except PeopleQueryError as error:
            return Answer.of(formatting.export_format_error(str(error)))
        return await self._export_query(query)

    async def _export_query(self, query: PeopleQuery) -> Answer:
        result = await self._run_blocking(lambda: self._people.export(query))
        logger.info(
            "event=telegram_command command=export result=ok from=%s to=%s people=%d rows=%d",
            query.date_from.isoformat(),
            query.date_to.isoformat(),
            len(result.people),
            sum(max(len(person.articles), 1) for person in result.people),
        )
        if not result.people:
            return Answer.of(formatting.export_empty(result))
        return Answer(
            messages=[formatting.export_ready(result)],
            document=Document(
                filename=people_filename(result),
                content=people_xlsx(result, self._settings.timezone),
            ),
        )

    async def _people_in_period(self, arguments: Sequence[str]) -> Answer:
        try:
            query = self._people.parse(arguments)
        except PeopleQueryError as error:
            return Answer.of(formatting.people_format_error(str(error)))
        return await self._people_query(query)

    async def _people_query(self, query: PeopleQuery) -> Answer:
        result = await self._run_blocking(lambda: self._people.people(query))
        logger.info(
            "event=telegram_command command=people result=ok from=%s to=%s limit=%d found=%d",
            query.date_from.isoformat(),
            query.date_to.isoformat(),
            query.limit,
            result.total,
        )
        return Answer(messages=formatting.people_messages(result))


async def _in_thread[T](work: Callable[[], T]) -> T:
    return await asyncio.to_thread(work)
