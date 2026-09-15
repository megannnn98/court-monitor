"""Read-only views of the pipeline state in PostgreSQL, keyed by article and span.

Database ids are used only to join rows inside one evaluation run; every
comparison with the golden dataset goes through `source:external_id` and
character offsets.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
    MonitoringFindingRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonEventLinkRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
    RosfinMatchRecord,
    Source,
    SourceDocument,
)
from evaluation.real_world.models import article_key
from persecution.queries import latest_persecution_classification_ids
from sources.source_registry import SOURCES

_SOURCE_NAMES_BY_BASE_URL = {definition.base_url: name for name, definition in SOURCES.items()}


@dataclass(frozen=True)
class DbArticle:
    article_id: int
    key: str
    title: str
    text: str
    url: str


@dataclass(frozen=True)
class DbDecision:
    action: str
    status: str
    selected_person_id: int | None
    candidate_person_ids: tuple[int, ...]


@dataclass(frozen=True)
class DbMention:
    mention_id: int
    article_key: str
    start: int
    end: int
    surface: str
    person_id: int | None
    decision: DbDecision | None


@dataclass(frozen=True)
class DbEvent:
    event_id: int
    article_key: str
    event_type: str
    start: int
    end: int
    person_ids: frozenset[int]


@dataclass
class PipelineState:
    articles: dict[str, DbArticle] = field(default_factory=dict)
    mentions: dict[str, list[DbMention]] = field(default_factory=lambda: defaultdict(list))
    events: dict[str, list[DbEvent]] = field(default_factory=lambda: defaultdict(list))
    person_status: dict[int, str] = field(default_factory=dict)
    classifications: dict[int, str] = field(default_factory=dict)
    rf_matches: dict[int, str] = field(default_factory=dict)
    active_findings: set[int] = field(default_factory=set)

    def article_by_id(self) -> dict[int, DbArticle]:
        return {article.article_id: article for article in self.articles.values()}


def stored_candidate_person_ids(candidates: list[dict[str, Any]] | None) -> tuple[int, ...]:
    """Ranked person ids of a stored decision (`ScoredPersonCandidate` JSON)."""
    ids = []
    for scored in candidates or []:
        candidate = scored.get("candidate") or {}
        if candidate.get("person_id") is not None:
            ids.append(int(candidate["person_id"]))
    return tuple(ids)


def source_name_for(base_url: str) -> str:
    return _SOURCE_NAMES_BY_BASE_URL.get(base_url, base_url)


def load_pipeline_state(
    session: Session, *, snapshot_id: int | None, article_keys: set[str] | None = None
) -> PipelineState:
    """Articles (optionally only `article_keys`), their latest successful extraction, links."""
    state = PipelineState()
    for article_id, base_url, external_id, title, text, url in session.execute(
        select(
            ParsedArticleRecord.id,
            Source.base_url,
            SourceDocument.external_id,
            ParsedArticleRecord.title,
            ParsedArticleRecord.text,
            SourceDocument.canonical_url,
        )
        .join(SourceDocument, SourceDocument.id == ParsedArticleRecord.document_id)
        .join(Source, Source.id == SourceDocument.source_id)
    ).all():
        key = article_key(source_name_for(base_url), external_id)
        if article_keys is None or key in article_keys:
            state.articles[key] = DbArticle(article_id, key, title, text, url)
    key_by_article = {article.article_id: article.key for article in state.articles.values()}

    latest_runs = (
        select(func.max(ArticleExtractionRunRecord.id))
        .where(ArticleExtractionRunRecord.status == "succeeded")
        .group_by(ArticleExtractionRunRecord.article_id)
    )
    run_articles = dict(
        session.execute(
            select(ArticleExtractionRunRecord.id, ArticleExtractionRunRecord.article_id).where(
                ArticleExtractionRunRecord.id.in_(latest_runs),
                ArticleExtractionRunRecord.article_id.in_(list(key_by_article)),
            )
        )
        .tuples()
        .all()
    )

    decisions: dict[int, DbDecision] = {}
    for mention_id, action, status, selected, candidates in session.execute(
        select(
            PersonResolutionDecisionRecord.mention_id,
            PersonResolutionDecisionRecord.action,
            PersonResolutionDecisionRecord.status,
            PersonResolutionDecisionRecord.selected_person_id,
            PersonResolutionDecisionRecord.candidates,
        ).order_by(PersonResolutionDecisionRecord.id)
    ).all():
        decisions[mention_id] = DbDecision(
            action=action,
            status=status,
            selected_person_id=selected,
            candidate_person_ids=stored_candidate_person_ids(candidates),
        )

    for mention_id, run_id, start, end, surface, person_id in session.execute(
        select(
            EntityMentionRecord.id,
            EntityMentionRecord.extraction_run_id,
            EntityMentionRecord.start_offset,
            EntityMentionRecord.end_offset,
            EntityMentionRecord.surface_text,
            EntityMentionRecord.person_id,
        )
        .where(
            EntityMentionRecord.entity_type == "person",
            EntityMentionRecord.extraction_run_id.in_(list(run_articles)),
        )
        .order_by(EntityMentionRecord.id)
    ).all():
        key = key_by_article[run_articles[run_id]]
        state.mentions[key].append(
            DbMention(mention_id, key, start, end, surface, person_id, decisions.get(mention_id))
        )

    links: dict[int, set[int]] = defaultdict(set)
    for event_id, person_id in session.execute(
        select(PersonEventLinkRecord.event_id, PersonEventLinkRecord.person_id)
    ).all():
        links[event_id].add(person_id)
    for event_id, run_id, event_type, start, end in session.execute(
        select(
            ExtractedEventRecord.id,
            ExtractedEventRecord.extraction_run_id,
            ExtractedEventRecord.event_type,
            ExtractedEventRecord.start_offset,
            ExtractedEventRecord.end_offset,
        )
        .where(ExtractedEventRecord.extraction_run_id.in_(list(run_articles)))
        .order_by(ExtractedEventRecord.id)
    ).all():
        key = key_by_article[run_articles[run_id]]
        state.events[key].append(
            DbEvent(event_id, key, event_type, start, end, frozenset(links.get(event_id, set())))
        )

    state.person_status = dict(
        session.execute(select(PersonRecord.id, PersonRecord.status)).tuples().all()
    )
    state.classifications = dict(
        session.execute(
            select(
                PersecutionClassificationRecord.person_id, PersecutionClassificationRecord.status
            ).where(PersecutionClassificationRecord.id.in_(latest_persecution_classification_ids()))
        )
        .tuples()
        .all()
    )
    if snapshot_id is not None:
        state.rf_matches = dict(
            session.execute(
                select(RosfinMatchRecord.person_id, RosfinMatchRecord.status).where(
                    RosfinMatchRecord.snapshot_id == snapshot_id
                )
            )
            .tuples()
            .all()
        )
    state.active_findings = set(
        session.scalars(
            select(MonitoringFindingRecord.person_id).where(
                MonitoringFindingRecord.active.is_(True)
            )
        ).all()
    )
    return state
