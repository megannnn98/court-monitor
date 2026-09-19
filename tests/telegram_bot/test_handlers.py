"""Handlers with fake services: answers, authorization, errors — no Telegram, no database."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import pytest

from monitoring.models import FailureKind
from operator_console import (
    OPERATION_DEFINITIONS,
    OperationParameters,
    OperationRun,
    OperationRunStatus,
)
from telegram_bot import formatting
from telegram_bot.app import _parse
from telegram_bot.authorization import Authorization
from telegram_bot.config import TelegramBotSettings
from telegram_bot.handlers import CommandHandlers
from telegram_bot.models import NewsArticleReference, PeopleFromNewsResult, PersonFromNews
from telegram_bot.people_service import PeopleQuery, parse_people_query
from telegram_bot.update_service import (
    RunFailures,
    UpdateAlreadyRunning,
    UpdateStarted,
    UpdateStatus,
)

ALLOWED = 42
DENIED = 4242
SETTINGS = TelegramBotSettings.from_env(
    {"TELEGRAM_BOT_TOKEN": "123:secret", "TELEGRAM_ALLOWED_USER_IDS": str(ALLOWED)}
)
RUN = OperationRun(
    id=42,
    operation=OPERATION_DEFINITIONS["monitor"],
    parameters=OperationParameters(limit=50),
    status=OperationRunStatus.RUNNING,
    created_at=datetime(2026, 9, 19, 14, 32, tzinfo=UTC),
    started_at=datetime(2026, 9, 19, 14, 32, tzinfo=UTC),
)
EMPTY_RESULT = PeopleFromNewsResult(
    date_from=datetime(2026, 9, 1, tzinfo=UTC).date(),
    date_to=datetime(2026, 9, 19, tzinfo=UTC).date(),
    total=0,
    limit=50,
    people=[],
)


class FakeUpdates:
    def __init__(
        self,
        outcome: UpdateStarted | UpdateAlreadyRunning | None = None,
        status: UpdateStatus | None = None,
    ) -> None:
        self.outcome = outcome or UpdateStarted(run=RUN, sources=("ovd-info", "sota-vision"))
        self.status = status
        self.started = 0

    def start_update(self) -> UpdateStarted | UpdateAlreadyRunning:
        self.started += 1
        return self.outcome

    def last_update(self) -> UpdateStatus | None:
        return self.status


class FakePeople:
    def __init__(self, result: PeopleFromNewsResult = EMPTY_RESULT) -> None:
        self.result = result
        self.queries: list[PeopleQuery] = []

    def parse(self, arguments: Sequence[str]) -> PeopleQuery:
        return parse_people_query(
            arguments,
            timezone=SETTINGS.timezone,
            default_limit=SETTINGS.people_default_limit,
            max_limit=SETTINGS.people_max_limit,
        )

    def people(self, query: PeopleQuery) -> PeopleFromNewsResult:
        self.queries.append(query)
        return self.result


def handlers(
    *, updates: FakeUpdates | None = None, people: FakePeople | None = None
) -> CommandHandlers:
    async def run_here[T](work: Callable[[], T]) -> T:
        # The "blocking" call runs inline: no test depends on thread timing.
        return work()

    return CommandHandlers(
        settings=SETTINGS,
        authorization=Authorization(SETTINGS),
        people=people or FakePeople(),  # type: ignore[arg-type]
        updates=updates or FakeUpdates(),  # type: ignore[arg-type]
        run_blocking=run_here,
    )


def reply(
    command: str,
    *arguments: str,
    user_id: int | None = ALLOWED,
    updates: FakeUpdates | None = None,
    people: FakePeople | None = None,
) -> list[str]:
    bot = handlers(updates=updates, people=people)
    return asyncio.run(bot.handle(command, list(arguments), user_id)).messages


def test_start_lists_the_commands() -> None:
    messages = reply("start")

    assert messages == [formatting.START]
    assert "/people" in messages[0]


def test_help_names_the_timezone() -> None:
    message = reply("help")[0]

    assert "Europe/Moscow" in message
    assert "YYYY-MM-DD" in message


def test_update_answers_with_the_run_id() -> None:
    updates = FakeUpdates()

    message = reply("update", updates=updates)[0]

    assert "Обновление запущено" in message
    assert "Run: #42" in message
    assert updates.started == 1


def test_update_reports_a_run_already_going_on() -> None:
    updates = FakeUpdates(outcome=UpdateAlreadyRunning(run=RUN))

    message = reply("update", updates=updates)[0]

    assert "Обновление уже выполняется" in message
    assert "Run: #42" in message


def test_status_without_any_run() -> None:
    assert "ещё ни разу не запускалось" in reply("status")[0]


def test_status_shows_counters_and_failures() -> None:
    status = UpdateStatus(
        run=RUN,
        sources_done=2,
        sources_total=2,
        current_source=None,
        derived_done=True,
        documents_discovered=34,
        documents_ingested=12,
        articles_extracted=12,
        events_created=27,
        persons_created=8,
        persons_linked=5,
        reviews_created=3,
        error_count=1,
        failures=(
            RunFailures(
                stage="ingestion", count=1, kind=FailureKind.RETRYABLE, message="ReadTimeout"
            ),
        ),
    )

    message = reply("status", updates=FakeUpdates(status=status))[0]

    assert "Run #42" in message
    assert "Обнаружено публикаций: 34" in message
    assert "Ошибок: 1" in message
    assert "ingestion: 1 × ReadTimeout (повторяемая)" in message


def test_people_reports_an_empty_period() -> None:
    message = reply("people", "2026-09-01", "2026-09-19")[0]

    assert "Найдено всего: 0" in message
    assert "не найдено" in message


def test_people_formats_a_person_with_articles() -> None:
    result = PeopleFromNewsResult(
        date_from=datetime(2026, 9, 1, tzinfo=UTC).date(),
        date_to=datetime(2026, 9, 19, tzinfo=UTC).date(),
        total=387,
        limit=50,
        people=[
            PersonFromNews(
                person_id=1,
                canonical_name="Иванов Иван <Иванович>",
                article_count=3,
                latest_published_at=datetime(2026, 9, 18, 10, tzinfo=UTC),
                sources=["ОВД-Инфо", "SOTA"],
                articles=[
                    NewsArticleReference(
                        article_id=7,
                        title="Суд & приговор",
                        url="https://news.example/7?a=1&b=2",
                        source_name="ОВД-Инфо",
                        published_at=datetime(2026, 9, 18, 10, tzinfo=UTC),
                    )
                ],
            )
        ],
    )

    message = reply("people", "2026-09-01", "2026-09-19", people=FakePeople(result))[0]

    assert "Найдено всего: 387" in message and "Показано: 1" in message
    # News text is escaped; the link stays a link.
    assert "Иванов Иван &lt;Иванович&gt;" in message
    assert "Суд &amp; приговор" in message
    assert '<a href="https://news.example/7?a=1&amp;b=2">' in message


def test_people_passes_the_parsed_period_to_the_service() -> None:
    people = FakePeople()

    reply("people", "2026-09-01", "2026-09-19", "100", people=people)

    assert people.queries[0].limit == 100
    assert people.queries[0].date_from.isoformat() == "2026-09-01"


@pytest.mark.parametrize(
    "arguments",
    [
        (),
        ("2026-09-01",),
        ("01.09.2026", "19.09.2026"),
        ("2026-99-99", "2026-09-19"),
        ("2026-09-20", "2026-09-01"),
        ("2026-09-01", "2026-09-19", "abc"),
    ],
)
def test_people_explains_the_format_on_a_bad_argument(arguments: tuple[str, ...]) -> None:
    message = reply("people", *arguments)[0]

    assert "/people YYYY-MM-DD YYYY-MM-DD [limit]" in message
    assert "Traceback" not in message


def test_an_unknown_command_is_answered_shortly() -> None:
    assert reply("dance") == [formatting.UNKNOWN_COMMAND]


def test_an_unauthorized_user_gets_nothing_done() -> None:
    updates = FakeUpdates()

    assert reply("update", user_id=DENIED, updates=updates) == [formatting.NOT_AUTHORIZED]
    assert updates.started == 0


def test_a_message_without_a_user_is_denied() -> None:
    assert reply("people", "2026-09-01", "2026-09-19", user_id=None) == [formatting.NOT_AUTHORIZED]


def test_an_unexpected_error_never_reaches_the_user(caplog: pytest.LogCaptureFixture) -> None:
    class Exploding(FakeUpdates):
        def start_update(self) -> UpdateStarted | UpdateAlreadyRunning:
            raise RuntimeError("postgresql://user:password@host/db is unreachable")

    with caplog.at_level(logging.ERROR, logger="telegram_bot.handlers"):
        messages = reply("update", updates=Exploding())

    assert messages == [formatting.UNEXPECTED_ERROR]
    assert "password" not in messages[0]
    # The server log keeps the traceback.
    assert caplog.records and caplog.records[-1].exc_info is not None


def test_a_command_addressed_to_the_bot_is_understood() -> None:
    assert _parse("/people@court_monitor_bot 2026-09-01 2026-09-19") == (
        "people",
        ["2026-09-01", "2026-09-19"],
    )


def test_plain_text_is_not_a_command() -> None:
    assert _parse("привет") == ("", [])


def test_a_very_long_title_stays_inside_one_message_with_its_link() -> None:
    result = PeopleFromNewsResult(
        date_from=datetime(2026, 9, 1, tzinfo=UTC).date(),
        date_to=datetime(2026, 9, 19, tzinfo=UTC).date(),
        total=60,
        limit=50,
        people=[
            PersonFromNews(
                person_id=index,
                canonical_name=f"Человек {index} " + "Длинноимённый" * 30,
                article_count=3,
                latest_published_at=datetime(2026, 9, 18, tzinfo=UTC),
                sources=["ОВД-Инфо"],
                articles=[
                    NewsArticleReference(
                        article_id=index,
                        title="слово " * 900,
                        url=f"https://news.example/{index}",
                        source_name="ОВД-Инфо",
                        published_at=datetime(2026, 9, 18, tzinfo=UTC),
                    )
                ],
            )
            for index in range(50)
        ],
    )

    messages = reply("people", "2026-09-01", "2026-09-19", people=FakePeople(result))

    for message in messages:
        assert len(message) <= 4096
        # Every link and every bold name opened in a part is closed in the same part.
        assert message.count("<a href=") == message.count("</a>")
        assert message.count("<b>") == message.count("</b>")
