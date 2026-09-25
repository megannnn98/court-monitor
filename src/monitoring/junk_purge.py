"""Removing the articles without a criminal case, and the persons only they named.

An article is junk when the latest successful extraction of it found no criminal-case
event (a fine alone does not count). It goes with everything extracted from it; its
source document stays as a tombstone with the content wiped, so discovery still knows
the post and never downloads it again. A person left with no mention and no event link
goes too, with what cascades from it (classifications, Rosfinmonitoring matches,
resolution decisions, findings).

An article published before the working date (`since`, `PIPELINE_SINCE`) goes the
same way, whatever it is about; its tombstone is marked `EXPIRED_CONTENT_TYPE`, so that
what is counted as loaded is only what the work covers.

Batches of their own transactions: a stopped purge keeps what it removed and leaves a
consistent database; the next purge goes on from there.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session, sessionmaker

from persons.resolution.service import REVIEW_SUBJECT_TYPE
from semantic_retrieval.models import RetrievalEntityType

logger = logging.getLogger("monitoring")

# The events of a criminal case; `fine` is administrative and does not keep an article.
CRIMINAL_EVENT_TYPES = ("case_opened", "charge", "arrest", "detention", "sentence", "search")
BATCH_SIZE = 500
# The tombstone of an article before the working date.
EXPIRED_CONTENT_TYPE = "application/x-court-monitor-expired"

# Articles whose latest successful extraction found no criminal-case event. An article
# never extracted successfully is not judged.
_JUNK_ARTICLES = text(
    """
    SELECT a.id
    FROM parsed_articles a
    JOIN LATERAL (
        SELECT r.id FROM article_extraction_runs r
        WHERE r.article_id = a.id AND r.status = 'succeeded'
        ORDER BY r.id DESC LIMIT 1
    ) latest ON true
    WHERE NOT EXISTS (
        SELECT 1 FROM extracted_events e
        WHERE e.extraction_run_id = latest.id AND e.event_type IN :criminal
    )
    ORDER BY a.id
    LIMIT :limit
    """
).bindparams(bindparam("criminal", expanding=True))


# Articles published before the working date, news or registry alike.
_OUTDATED_ARTICLES = text(
    """
    SELECT a.id FROM parsed_articles a
    WHERE a.published_at < :since
    ORDER BY a.id
    LIMIT :limit
    """
)


def since_from_env(env: Mapping[str, str] | None = None) -> datetime | None:
    """`PIPELINE_SINCE` (YYYY-MM-DD) as the start of that day, UTC; None when unset."""
    env = os.environ if env is None else env
    value = env.get("PIPELINE_SINCE", "").strip()
    if not value:
        return None
    return datetime.combine(date.fromisoformat(value), time.min, UTC)


@dataclass
class JunkPurgeResult:
    # Every article deleted: before the working date and without a criminal case.
    articles: int = 0
    persons: int = 0
    reviews: int = 0
    # Of `articles`, those before the working date.
    outdated: int = 0


class JunkPurge:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        batch_size: int = BATCH_SIZE,
        on_progress: Callable[[JunkPurgeResult], None] = lambda _result: None,
        since: datetime | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be greater than zero")
        self._since = since
        self._session_factory = session_factory
        self._batch_size = batch_size
        self._on_progress = on_progress

    def count(self) -> int:
        """How many articles go now: before the working date, or junk."""
        with self._session_factory() as session:
            outdated = set(self._outdated_articles(session, limit=None))
            return len(outdated | set(self._junk_articles(session, limit=None)))

    def run(self) -> JunkPurgeResult:
        result = JunkPurgeResult()
        while True:
            with self._session_factory.begin() as session:
                article_ids = self._outdated_articles(session, limit=self._batch_size)
                if not article_ids:
                    break
                persons = self._purge_articles(
                    session, article_ids, content_type=EXPIRED_CONTENT_TYPE
                )
                result.articles += len(article_ids)
                result.outdated += len(article_ids)
                result.persons += self._purge_orphans(session, persons)
            self._on_progress(result)
        while True:
            with self._session_factory.begin() as session:
                article_ids = self._junk_articles(session, limit=self._batch_size)
                if not article_ids:
                    break
                persons = self._purge_articles(session, article_ids)
                result.articles += len(article_ids)
                result.persons += self._purge_orphans(session, persons)
            self._on_progress(result)
        with self._session_factory.begin() as session:
            result.reviews = self._purge_orphan_reviews(session)
        self._on_progress(result)
        return result

    def _outdated_articles(self, session: Session, *, limit: int | None) -> list[int]:
        if self._since is None:
            return []
        return list(session.scalars(_OUTDATED_ARTICLES, {"since": self._since, "limit": limit}))

    def _junk_articles(self, session: Session, *, limit: int | None) -> list[int]:
        return list(
            session.scalars(
                _JUNK_ARTICLES, {"criminal": list(CRIMINAL_EVENT_TYPES), "limit": limit}
            ).all()
        )

    def _purge_articles(
        self, session: Session, article_ids: Sequence[int], *, content_type: str | None = None
    ) -> list[int]:
        """Delete the articles and all extracted from them; the persons they named. The
        tombstone keeps its content type unless one is given."""
        ids = {"ids": list(article_ids)}
        persons = list(
            session.scalars(
                text(
                    """
                    SELECT m.person_id FROM entity_mentions m
                    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
                    WHERE r.article_id IN :ids AND m.person_id IS NOT NULL
                    UNION
                    SELECT l.person_id FROM person_event_links l
                    JOIN extracted_events e ON e.id = l.event_id
                    JOIN article_extraction_runs r ON r.id = e.extraction_run_id
                    WHERE r.article_id IN :ids
                    """
                ).bindparams(bindparam("ids", expanding=True)),
                ids,
            ).all()
        )
        # The semantic index keeps events by id, without a foreign key.
        session.execute(
            text(
                """
                DELETE FROM semantic_documents
                WHERE entity_type = :event AND entity_id IN (
                    SELECT e.id FROM extracted_events e
                    JOIN article_extraction_runs r ON r.id = e.extraction_run_id
                    WHERE r.article_id IN :ids)
                """
            ).bindparams(bindparam("ids", expanding=True)),
            {**ids, "event": RetrievalEntityType.EVENT.value},
        )
        documents = list(
            session.scalars(
                text(
                    "DELETE FROM parsed_articles WHERE id IN :ids RETURNING document_id"
                ).bindparams(bindparam("ids", expanding=True)),
                ids,
            ).all()
        )
        # The tombstone: the post stays known to discovery, its content goes.
        session.execute(
            text(
                "UPDATE source_documents SET raw_content = '', "
                "content_type = coalesce(:content_type, content_type) WHERE id IN :documents"
            ).bindparams(bindparam("documents", expanding=True)),
            {"documents": documents, "content_type": content_type},
        )
        return persons

    def _purge_orphans(self, session: Session, person_ids: Sequence[int]) -> int:
        """Of these persons, delete the ones nothing mentions or links to any more."""
        if not person_ids:
            return 0
        orphans = list(
            session.scalars(
                text(
                    """
                    SELECT p.id FROM persons p
                    WHERE p.id IN :ids
                      AND NOT EXISTS (SELECT 1 FROM entity_mentions m WHERE m.person_id = p.id)
                      AND NOT EXISTS (SELECT 1 FROM person_event_links l WHERE l.person_id = p.id)
                    """
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": list(person_ids)},
            ).all()
        )
        if not orphans:
            return 0
        session.execute(
            text(
                "DELETE FROM semantic_documents WHERE entity_type = :person AND entity_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": orphans, "person": RetrievalEntityType.PERSON.value},
        )
        session.execute(
            text("DELETE FROM persons WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": orphans},
        )
        return len(orphans)

    def _purge_orphan_reviews(self, session: Session) -> int:
        """Reviews of resolution decisions that went with their mentions."""
        deleted = session.execute(
            text(
                """
                DELETE FROM review_records rr
                WHERE rr.subject_type = :subject
                  AND NOT EXISTS (
                      SELECT 1 FROM person_resolution_decisions d WHERE d.id = rr.subject_id)
                """
            ),
            {"subject": REVIEW_SUBJECT_TYPE},
        )
        return int(deleted.rowcount or 0)  # type: ignore[attr-defined]
