"""«Отсев: на проверке»: the articles the junk screen held back from the purge.

Each one: the publication, why it was held (the screen's score and cutoff — no proof of a
case), the events today's extraction found, and a person's two ways out: «Мусор» (the
next purge deletes it) or «Извлечь заново» (after the extraction was fixed; an event found
returns the article to the pipeline). `processing.junk.holds` does the work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ingestion import read as ingestion_read
from processing.junk import hold_view
from processing.junk.hold_reader import MODEL_JUNK, RELEASED, release
from processing.junk.hold_view import MODEL_JUNK_VIEW
from processing.junk.holds import hold_again, mark_junk, reextract, unrelease
from processing.junk.screen import HELD, JUNK
from web.dependencies import session_factory_for

PAGE_SIZE = 30

_STATUSES = {
    HELD: "На проверке",
    MODEL_JUNK_VIEW: "Модель считает мусором",
    RELEASED: "Выпущено в работу",
    JUNK: "Отмечены как мусор",
}

# The same news told twice. Sources copy one another, and reading one story five times is
# the operator's dullest work — so held articles that tell one story are shown together
# and can be dismissed together. Two signs, each on its own enough:
# - they name the same person (first name and surname, as the extraction normalised it);
# - their titles are nearly the same text (trigram similarity, pg_trgm).
# Neither proves it is one story, so nothing is merged or deleted on its own: the cards
# only stand together, and every one keeps its own buttons.
TITLE_SIMILARITY = 0.5


def same_news(ids: list[int], pairs: list[tuple[int, int]]) -> list[list[int]]:
    """The articles in groups of one story, in the order of `ids`.

    A pair joins two articles; pairs chain (A with B, B with C: one story of three). A
    group stands where its first article stood, so the list keeps its order.
    """
    parent = {article: article for article in ids}

    def root(article: int) -> int:
        while parent[article] != article:
            parent[article] = parent[parent[article]]
            article = parent[article]
        return article

    for first, second in pairs:
        if first in parent and second in parent:
            parent[root(second)] = root(first)
    groups: dict[int, list[int]] = {}
    for article in ids:
        groups.setdefault(root(article), []).append(article)
    return list(groups.values())


def _day(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d.%m.%Y") if moment else "—"


# Whose word the note is, when it is a model's.
_NOTE_LABELS = {MODEL_JUNK: "Модель: в работу не брать — ", "case": "Модель: дело — "}


def _pages(stories: list[list[int]]) -> list[list[list[int]]]:
    """The stories in pages of about `PAGE_SIZE` articles. A story is never cut by a page
    break: a page takes whole stories until it is full."""
    pages: list[list[list[int]]] = []
    count = PAGE_SIZE
    for story in stories:
        if count >= PAGE_SIZE:
            pages.append([])
            count = 0
        pages[-1].append(story)
        count += len(story)
    return pages


def junk_counts(db: Session) -> dict[str, int]:
    return hold_view.hold_counts(db)


def default_status(counts: dict[str, int]) -> str:
    """Opened from the queue: the list there is work in, the model's when it is all."""
    return MODEL_JUNK_VIEW if not counts.get(HELD) and counts.get(MODEL_JUNK_VIEW) else HELD


def held_stories(db: Session, status: str) -> tuple[dict[int, Any], list[list[list[int]]]]:
    """The list's rows by article, and its stories in pages; read by this page and the API."""
    rows = hold_view.held_articles(db, status)
    by_id = {row.article_id: row for row in rows}
    pairs = [
        *hold_view.same_person_pairs(db, status),
        *ingestion_read.similar_titles(db, list(by_id), similarity=TITLE_SIMILARITY),
    ]
    return by_id, _pages(same_news(list(by_id), pairs))


def note_label(reader_verdict: str | None) -> str:
    return _NOTE_LABELS.get(reader_verdict or "", "")


def apply_junk(db: Session, article_id: int) -> None:
    """«Мусор» of a held article; commits."""
    if not mark_junk(db, article_id):
        raise HTTPException(status_code=404, detail="Статья не на проверке")
    db.commit()


def apply_unrelease(db: Session, article_id: int) -> None:
    """«Мусор» of a released article: under the purge again; commits."""
    if not unrelease(db, article_id):
        raise HTTPException(status_code=404, detail="Статья не выпущена в работу")
    db.commit()


def apply_release(db: Session, article_id: int) -> None:
    """«Это дело — в работу»; commits."""
    if not release(db, article_id):
        raise HTTPException(status_code=404, detail="Статья не на проверке")
    db.commit()


def apply_junk_all(db: Session, articles: list[int]) -> None:
    """«Мусор» for every article of one story. All of them, or none: one that is no
    longer held means the page is stale, and the operator should look again."""
    if not articles:
        raise HTTPException(status_code=400, detail="Не указаны статьи")
    if not all(mark_junk(db, article_id) for article_id in articles):
        db.rollback()
        raise HTTPException(status_code=404, detail="Статья не на проверке")
    db.commit()


def apply_hold(db: Session, article_id: int) -> None:
    """«Вернуть на проверку» of an article marked junk; commits."""
    if not hold_again(db, article_id):
        raise HTTPException(status_code=404, detail="Статья не отмечена как мусор")
    db.commit()


def apply_reextract(db: Session, article_id: int) -> bool:
    """«Извлечь заново»: whether an event was found and the article went back to work."""
    held = hold_view.hold_status(db, article_id)
    if held != HELD:
        raise HTTPException(status_code=404, detail="Статья не на проверке")
    return reextract(session_factory_for(db), article_id).released
