"""The analysis's explicit testing API: reads of its tables for the evaluation and tests.

Not for production code (`tests/test_data_ownership.py` holds every production module
to it): what is here samples or measures the analysis's storage itself (ADR 0031,
«Evaluation»).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from analysis.persecution.queries import latest_persecution_classification_ids
from analysis.storage.models import PersecutionClassificationRecord, RosfinMatchRecord

_GROUP_FACTS = text(
    """
    SELECT 'politics' AS fact, p.verdict AS value FROM entity_group_politics p
      WHERE p.group_id = :id
    UNION ALL
    SELECT 'latest_news', n.kind FROM entity_group_news n WHERE n.group_id = :id
    UNION ALL
    SELECT 'rf_list', string_agg(DISTINCT m.level, ', ' ORDER BY m.level)
      FROM entity_group_rf_matches m WHERE m.group_id = :id HAVING count(*) > 0
    """
)
_UNNAMED_ARTICLES = text(
    """
    SELECT DISTINCT ON (u.article_id) u.article_id, ir.resolution
    FROM unnamed_figurants u
    LEFT JOIN unnamed_identity_resolutions ir ON ir.figurant_key = u.key
    ORDER BY u.article_id, ir.resolution NULLS LAST
    LIMIT :limit
    """
)

# The tables a disposable test or evaluation database is wiped of between runs
# (`db.maintenance.truncate_disposable_tables`); a cache of model answers worth keeping
# is not among them.
DISPOSABLE_TABLES = (
    "entity_news_answers",
    "entity_group_news",
    "unnamed_identity_resolutions",
    "unnamed_decisions",
    "unnamed_answers",
    "unnamed_figurants",
    "unnamed_screened",
    "unnamed_keeps",
    "airtable_known_persons",
    "criminal_articles",
    "entity_politics_decisions",
    "entity_politics_answers",
    "entity_group_politics",
    "entity_group_rf_matches",
    "rosfin_matches",
    "rfm_operator_entries",
    "rosfinmonitoring_entries",
    "rosfinmonitoring_snapshots",
    "persecution_classifications",
)


def group_facts(session: Session, group_id: int) -> list[tuple[str, str | None]]:
    """(fact, value) of a group: its politics verdict, its latest news, its list levels."""
    return [(fact, value) for fact, value in session.execute(_GROUP_FACTS, {"id": group_id})]


def unnamed_articles(session: Session, *, limit: int) -> list[tuple[int, str | None]]:
    """The first `limit` articles with an unnamed figurant, by id, each with a resolution
    of one of its figurants (None: none resolved)."""
    return [
        (article_id, resolution)
        for article_id, resolution in session.execute(_UNNAMED_ARTICLES, {"limit": limit})
    ]


def model_verdicts(session: Session) -> list[tuple[int, str]]:
    """(group, verdict) of the politics verdicts a model gave (step 5)."""
    return [
        (group_id, verdict)
        for group_id, verdict in session.execute(
            text("SELECT group_id, verdict FROM entity_group_politics WHERE method = 'model'")
        )
    ]


def unsettled_count(
    session: Session,
    *,
    persecution_statuses: Sequence[str],
    snapshot_id: int | None,
    match_statuses: Sequence[str],
) -> int:
    """The latest persecution classifications of these statuses, and the list matches of
    the snapshot of those: what is left for a person to settle (ER v2)."""
    classifications = select(func.count(PersecutionClassificationRecord.id)).where(
        PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()),
        PersecutionClassificationRecord.status.in_(list(persecution_statuses)),
    )
    unsettled = session.scalar(classifications) or 0
    if snapshot_id is not None:
        unsettled += (
            session.scalar(
                select(func.count(RosfinMatchRecord.id)).where(
                    RosfinMatchRecord.snapshot_id == snapshot_id,
                    RosfinMatchRecord.status.in_(list(match_statuses)),
                )
            )
            or 0
        )
    return int(unsettled)


def add_classification(
    session: Session,
    person_id: int,
    *,
    status: str,
    confidence: float,
    reasons: list[str],
    evidence_types: list[str],
    classifier: str,
    at: datetime,
) -> None:
    """An ER v2 person's persecution classification by the named classifier, version 1."""
    session.add(
        PersecutionClassificationRecord(
            person_id=person_id,
            status=status,
            confidence=confidence,
            reasons=reasons,
            evidence_types=evidence_types,
            classifier_name=classifier,
            classifier_version="1",
            classified_at=at,
        )
    )


def latest_classification_statuses(session: Session) -> dict[int, str]:
    """Per ER v2 person, the status of its latest persecution classification."""
    return dict(
        session.execute(
            select(
                PersecutionClassificationRecord.person_id, PersecutionClassificationRecord.status
            ).where(PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()))
        )
        .tuples()
        .all()
    )


def match_statuses(session: Session, snapshot_id: int) -> dict[int, str]:
    """Per ER v2 person, the status of its match with the snapshot."""
    return dict(
        session.execute(
            select(RosfinMatchRecord.person_id, RosfinMatchRecord.status).where(
                RosfinMatchRecord.snapshot_id == snapshot_id
            )
        )
        .tuples()
        .all()
    )
