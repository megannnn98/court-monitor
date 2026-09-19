"""The bot process: aiogram long polling over the application services (ADR 0019).

No webhook, no open port: the process dials out to Telegram and stops on SIGTERM,
which aiogram turns into a clean shutdown of the polling loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message
from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine, create_session_factory
from monitoring.repository import SqlAlchemyMonitoringRepository
from operator_console import OperationRegistry
from settings import ApplicationSettings
from telegram_bot.authorization import Authorization
from telegram_bot.config import TelegramBotSettings
from telegram_bot.handlers import CommandHandlers
from telegram_bot.people_repository import PeopleFromNewsRepository
from telegram_bot.people_service import PeopleService
from telegram_bot.update_service import UpdateService

logger = logging.getLogger(__name__)

COMMANDS = ("start", "help", "update", "status", "people", "export")


def build_handlers(
    session_factory: sessionmaker[Session],
    application: ApplicationSettings,
    settings: TelegramBotSettings,
) -> CommandHandlers:
    return CommandHandlers(
        settings=settings,
        authorization=Authorization(settings),
        people=PeopleService(PeopleFromNewsRepository(session_factory), settings),
        updates=UpdateService(
            OperationRegistry(session_factory),
            SqlAlchemyMonitoringRepository(session_factory),
            enabled_sources=application.monitoring.enabled_sources,
            discovery_limit=application.monitoring.discovery_limit,
        ),
    )


def build_dispatcher(handlers: CommandHandlers) -> Dispatcher:
    dispatcher = Dispatcher()

    async def on_command(message: Message) -> None:
        command, arguments = _parse(message.text or "")
        answer = await handlers.handle(
            command, arguments, message.from_user.id if message.from_user else None
        )
        for part in answer.messages:
            await message.answer(part, disable_web_page_preview=True)
        if answer.document is not None:
            await message.answer_document(
                BufferedInputFile(answer.document.content, filename=answer.document.filename)
            )

    dispatcher.message.register(on_command, Command(commands=COMMANDS))
    # Anything else, including an unknown command, gets the same short reminder.
    dispatcher.message.register(on_command)
    return dispatcher


def _parse(text: str) -> tuple[str, list[str]]:
    """`/people 2026-09-01 2026-09-19` → ("people", ["2026-09-01", "2026-09-19"])."""
    parts = text.split()
    if not parts or not parts[0].startswith("/"):
        return "", []
    # A command may be addressed to the bot in a group: "/people@court_monitor_bot".
    return parts[0][1:].split("@", maxsplit=1)[0].lower(), parts[1:]


async def run(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = TelegramBotSettings.from_env()
    application = ApplicationSettings.from_env()
    logger.info("event=telegram_bot_starting config=%s", settings.redacted())
    engine = create_database_engine(application.database_url, pool=application.database_pool)
    session_factory = create_session_factory(engine)
    bot = Bot(
        token=settings.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = build_dispatcher(build_handlers(session_factory, application, settings))
    try:
        await dispatcher.start_polling(bot, handle_signals=True)
    finally:
        await bot.session.close()
        engine.dispose()
        logger.info("event=telegram_bot_stopped")


def main(argv: Sequence[str] | None = None) -> None:
    asyncio.run(run(argv))
