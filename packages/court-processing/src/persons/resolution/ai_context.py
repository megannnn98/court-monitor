"""What an AI reviewer is shown: the two sides of one pending ER v2 decision.

Only the stored decision, the candidate persons it compared and short quotes around the
mentions. Never a whole publication, never the corpus, never search results — an LLM
reads as little as the question needs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ingestion import read
from persons.resolution.ai_review import (
    EVIDENCE_EXCERPTS_PER_SIDE,
    CandidateReviewContext,
    DecisionReviewContext,
    EntityReviewRequest,
    EvidenceExcerpt,
    MentionReviewContext,
)
from persons.resolution.models import PersonResolutionReason
from persons.resolution.normalizer import name_is_complete
from persons.storage.models import PersonRecord, PersonResolutionDecisionRecord
from processing.storage.models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
)

# Characters of article text quoted around a mention: the sentence it appears in.
QUOTE_RADIUS = 200
# Candidates are reviewed in score order; deeper candidates are noise for the model.
MAX_REVIEWED_CANDIDATES = 3


def _quote(text: str, start: int, end: int) -> str:
    left = max(0, start - QUOTE_RADIUS)
    right = min(len(text), end + QUOTE_RADIUS)
    quote = text[left:right].strip()
    return ("…" if left > 0 else "") + quote + ("…" if right < len(text) else "")


# Stands in for an unknown date in the sort key; the flag before it already puts those last.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _article_source(session: Session, article_id: int) -> tuple[str | None, str | None]:
    card = read.article_cards(session, [article_id]).get(article_id)
    return (None, None) if card is None else (card.source_name, card.url)


def _excerpt(article: read.StoredArticle, start: int, end: int) -> EvidenceExcerpt:
    return EvidenceExcerpt(
        article_id=article.id,
        source_name=article.source_name,
        url=article.url,
        published_at=article.published_at,
        text=f"{article.title}. {_quote(article.text, start, end)}",
    ).trimmed()


def _mention_rows(session: Session, condition: Any) -> list[Any]:
    """(mention id, article id, start, end) of the mentions `condition` picks."""
    return list(
        session.execute(
            select(
                EntityMentionRecord.id,
                ArticleExtractionRunRecord.article_id,
                EntityMentionRecord.start_offset,
                EntityMentionRecord.end_offset,
            )
            .join(
                ArticleExtractionRunRecord,
                ArticleExtractionRunRecord.id == EntityMentionRecord.extraction_run_id,
            )
            .where(condition)
        ).all()
    )


def mention_evidence(session: Session, mention_id: int) -> tuple[EvidenceExcerpt, ...]:
    """The sentence the mention stands in, from its own article."""
    rows = _mention_rows(session, EntityMentionRecord.id == mention_id)
    if not rows:
        return ()
    _, article_id, start, end = rows[0]
    article = read.stored_articles(session, [article_id]).get(article_id)
    if article is None:
        return ()
    return (_excerpt(article, start, end),)


def candidate_evidence(
    session: Session, person_id: int, *, limit: int = EVIDENCE_EXCERPTS_PER_SIDE
) -> tuple[EvidenceExcerpt, ...]:
    """Quotes of the person's own latest mentions: what the pipeline knows about them."""
    rows = _mention_rows(session, EntityMentionRecord.person_id == person_id)
    published = read.published_at(session, {row[1] for row in rows})
    # Latest publication first (unknown dates last), then the latest mention.
    latest = sorted(
        (row for row in rows if row[1] in published),
        key=lambda row: (
            published[row[1]] is None,
            -(published[row[1]] or _EPOCH).timestamp(),
            -row[0],
        ),
    )[:limit]
    articles = read.stored_articles(session, {row[1] for row in latest})
    return tuple(
        _excerpt(articles[article_id], start, end)
        for _, article_id, start, end in latest
        if article_id in articles
    )


def candidate_event_types(session: Session, person_id: int, *, limit: int = 10) -> tuple[str, ...]:
    rows = session.scalars(
        select(ExtractedEventRecord.event_type)
        .join(
            EventEntityMentionRecord, EventEntityMentionRecord.event_id == ExtractedEventRecord.id
        )
        .join(EntityMentionRecord, EntityMentionRecord.id == EventEntityMentionRecord.mention_id)
        .where(EntityMentionRecord.person_id == person_id)
        .order_by(ExtractedEventRecord.id.desc())
        .limit(limit)
    ).all()
    return tuple(dict.fromkeys(str(event_type) for event_type in rows))


def build_review_context(
    session: Session, decision: PersonResolutionDecisionRecord
) -> DecisionReviewContext:
    """The AI review input of one pending decision, from its stored ER v2 snapshot."""
    identity = decision.identity
    normalized = identity.get("normalized") or {}
    name = str(identity["name"])
    mention = MentionReviewContext(
        mention_id=decision.mention_id,
        name=name,
        surface_text=identity.get("surface_text"),
        normalized_form=normalized.get("canonical_form"),
        matching_key=identity.get("matching_key"),
        article_id=identity.get("article_id"),
        event_types=_strings(identity.get("event_types")),
        evidence=mention_evidence(session, decision.mention_id),
        name_is_complete=_name_is_complete(name),
    )
    reasons = tuple(
        PersonResolutionReason(reason)
        for reason in decision.reasons
        if reason in set(PersonResolutionReason)
    )
    ranked = sorted(
        decision.candidates,
        key=lambda raw: (-float(raw["score"]["resolution_score"]), raw["candidate"]["person_id"]),
    )[:MAX_REVIEWED_CANDIDATES]
    active = {
        person_id
        for person_id in session.scalars(
            select(PersonRecord.id).where(
                PersonRecord.id.in_([raw["candidate"]["person_id"] for raw in ranked]),
                PersonRecord.status == "active",
            )
        ).all()
    }
    requests = tuple(
        EntityReviewRequest(
            decision_id=decision.id,
            mention=mention,
            candidate=_candidate_context(session, raw),
            deterministic_score=float(raw["score"]["resolution_score"]),
            matched_features=_matched_features(raw["features"]),
            conflicting_features=tuple(raw["features"].get("conflicts") or ()),
            review_reasons=reasons,
        )
        for raw in ranked
        if raw["candidate"]["person_id"] in active
    )
    return DecisionReviewContext(
        decision_id=decision.id,
        mention=mention,
        requests=requests,
        review_reasons=reasons,
        resolver_version=decision.resolver_version,
    )


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _candidate_context(session: Session, raw: dict[str, Any]) -> CandidateReviewContext:
    candidate: dict[str, Any] = raw["candidate"]
    features: dict[str, Any] = raw["features"]
    person_id = int(candidate["person_id"])
    return CandidateReviewContext(
        person_id=person_id,
        canonical_name=str(candidate["canonical_name"]),
        matching_key=str(candidate.get("matching_key") or ""),
        aliases=_strings(candidate.get("aliases")),
        candidate_sources=_strings(candidate.get("sources")),
        resolution_score=float(raw["score"]["resolution_score"]),
        surname_match=str(features["surname"]),
        given_name_match=str(features["given_name"]),
        patronymic_match=str(features["patronymic"]),
        conflicts=_strings(features.get("conflicts")),
        same_article_mention=bool(features.get("same_article_mention")),
        case_context_match=bool(features.get("case_context_match")),
        event_types=candidate_event_types(session, person_id),
        evidence=candidate_evidence(session, person_id),
    )


def _matched_features(features: dict[str, Any]) -> tuple[str, ...]:
    """The deterministic comparison in words, so the model sees what ER v2 already knows."""
    matched = []
    for component in ("surname", "given_name", "patronymic"):
        value = features.get(component)
        if value in ("exact", "typo", "initial_compatible"):
            matched.append(f"{component}:{value}")
    if features.get("exact_alias"):
        matched.append("exact_alias")
    if features.get("exact_matching_key"):
        matched.append("exact_matching_key")
    if features.get("same_article_mention"):
        matched.append("same_article_mention")
    if features.get("case_context_match"):
        matched.append("case_context_match")
    return tuple(matched)


def _name_is_complete(name: str) -> bool:
    """A full given name and surname: not a single token, not initials («А.И.» included)."""
    return name_is_complete(name)
