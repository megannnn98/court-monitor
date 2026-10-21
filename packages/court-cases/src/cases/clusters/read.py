"""The clusters of publications as the API and the console read them (ADR 0031): values,
never ORM records.

A cluster is one event told by several publications. The anchor is the event it was
created for; a member that joined it carries the reasons of the join and the clusters it
was not joined to. An event that joined nothing and has candidates only (an article
without a name) is not a cluster: it is read through its membership.

One publication may be in two clusters (its article tells two steps: a charge and the
opening of a case): `clusters_of_article` returns both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from cases.storage.models import EventClusterMemberRecord, EventClusterRecord
from ingestion import read as ingestion_read


@dataclass(frozen=True)
class ClusterMemberView:
    """One event in a cluster, and why it is there."""

    evidence_key: str
    event_id: int | None
    article_id: int
    event_type: str
    event_date: date | None
    # core (the anchor) | member | candidate.
    role: str
    # candidate | confirmed | rejected.
    status: str
    score: float
    reasons: tuple[str, ...]
    # The clusters it matched but was not joined to: id, score, features, vetoes, support.
    candidates: tuple[dict[str, Any], ...]
    algorithm_version: str


@dataclass(frozen=True)
class ClusterSource:
    article_id: int
    title: str
    url: str
    published_at: datetime | None
    source_name: str


@dataclass(frozen=True)
class ClusterSummary:
    id: UUID
    core_evidence_key: str
    event_type: str
    event_date: date | None
    status: str
    score: float
    members: int
    articles: int
    candidates: int
    algorithm_version: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ClusterDetail:
    summary: ClusterSummary
    members: tuple[ClusterMemberView, ...]
    sources: tuple[ClusterSource, ...]


def _summaries(session: Session, query: Select[Any]) -> list[ClusterSummary]:
    return [
        ClusterSummary(
            id=cluster.id,
            core_evidence_key=cluster.core_evidence_key,
            event_type=cluster.event_type,
            event_date=cluster.event_date,
            status=cluster.status,
            score=cluster.score,
            members=members,
            articles=articles,
            candidates=candidates,
            algorithm_version=cluster.algorithm_version,
            created_at=cluster.created_at,
            updated_at=cluster.updated_at,
        )
        for cluster, members, articles, candidates in session.execute(query)
    ]


def _listing() -> Select[Any]:
    """Clusters with their members counted, newest first."""
    return (
        select(
            EventClusterRecord,
            func.count(EventClusterMemberRecord.evidence_key),
            func.count(func.distinct(EventClusterMemberRecord.article_id)),
            func.jsonb_array_length(EventClusterRecord.candidates),
        )
        .join(
            EventClusterMemberRecord, EventClusterMemberRecord.cluster_id == EventClusterRecord.id
        )
        .where(EventClusterRecord.active.is_(True), EventClusterMemberRecord.active.is_(True))
        .group_by(EventClusterRecord.id)
        .order_by(EventClusterRecord.created_at.desc(), EventClusterRecord.id)
    )


def list_clusters(
    session: Session,
    *,
    limit: int,
    offset: int = 0,
    person_key: str | None = None,
    article_id: int | None = None,
    status: str | None = None,
) -> tuple[int, list[ClusterSummary]]:
    """(the number of clusters, one page of them)."""
    query = _listing()
    if person_key is not None:
        query = query.where(EventClusterRecord.targets.contains([person_key]))
    if article_id is not None:
        ids = select(EventClusterMemberRecord.cluster_id).where(
            EventClusterMemberRecord.article_id == article_id,
            EventClusterMemberRecord.active.is_(True),
        )
        query = query.where(EventClusterRecord.id.in_(ids))
    if status is not None:
        query = query.where(EventClusterRecord.status == status)
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    return total, _summaries(session, query.limit(limit).offset(offset))


def clusters_of_article(session: Session, article_id: int) -> list[ClusterSummary]:
    """The clusters the publication is in, newest first.

    A publication that tells two steps (a charge and the opening of a case) is in two
    clusters, and both are returned.
    """
    _, found = list_clusters(session, limit=200, article_id=article_id)
    return found


def cluster_detail(session: Session, cluster_id: UUID) -> ClusterDetail | None:
    found = _summaries(session, _listing().where(EventClusterRecord.id == cluster_id))
    if not found:
        return None
    members = session.scalars(
        select(EventClusterMemberRecord)
        .where(
            EventClusterMemberRecord.cluster_id == cluster_id,
            EventClusterMemberRecord.active.is_(True),
        )
        .order_by(EventClusterMemberRecord.role, EventClusterMemberRecord.evidence_key)
    ).all()
    views = tuple(
        ClusterMemberView(
            evidence_key=member.evidence_key,
            event_id=member.event_id,
            article_id=member.article_id,
            event_type=member.evidence.get("event_type", ""),
            event_date=date.fromisoformat(member.evidence["event_date"])
            if member.evidence.get("event_date")
            else None,
            role=member.role,
            status=member.status,
            score=member.score,
            reasons=tuple(member.reasons),
            candidates=tuple(member.candidates),
            algorithm_version=member.algorithm_version,
        )
        for member in members
    )
    cards = ingestion_read.article_cards(session, {view.article_id for view in views})
    sources = tuple(
        ClusterSource(card.id, card.title, card.url, card.published_at, card.source_name)
        for card in sorted(cards.values(), key=lambda card: card.id)
    )
    return ClusterDetail(found[0], views, sources)
