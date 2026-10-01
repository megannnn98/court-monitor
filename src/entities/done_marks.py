"""«Обработано»: the operator's mark on a person of the result, and when it still stands.

A mark remembers the news it was made at. A later news about the person brings them back,
so a mark stands only while nothing newer has been published. The named people are asked
in SQL (`is_done`), the unnamed cases — which are no rows of a table — in Python
(`case_done`); both read the same rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from db.orm_models import EntityDoneMarkRecord, EntityGroupRecord
from entities.unnamed_cases import KEY_PREFIX, Case


def stands(seen: datetime | None, latest: datetime | None) -> bool:
    """A mark made at the news `seen` stands while the latest news is not later."""
    return latest is None or (seen is not None and seen >= latest)


def is_done() -> Any:
    """The person carries a mark, and no news later than the one it was made at."""
    return exists().where(
        EntityDoneMarkRecord.key == EntityGroupRecord.key,
        or_(
            EntityGroupRecord.last_published_at.is_(None),
            EntityDoneMarkRecord.news_at >= EntityGroupRecord.last_published_at,
        ),
    )


def done_keys(db: Session, keys: Sequence[str]) -> set[str]:
    """Which of these people are «обработано» now."""
    return set(
        db.scalars(select(EntityGroupRecord.key).where(EntityGroupRecord.key.in_(keys), is_done()))
    )


def unnamed_marks(db: Session) -> dict[str, datetime | None]:
    """The «обработано» marks of the unnamed, by key: the news each was made at."""
    return {
        key: news_at
        for key, news_at in db.execute(
            select(EntityDoneMarkRecord.key, EntityDoneMarkRecord.news_at).where(
                EntityDoneMarkRecord.key.startswith(KEY_PREFIX)
            )
        ).all()
    }


def case_done(case: Case, marks: Mapping[str, datetime | None]) -> bool:
    """Marked, and no sentence about the person later than the mark saw."""
    return case.key in marks and stands(marks[case.key], case.last_published_at)
