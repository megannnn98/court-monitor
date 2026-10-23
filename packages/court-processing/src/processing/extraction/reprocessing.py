"""What re-extracting the stored corpus with the current versions would touch, read-only.

Monitoring extracts only articles no run of the extractor has seen (`pending.py`). Reading
the corpus again with newer rules is an explicit operation: it adds a run per article and
keeps the older ones. This module selects those articles and describes the consequences
without writing anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session, sessionmaker

from processing.extraction.pipeline import ExtractionVersions

_STALE = text(
    """
    SELECT a.id, length(coalesce(a.text, '')) AS text_length
    FROM parsed_articles a
    WHERE NOT EXISTS (
        SELECT 1 FROM article_extraction_runs r
        WHERE r.article_id = a.id
          AND r.extractor_name = :extractor_name
          AND r.extractor_version = :extractor_version
          AND r.normalizer_version = :normalizer_version
          AND r.status = 'succeeded'
    )
    ORDER BY a.id
    """
)
_STALE_OF = text(
    """
    SELECT a.id, length(coalesce(a.text, '')) AS text_length
    FROM parsed_articles a
    WHERE a.id IN :ids
      AND NOT EXISTS (
        SELECT 1 FROM article_extraction_runs r
        WHERE r.article_id = a.id
          AND r.extractor_name = :extractor_name
          AND r.extractor_version = :extractor_version
          AND r.normalizer_version = :normalizer_version
          AND r.status = 'succeeded'
    )
    ORDER BY a.id
    """
).bindparams(bindparam("ids", expanding=True))

# Entity groups point at the mentions of the article's latest run; a new run moves "latest"
# and the groups wait for the next rebuild of the entities.
_GROUP_MENTIONS_ON_OLDER_RUNS = text(
    """
    SELECT count(*) FROM entity_group_mentions g
    JOIN entity_mentions m ON m.id = g.mention_id
    JOIN article_extraction_runs r ON r.id = m.extraction_run_id
    WHERE r.id <> (
        SELECT max(r2.id) FROM article_extraction_runs r2
        WHERE r2.article_id = r.article_id AND r2.status = 'succeeded'
    )
    """
)
_ARTICLES_WITH_SEVERAL_RUNS = text(
    """
    SELECT count(*) FROM (
        SELECT article_id FROM article_extraction_runs
        WHERE status = 'succeeded' GROUP BY article_id HAVING count(*) > 1
    ) s
    """
)
_LINKED_PERSON_EVENTS = text("SELECT count(*) FROM person_event_links")


@dataclass(frozen=True)
class StaleArticle:
    article_id: int
    text_length: int


@dataclass(frozen=True)
class ReprocessingPlan:
    versions: ExtractionVersions
    stale_total: int
    selected: tuple[int, ...]
    selected_without_text: int
    articles_with_several_runs: int
    group_mentions_on_older_runs: int
    person_event_links: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


def stale_articles(
    session_factory: sessionmaker[Session],
    versions: ExtractionVersions,
    *,
    article_ids: Sequence[int] | None = None,
) -> list[StaleArticle]:
    """Stored articles with no run under exactly these versions, in id order.

    A restart selects the rest by itself: an article done is no longer stale.
    """
    params = {
        "extractor_name": versions.extractor_name,
        "extractor_version": versions.extractor_version,
        "normalizer_version": versions.normalizer_version,
    }
    with session_factory() as session:
        if article_ids is None:
            rows = session.execute(_STALE, params).all()
        elif not article_ids:
            rows = []
        else:
            rows = session.execute(_STALE_OF, {**params, "ids": list(article_ids)}).all()
    return [StaleArticle(row.id, row.text_length) for row in rows]


def plan_reprocessing(
    session_factory: sessionmaker[Session],
    versions: ExtractionVersions,
    *,
    article_ids: Sequence[int] | None = None,
    limit: int | None = None,
) -> ReprocessingPlan:
    """The articles a re-extraction would read and what it leaves behind. Writes nothing."""
    stale = stale_articles(session_factory, versions)
    if article_ids is not None:
        wanted = set(article_ids)
        chosen = [article for article in stale if article.article_id in wanted]
    else:
        chosen = stale
    if limit is not None:
        chosen = chosen[:limit]
    with session_factory() as session:
        several_runs = session.scalar(_ARTICLES_WITH_SEVERAL_RUNS) or 0
        on_older_runs = session.scalar(_GROUP_MENTIONS_ON_OLDER_RUNS) or 0
        person_links = session.scalar(_LINKED_PERSON_EVENTS) or 0
    without_text = sum(1 for article in chosen if article.text_length == 0)
    warnings = [
        "older runs are kept; readers must take the latest succeeded run per article",
        (
            "entity groups keep pointing at older runs until the entities are rebuilt "
            "(the rebuild asks a paid model: not part of re-extraction)"
        ),
    ]
    if without_text:
        warnings.append(f"{without_text} selected articles have no text and will fail")
    if article_ids is not None and len(chosen) < len(set(article_ids)):
        warnings.append("some of the article ids are unknown or already extracted")
    return ReprocessingPlan(
        versions=versions,
        stale_total=len(stale),
        selected=tuple(article.article_id for article in chosen),
        selected_without_text=without_text,
        articles_with_several_runs=several_runs,
        group_mentions_on_older_runs=on_older_runs,
        person_event_links=person_links,
        warnings=tuple(warnings),
    )
