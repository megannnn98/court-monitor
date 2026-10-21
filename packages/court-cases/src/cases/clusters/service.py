"""Clusters of publications: group the publications that tell one event.

The one public command is `resolve`. It is safe to repeat: an event that already has a
membership is skipped, an event processing has re-extracted is found again by its
`evidence_key` (the article, the step and the words, none of which a rebuild changes)
and tied back to its cluster, and a cluster id is never given twice. Two runs never
overlap: each batch holds one advisory lock until its transaction ends.

The rules are conservative and explainable (`clusters.matching`): a cluster is one
event of one person, and a publication joins it only when it matches the **anchor** of
the cluster directly — never through a chain of members. Two clusters that match are an
ambiguity: the event opens a cluster of its own, the candidates are recorded, and
nothing is merged.

Articles without a name are never put into a cluster: they only get candidates (the
court, the article, the place, the day, the words of the previous hearing), and their
status is always `candidate`.

The optional e5 signal only ranks the candidates: the vectors are never written to the
database, at most a cache file outside it, and the similarity of the words is never
enough on its own to put two events in one cluster.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from cases.clusters import matching
from cases.clusters.matching import ClusterEvidence, Match
from cases.service import evidence_key
from cases.storage.models import (
    EventClusterMemberRecord,
    EventClusterRecord,
)
from ingestion import fulltext as ingestion_fulltext
from processing import case_events
from processing.case_events import CaseEvent

LOCK_KEY = "event-cluster-resolution"
# Candidates kept on a membership that was not joined to any of them.
MAX_RECORDED_CANDIDATES = 10
# Below this a candidate shares nothing worth recording but the person.
_RECORDED_SCORE = 0.40
# The window the candidates are looked for in, in days.
CANDIDATE_WINDOW_DAYS = 365
_SPACES = re.compile(r"\s+")
# The words of the quote the candidate lookup looks for in the text.
_QUERY_WORDS = 3
_MIN_WORD_LENGTH = 5

# The clusters that may hold the event of a person: the same person, the same step, the
# same time window, and the same words in the text.
_CANDIDATE_CLUSTERS = text(
    """
    SELECT c.id, c.core_evidence_key, c.core_article_id, c.evidence
    FROM event_clusters c
    WHERE c.active AND c.status <> 'rejected'
      AND c.targets ?| CAST(:targets AS text[])
      AND c.event_type = :event_type
      AND (c.event_date IS NULL OR c.event_date BETWEEN :date_from AND :date_to)
    """
)


# The clusters an event without a name may belong to: the same step, the same time
# window and the same words. The person is not looked for: there is none.
_CANDIDATE_CLUSTERS_UNNAMED = text(
    """
    SELECT c.id, c.core_evidence_key, c.core_article_id, c.evidence
    FROM event_clusters c
    WHERE c.active AND c.status <> 'rejected'
      AND c.event_type = :event_type
      AND (c.event_date IS NULL OR c.event_date BETWEEN :date_from AND :date_to)
    """
)


@dataclass(frozen=True)
class ClusterReport:
    events_seen: int = 0
    # Events that already have a membership, left as they are.
    unchanged: int = 0
    # Events found again after a rebuild and tied back to their old membership.
    reattached: int = 0
    joined: int = 0
    opened: int = 0
    # Of the opened clusters, those that had a candidate they were not joined to.
    ambiguous: int = 0
    # Events without a name: candidates only, no cluster.
    candidates_only: int = 0
    # Memberships whose event or article is gone.
    deactivated: int = 0
    batches: int = 0
    dry_run: bool = False


def evidence_of(event: CaseEvent) -> ClusterEvidence:
    """What the event brings to the matching."""
    words = _SPACES.sub(" ", event.quote.lower()).strip()
    return ClusterEvidence(
        event_type=event.event_type,
        event_date=event.event_date,
        targets=frozenset(person.key for person in event.targets),
        charges=frozenset(event.charges),
        courts=frozenset(event.courts),
        authorities=frozenset(event.authorities),
        places=frozenset(event.places),
        quote_hash=hashlib.sha256(words.encode()).hexdigest(),
        quote_length=len(words),
    )


def _query_of(quote: str) -> str | None:
    """The words the candidate lookup looks for in the text of the other publication."""
    words = [
        word for word in _SPACES.sub(" ", quote.lower()).split() if len(word) >= _MIN_WORD_LENGTH
    ]
    if not words:
        return None
    chosen = sorted(words, key=len, reverse=True)[:_QUERY_WORDS]
    return " & ".join(chosen)


def resolve(
    session_factory: sessionmaker[Session],
    *,
    batch_size: int = 500,
    dry_run: bool = False,
    text_similarity: Any = None,
) -> ClusterReport:
    """Put every event that has no membership into a cluster; with `dry_run` nothing is
    written (the work is rolled back at the end, so later batches still see earlier
    ones)."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    report = ClusterReport(dry_run=dry_run)
    with session_factory() as session:
        after = 0
        while True:
            if not dry_run:
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": LOCK_KEY}
                )
            ids = case_events.event_ids_after(session, after=after, limit=batch_size)
            if not ids:
                break
            after = ids[-1]
            report = _resolve_batch(session, ids, report, text_similarity)
            if not dry_run:
                session.commit()
        if not dry_run:
            report = _deactivate_orphans(session, report)
            session.commit()
        else:
            session.rollback()
    return report


def _resolve_batch(
    session: Session,
    ids: Sequence[int],
    report: ClusterReport,
    text_similarity: Any,
) -> ClusterReport:
    linked = set(
        session.scalars(
            select(EventClusterMemberRecord.event_id).where(
                EventClusterMemberRecord.event_id.in_(ids),
                EventClusterMemberRecord.active.is_(True),
            )
        )
    )
    events = case_events.case_events(
        session, [event_id for event_id in ids if event_id not in linked]
    )
    outcomes: Counter[str] = Counter()
    # Order of the batch, not of arrival: the same events give the same clusters.
    for event in sorted(events, key=_order):
        outcomes[_resolve_event(session, event, text_similarity)] += 1
    return replace(
        report,
        events_seen=report.events_seen + len(ids),
        unchanged=report.unchanged + len(linked),
        reattached=report.reattached + outcomes["reattached"],
        joined=report.joined + outcomes["joined"],
        opened=report.opened + outcomes["opened"] + outcomes["opened_ambiguous"],
        ambiguous=report.ambiguous + outcomes["opened_ambiguous"],
        candidates_only=report.candidates_only + outcomes["candidates_only"],
        batches=report.batches + 1,
    )


def _order(event: CaseEvent) -> tuple[bool, date, int, str, str]:
    return (
        event.event_date is None,
        event.event_date or date.max,
        event.article_id,
        event.event_type,
        event.quote,
    )


def _free_key(session: Session, event: CaseEvent) -> tuple[str, EventClusterMemberRecord | None]:
    """The event's key, and its membership if it has one. A key taken by another live
    event (the same sentence twice in one article) is skipped for the next ordinal."""
    ordinal = 0
    while True:
        key = evidence_key(event, ordinal=ordinal)
        member = session.get(EventClusterMemberRecord, key)
        if member is None or member.event_id in (None, event.id):
            return key, member
        ordinal += 1


def _resolve_event(session: Session, event: CaseEvent, text_similarity: Any) -> str:
    key, member = _free_key(session, event)
    evidence = evidence_of(event)
    if member is not None:
        return _reattach(session, member, event, evidence)
    if not evidence.targets:
        # An article without a name: candidates only, never a cluster of its own.
        candidates = _candidates_unnamed(session, event, evidence)
        _record_candidates(session, key, event, evidence, candidates)
        return "candidates_only"
    candidates = _candidates(session, event, evidence, text_similarity)
    chosen = _choose(candidates)
    recorded = _recorded(candidates)
    if chosen is not None:
        cluster_id = chosen[0]
        _join(session, key, cluster_id, event, evidence, chosen[1], recorded)
        return "joined"
    cluster_id = _open_cluster(session, key, event, evidence, recorded)
    _join(session, key, cluster_id, event, evidence, Match(0.0, ()), recorded, core=True)
    return "opened_ambiguous" if recorded else "opened"


def _candidates(
    session: Session, event: CaseEvent, evidence: ClusterEvidence, text_similarity: Any
) -> list[tuple[UUID, Match]]:
    """The clusters this event may join: the same person, the same step, the same time
    window, the same words."""
    if not evidence.targets:
        return []
    day = evidence.event_date
    rows = session.execute(
        _CANDIDATE_CLUSTERS,
        {
            "targets": sorted(evidence.targets),
            "event_type": evidence.event_type,
            "date_from": (day - _days(CANDIDATE_WINDOW_DAYS)) if day else date.min,
            "date_to": (day + _days(CANDIDATE_WINDOW_DAYS)) if day else date.max,
        },
    ).all()
    rows = _with_same_words(session, rows, event.quote)
    found: list[tuple[UUID, Match]] = []
    for cluster_id, _core_key, _article_id, stored in rows:
        core = ClusterEvidence.from_json(stored)
        similarity = None
        if text_similarity is not None:
            similarity = text_similarity(core.quote_hash, evidence.quote_hash)
        found.append((cluster_id, matching.score_pair(core, evidence, text_similarity=similarity)))
    return found


def _candidates_unnamed(
    session: Session, event: CaseEvent, evidence: ClusterEvidence
) -> list[tuple[UUID, Match]]:
    """The clusters an event without a name may belong to: the same step, the same days
    and the same words. Nothing is joined: an article without a name only gets
    candidates."""
    day = evidence.event_date
    rows = session.execute(
        _CANDIDATE_CLUSTERS_UNNAMED,
        {
            "event_type": evidence.event_type,
            "date_from": (day - _days(CANDIDATE_WINDOW_DAYS)) if day else date.min,
            "date_to": (day + _days(CANDIDATE_WINDOW_DAYS)) if day else date.max,
        },
    ).all()
    rows = _with_same_words(session, rows, event.quote)
    found: list[tuple[UUID, Match]] = []
    for cluster_id, _core_key, _article_id, stored in rows:
        core = ClusterEvidence.from_json(stored)
        found.append((cluster_id, matching.score_pair(core, evidence)))
    return found


def _with_same_words(session: Session, rows: Sequence[Any], quote: str) -> list[Any]:
    """The candidates whose publication holds the words of the quote.

    The text belongs to the ingestion: the words are looked for through its public API,
    never in a table of another package.
    """
    words = [
        word for word in _SPACES.sub(" ", quote.lower()).split() if len(word) >= _MIN_WORD_LENGTH
    ]
    if not words or not rows:
        return []
    words.sort(key=len, reverse=True)
    holding = ingestion_fulltext.articles_holding(
        session,
        [row[2] for row in rows],
        words[:2],
        any_of=words[2:5],
    )
    return [row for row in rows if row[2] in holding]


def _days(count: int) -> Any:
    from datetime import timedelta

    return timedelta(days=count)


def _choose(candidates: Sequence[tuple[UUID, Match]]) -> tuple[UUID, Match] | None:
    """The cluster the event joins: the only one that matches automatically. Two strong
    matches are an ambiguity, never settled by a margin."""
    accepted = [pair for pair in candidates if pair[1].automatic]
    return accepted[0] if len(accepted) == 1 else None


def _recorded(candidates: Sequence[tuple[UUID, Match]]) -> list[dict[str, Any]]:
    return [
        {
            "cluster_id": str(cluster_id),
            "score": match.score,
            "features": list(match.features),
            "vetoes": list(match.vetoes),
        }
        for cluster_id, match in sorted(candidates, key=lambda pair: (-pair[1].score, str(pair[0])))
        if match.score >= _RECORDED_SCORE
    ][:MAX_RECORDED_CANDIDATES]


def _open_cluster(
    session: Session,
    key: str,
    event: CaseEvent,
    evidence: ClusterEvidence,
    recorded: list[dict[str, Any]],
) -> UUID:
    cluster = EventClusterRecord(
        core_evidence_key=key,
        core_article_id=event.article_id,
        event_type=event.event_type,
        event_date=event.event_date,
        targets=sorted(evidence.targets),
        evidence=evidence.to_json(),
        status="confirmed",
        score=0.0,
        reasons=[],
        candidates=recorded,
        algorithm_version=matching.VERSION,
        active=True,
    )
    session.add(cluster)
    session.flush()
    return cluster.id


def _join(
    session: Session,
    key: str,
    cluster_id: UUID,
    event: CaseEvent,
    evidence: ClusterEvidence,
    match: Match,
    recorded: list[dict[str, Any]],
    core: bool = False,
) -> None:
    session.add(
        EventClusterMemberRecord(
            evidence_key=key,
            event_id=event.id,
            cluster_id=cluster_id,
            role="core" if core else "member",
            status="confirmed",
            score=match.score,
            reasons=list(match.features),
            candidates=recorded,
            algorithm_version=matching.VERSION,
            active=True,
        )
    )


def _record_candidates(
    session: Session,
    key: str,
    event: CaseEvent,
    evidence: ClusterEvidence,
    candidates: Sequence[tuple[UUID, Match]],
) -> None:
    """An event without a name: the clusters it could have joined, and nothing else."""
    session.add(
        EventClusterMemberRecord(
            evidence_key=key,
            event_id=event.id,
            cluster_id=None,
            role="candidate",
            status="candidate",
            score=0.0,
            reasons=[],
            candidates=_recorded(candidates),
            algorithm_version=matching.VERSION,
            active=True,
        )
    )


def _reattach(
    session: Session,
    member: EventClusterMemberRecord,
    event: CaseEvent,
    evidence: ClusterEvidence,
) -> str:
    """The event processing has re-extracted: the membership is found again by its key
    and tied to the new event."""
    member.event_id = event.id
    member.active = True
    member.updated_at = datetime_now()
    return "reattached"


def _deactivate_orphans(session: Session, report: ClusterReport) -> ClusterReport:
    """The memberships whose event or article is gone: they are deactivated, the rows
    and their decisions stay."""
    rows = list(
        session.execute(
            select(EventClusterMemberRecord.evidence_key, EventClusterMemberRecord.event_id).where(
                EventClusterMemberRecord.active.is_(True),
                EventClusterMemberRecord.event_id.is_not(None),
            )
        )
    )
    alive = {event.id for event in case_events.case_events(session, [row[1] for row in rows])}
    gone = [row for row in rows if row[1] not in alive]
    if not gone:
        return report
    for evidence_key_value, _event_id in gone:
        member = session.get(EventClusterMemberRecord, evidence_key_value)
        if member is not None:
            member.active = False
            member.updated_at = datetime_now()
    return replace(report, deactivated=len(gone))


def datetime_now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)
