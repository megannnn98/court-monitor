"""Re-decide the name-only review queue under the current decision rules.

The case-context rule (ADR 0012, amendment 2026-09-17) applies to new decisions. The
queue built before it holds name-only reviews the rule now accepts; this re-decides
them once and links the accepted ones through the reviewer's path, so every link keeps
a reviewed decision and an audit note.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    PersonResolutionDecisionRecord,
)
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.factory import build_person_resolution_engine
from persons.resolution.models import PersonResolutionAction, PersonResolutionReason
from persons.resolution.review import PersonResolutionReviewService, ResolutionReviewAction
from persons.resolution.service import (
    RESOLVER_VERSION,
    PersonResolutionEngine,
    identity_from_mention,
)

logger = logging.getLogger("person_resolution")

REDECIDE_NOTE = "auto: case_context_match (ADR 0012 amendment 2026-09-17)"


@dataclass(frozen=True)
class RedecideSummary:
    checked: int
    linkable: int
    linked: int


def redecide_name_only_reviews(
    session_factory: sessionmaker[Session],
    *,
    apply: bool,
    limit: int | None = None,
    engine: PersonResolutionEngine | None = None,
    reviews: PersonResolutionReviewService | None = None,
) -> RedecideSummary:
    """Re-decide pending name-only reviews; link the accepted ones only with `apply`."""
    engine = engine or build_person_resolution_engine(session_factory)
    reviews = reviews or PersonResolutionReviewService(SqlAlchemyPersonPersistence(session_factory))
    with session_factory() as session:
        decision_ids = session.scalars(
            select(PersonResolutionDecisionRecord.id)
            .where(
                PersonResolutionDecisionRecord.status == "pending_review",
                PersonResolutionDecisionRecord.resolver_version == RESOLVER_VERSION,
                PersonResolutionDecisionRecord.reasons.contains(
                    [PersonResolutionReason.NAME_ONLY_EVIDENCE.value]
                ),
            )
            .order_by(PersonResolutionDecisionRecord.id)
            .limit(limit)
        ).all()

    linkable = linked = 0
    for decision_id in decision_ids:
        with session_factory.begin() as session:
            record = session.get_one(PersonResolutionDecisionRecord, decision_id)
            mention = session.get_one(EntityMentionRecord, record.mention_id)
            article_id = session.scalar(
                select(ArticleExtractionRunRecord.article_id).where(
                    ArticleExtractionRunRecord.id == mention.extraction_run_id
                )
            )
            identity = identity_from_mention(mention, article_id=article_id)
            if identity is None:
                continue
            decision = engine.plan(identity, session).decision
            if decision.action is not PersonResolutionAction.AUTO_LINK:
                continue
            linkable += 1
            if not apply:
                continue
            reviews.apply(
                session,
                decision_id,
                ResolutionReviewAction.LINK_TO_PERSON,
                person_id=decision.selected_person_id,
                note=REDECIDE_NOTE,
            )
            linked += 1
            logger.info(
                "event=er_redecided decision_id=%s person_id=%s",
                decision_id,
                decision.selected_person_id,
            )
    return RedecideSummary(checked=len(decision_ids), linkable=linkable, linked=linked)
