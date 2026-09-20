"""What an AI reviewer is shown: the two sides of one pending ER v2 decision.

Only the stored decision, the candidate persons it compared and short quotes around the
mentions. Never a whole publication, never the corpus, never search results — an LLM
reads as little as the question needs.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
    Source,
    SourceDocument,
)
from persons.resolution.ai_review import (
    EVIDENCE_EXCERPTS_PER_SIDE,
    CandidateReviewContext,
    DecisionReviewContext,
    EntityReviewRequest,
    EvidenceExcerpt,
    MentionReviewContext,
)
from persons.resolution.models import PersonResolutionReason

# Characters of article text quoted around a mention: the sentence it appears in.
QUOTE_RADIUS = 200
# Candidates are reviewed in score order; deeper candidates are noise for the model.
MAX_REVIEWED_CANDIDATES = 3


def _quote(text: str, start: int, end: int) -> str:
    left = max(0, start - QUOTE_RADIUS)
    right = min(len(text), end + QUOTE_RADIUS)
    quote = text[left:right].strip()
    return ("…" if left > 0 else "") + quote + ("…" if right < len(text) else "")


def _article_source(session: Session, article_id: int) -> tuple[str | None, str | None]:
    row = session.execute(
        select(Source.name, SourceDocument.canonical_url)
        .join(SourceDocument, SourceDocument.source_id == Source.id)
        .join(ParsedArticleRecord, ParsedArticleRecord.document_id == SourceDocument.id)
        .where(ParsedArticleRecord.id == article_id)
    ).one_or_none()
    return (None, None) if row is None else (row.name, row.canonical_url)


def mention_evidence(session: Session, mention_id: int) -> tuple[EvidenceExcerpt, ...]:
    """The sentence the mention stands in, from its own article."""
    row = session.execute(
        select(
            ParsedArticleRecord.id,
            ParsedArticleRecord.title,
            ParsedArticleRecord.text,
            ParsedArticleRecord.published_at,
            EntityMentionRecord.start_offset,
            EntityMentionRecord.end_offset,
        )
        .join(
            ArticleExtractionRunRecord,
            ArticleExtractionRunRecord.id == EntityMentionRecord.extraction_run_id,
        )
        .join(ParsedArticleRecord, ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id)
        .where(EntityMentionRecord.id == mention_id)
    ).one_or_none()
    if row is None:
        return ()
    source_name, url = _article_source(session, row.id)
    return (
        EvidenceExcerpt(
            article_id=row.id,
            source_name=source_name,
            url=url,
            published_at=row.published_at,
            text=f"{row.title}. {_quote(row.text, row.start_offset, row.end_offset)}",
        ).trimmed(),
    )


def candidate_evidence(
    session: Session, person_id: int, *, limit: int = EVIDENCE_EXCERPTS_PER_SIDE
) -> tuple[EvidenceExcerpt, ...]:
    """Quotes of the person's own latest mentions: what the pipeline knows about them."""
    rows = session.execute(
        select(
            ParsedArticleRecord.id,
            ParsedArticleRecord.title,
            ParsedArticleRecord.text,
            ParsedArticleRecord.published_at,
            EntityMentionRecord.start_offset,
            EntityMentionRecord.end_offset,
        )
        .join(
            ArticleExtractionRunRecord,
            ArticleExtractionRunRecord.id == EntityMentionRecord.extraction_run_id,
        )
        .join(ParsedArticleRecord, ParsedArticleRecord.id == ArticleExtractionRunRecord.article_id)
        .where(EntityMentionRecord.person_id == person_id)
        .order_by(
            ParsedArticleRecord.published_at.desc().nullslast(), EntityMentionRecord.id.desc()
        )
        .limit(limit)
    ).all()
    excerpts = []
    for row in rows:
        source_name, url = _article_source(session, row.id)
        excerpts.append(
            EvidenceExcerpt(
                article_id=row.id,
                source_name=source_name,
                url=url,
                published_at=row.published_at,
                text=f"{row.title}. {_quote(row.text, row.start_offset, row.end_offset)}",
            ).trimmed()
        )
    return tuple(excerpts)


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
    """A full given name and surname: not a single token, not initials only."""
    tokens = [token for token in name.split() if token]
    full = [token for token in tokens if len(token.rstrip(".")) > 1]
    return len(full) >= 2
