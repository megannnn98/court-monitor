"""`/people` and `/export YYYY-MM-DD YYYY-MM-DD`: parse the arguments, fix the period.

Both dates are inclusive. The list and the file are one cohort — the candidates of
`/ui/candidates` for that period; `limit` only shortens the chat message.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram_bot.candidates import CandidatesRepository, CandidatesResult
from telegram_bot.config import TelegramBotSettings

DATE_FORMAT = "%Y-%m-%d"


class PeopleQueryError(ValueError):
    """An argument the user can fix: the handler answers with the expected format."""


@dataclass(frozen=True)
class PeopleQuery:
    date_from: date
    date_to: date
    limit: int
    # Half-open period in UTC, as stored in `parsed_articles.published_at`.
    start: datetime
    end: datetime


def parse_people_query(
    arguments: Sequence[str], *, timezone: ZoneInfo, default_limit: int, max_limit: int
) -> PeopleQuery:
    if len(arguments) not in (2, 3):
        raise PeopleQueryError("нужны две даты и необязательный limit")
    date_from = _parse_date(arguments[0])
    date_to = _parse_date(arguments[1])
    if date_from > date_to:
        raise PeopleQueryError("начало периода позже его конца")
    limit = default_limit if len(arguments) == 2 else _parse_limit(arguments[2], max_limit)
    start = datetime.combine(date_from, datetime.min.time(), tzinfo=timezone)
    # The end date is included: the period ends at the start of the next day.
    end = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=timezone)
    return PeopleQuery(
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        start=start.astimezone(UTC),
        end=end.astimezone(UTC),
    )


def _parse_date(text: str) -> date:
    try:
        # A calendar date, not a moment: the timezone is applied when the period is built.
        # The length check keeps `fromisoformat`'s other spellings ("20260901") out.
        if len(text) != len("YYYY-MM-DD"):
            raise ValueError(text)
        return date.fromisoformat(text)
    except ValueError:
        raise PeopleQueryError(f"дата {text!r} не в формате YYYY-MM-DD") from None


def _parse_limit(text: str, max_limit: int) -> int:
    try:
        limit = int(text)
    except ValueError:
        raise PeopleQueryError(f"limit {text!r} не число") from None
    if not 1 <= limit <= max_limit:
        raise PeopleQueryError(f"limit должен быть от 1 до {max_limit}")
    return limit


class PeopleService:
    def __init__(self, repository: CandidatesRepository, settings: TelegramBotSettings) -> None:
        self._repository = repository
        self._settings = settings

    def parse(self, arguments: Sequence[str]) -> PeopleQuery:
        return parse_people_query(
            arguments,
            timezone=self._settings.timezone,
            default_limit=self._settings.people_default_limit,
            max_limit=self._settings.people_max_limit,
        )

    def parse_export(self, arguments: Sequence[str]) -> PeopleQuery:
        """`/export` takes the period only: a file always holds everything found."""
        if len(arguments) != 2:
            raise PeopleQueryError("нужны две даты")
        return self.parse(arguments)

    def period_query(self, date_from: date, date_to: date) -> PeopleQuery:
        """A period chosen by buttons: already valid, only the UTC bounds are computed."""
        return parse_people_query(
            [date_from.isoformat(), date_to.isoformat()],
            timezone=self._settings.timezone,
            default_limit=self._settings.people_default_limit,
            max_limit=self._settings.people_max_limit,
        )

    def export(self, query: PeopleQuery) -> CandidatesResult:
        return self._repository.candidates(date_from=query.date_from, date_to=query.date_to)

    def people(self, query: PeopleQuery) -> CandidatesResult:
        """The same cohort as `export`; the message shows at most `query.limit` of it."""
        return self._repository.candidates(date_from=query.date_from, date_to=query.date_to)
