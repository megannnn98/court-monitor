"""«Обзор», the home page: what is new — the political cases' new cases and sentences, the
unnamed figurants to identify — and what waits for the operator. The pipeline, its runs
and the sources' errors are on «Управление»."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from analysis import read as analysis_read
from analysis.politics.classification import POLITICAL
from analysis.read import Figurant
from analysis.unnamed.matching import Candidates
from processing import read as identity
from processing.evidence.publications import latest_first

LATEST = 8

UNNAMED = 5

QUOTE_LIMIT = 160

# The unnamed nobody has identified or closed yet.
_CLOSED = ("rf_entry", "existing_person", "supplied_name", "insufficient")


def _day(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d.%m") if moment else "—"


def latest_news(db: Session, kind: str) -> list[tuple[str, str, datetime | None, str]]:
    """A political case's latest news of one kind: (key, name, published_at, reason), the
    latest first (unknown dates last), then by key."""
    news = analysis_read.news_of_verdict(db, verdict=POLITICAL, kind=kind)
    rank = {
        group_id: place
        for place, group_id in enumerate(identity.by_key(db, [n.group_id for n in news]))
    }
    latest = sorted(
        news,
        key=lambda item: (
            *latest_first(item.published_at),
            rank.get(item.group_id, len(rank)),
        ),
    )[:LATEST]
    groups = identity.groups(db, [item.group_id for item in latest])
    return [
        (groups[item.group_id].key, groups[item.group_id].name, item.published_at, item.reason)
        for item in latest
        if item.group_id in groups
    ]


def news_counts(db: Session) -> dict[str, int]:
    return analysis_read.news_counts_of_verdict(db, verdict=POLITICAL)


def open_unnamed(db: Session) -> list[Figurant]:
    """The latest unnamed nobody has identified or closed yet."""
    return analysis_read.latest_open_figurants(db, closed=_CLOSED, limit=UNNAMED)


def short_quote(quote_text: str) -> str:
    text_ = " ".join(quote_text.split())
    return text_[:QUOTE_LIMIT].rstrip() + "…" if len(text_) > QUOTE_LIMIT else text_


def found_text(found: Candidates, *, age_told: bool = True) -> str:
    return _found(found, age_told=age_told)


def _found(found: Candidates, *, age_told: bool = True) -> str:
    """How many of the list may be this person — the ones worth looking at."""
    if not age_told:
        return "возраст не назван — искать в перечне не по чему"
    if found.likely:
        return f"вероятных в перечне: {found.likely}"
    if found.total:
        return f"того возраста в перечне {found.total} — примет мало"
    return "в перечне никого"
