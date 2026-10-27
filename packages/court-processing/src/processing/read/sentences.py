"""Shown and hidden sentence evidence and reading counts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ingestion import read
from processing.storage.models import (
    ArticleSentenceReadingRecord,
    ArticleSentenceRecord,
)


@dataclass(frozen=True)
class HiddenSentence:
    """A sentence removed from all result counts, with its publication title."""

    id: int
    article_id: int
    person: str
    region: str
    kind: str
    months: int
    fine_rub: int
    in_absentia: bool
    sentenced_on: str
    reason: str
    reason_text: str
    quote: str
    title: str


def hidden_sentences(session: Session) -> list[HiddenSentence]:
    """Hidden evidence newest first, enriched by one bulk publication read."""
    rows = session.execute(
        select(
            ArticleSentenceRecord.id,
            ArticleSentenceRecord.article_id,
            ArticleSentenceRecord.person,
            ArticleSentenceRecord.region,
            ArticleSentenceRecord.kind,
            ArticleSentenceRecord.months,
            ArticleSentenceRecord.fine_rub,
            ArticleSentenceRecord.in_absentia,
            ArticleSentenceRecord.sentenced_on,
            ArticleSentenceRecord.reason,
            ArticleSentenceRecord.reason_text,
            ArticleSentenceRecord.quote,
        )
        .where(ArticleSentenceRecord.hidden)
        .order_by(ArticleSentenceRecord.id.desc())
    ).all()
    cards = read.article_cards(session, {row.article_id for row in rows})
    return [
        HiddenSentence(
            id=row.id,
            article_id=row.article_id,
            person=row.person,
            region=row.region,
            kind=row.kind,
            months=row.months,
            fine_rub=row.fine_rub,
            in_absentia=row.in_absentia,
            sentenced_on=row.sentenced_on,
            reason=row.reason,
            reason_text=row.reason_text,
            quote=row.quote,
            title=cards[row.article_id].title,
        )
        for row in rows
        if row.article_id in cards
    ]


@dataclass(frozen=True)
class ShownSentence:
    """A sentence the result counts, with the publication it stands in."""

    id: int
    article_id: int
    person: str
    person_key: str | None
    region: str
    kind: str
    months: int
    fine_rub: int
    in_absentia: bool
    sentenced_on: str
    articles: list[Any]
    reason: str
    reason_text: str
    quote: str
    title: str
    published_at: datetime | None
    source: str


def shown_sentences(session: Session) -> list[ShownSentence]:
    """Every sentence not hidden, in id order, enriched by one bulk publication read."""
    rows = session.execute(
        select(
            ArticleSentenceRecord.id,
            ArticleSentenceRecord.article_id,
            ArticleSentenceRecord.person,
            ArticleSentenceRecord.person_key,
            ArticleSentenceRecord.region,
            ArticleSentenceRecord.kind,
            ArticleSentenceRecord.months,
            ArticleSentenceRecord.fine_rub,
            ArticleSentenceRecord.in_absentia,
            ArticleSentenceRecord.sentenced_on,
            ArticleSentenceRecord.articles,
            ArticleSentenceRecord.reason,
            ArticleSentenceRecord.reason_text,
            ArticleSentenceRecord.quote,
        )
        .where(~ArticleSentenceRecord.hidden)
        .order_by(ArticleSentenceRecord.id)
    ).all()
    cards = read.article_cards(session, {row.article_id for row in rows})
    return [
        ShownSentence(
            id=row.id,
            article_id=row.article_id,
            person=row.person,
            person_key=row.person_key,
            region=row.region,
            kind=row.kind,
            months=row.months,
            fine_rub=row.fine_rub,
            in_absentia=row.in_absentia,
            sentenced_on=row.sentenced_on,
            articles=row.articles,
            reason=row.reason,
            reason_text=row.reason_text,
            quote=row.quote,
            title=cards[row.article_id].title,
            published_at=cards[row.article_id].published_at,
            source=cards[row.article_id].source_name,
        )
        for row in rows
        if row.article_id in cards
    ]


def sentence_reading_count(session: Session) -> int:
    """Publications the sentence-reading step has processed."""
    return session.scalar(select(func.count()).select_from(ArticleSentenceReadingRecord)) or 0
