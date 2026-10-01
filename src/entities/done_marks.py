"""«Обработано»: the operator's mark on a person of the result, and when it still stands.

A mark remembers the news it was made at. A later news about the person brings them back,
so a mark stands only while nothing newer has been published. The people with a name and
the figurants without one carry their marks by key in the same table, and the same rule
is asked of both.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import EntityDoneMarkRecord

# The news each mark was made at, by the person's key.
Marks = Mapping[str, datetime | None]


def done_marks(db: Session) -> dict[str, datetime | None]:
    """Every mark there is."""
    return {
        key: news_at
        for key, news_at in db.execute(
            select(EntityDoneMarkRecord.key, EntityDoneMarkRecord.news_at)
        ).all()
    }


def is_done(marks: Marks, key: str, latest: datetime | None) -> bool:
    """Marked, and no news about the person later than the one the mark saw."""
    if key not in marks:
        return False
    seen = marks[key]
    return latest is None or (seen is not None and seen >= latest)
