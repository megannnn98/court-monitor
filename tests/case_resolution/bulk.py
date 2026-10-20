"""Many events at once, written layer by layer: a world for the speed of case resolution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from ingestion.storage.models import ParsedArticleRecord, Source, SourceDocument
from processing.storage.models import (
    ArticleExtractionRunRecord,
    EntityGroupChargeRecord,
    EntityGroupMentionRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
)

START = datetime(2020, 1, 1, tzinfo=UTC)
KINDS = ("case_opened", "detention", "arrest", "charge", "sentence")


def seed_events(
    factory: sessionmaker[Session], *, people: int, per_person: int, prolific: int = 0
) -> int:
    """`people` figurants with `per_person` events each, and one with `prolific` more.
    Returns the number of events written."""
    plan = [(f"Человек{number:05d} Фамилия", per_person) for number in range(people)]
    if prolific:
        plan.append(("Многосюжетный Фигурант", prolific))
    flat = [(name, step) for name, events in plan for step in range(events)]
    with factory() as session:
        source = Source(name="bulk", base_url="https://bulk.example.test")
        session.add(source)
        session.flush()
        documents = [
            SourceDocument(
                source_id=source.id,
                external_id=f"b{number}",
                canonical_url=f"https://bulk.example.test/{number}",
                fetched_at=START,
                content_type="text/html",
                raw_content=b"",
            )
            for number in range(len(flat))
        ]
        session.add_all(documents)
        session.flush()
        texts = [
            f"{name} {KINDS[step % len(KINDS)]} №{number}"
            for number, (name, step) in enumerate(flat)
        ]
        articles = [
            ParsedArticleRecord(
                document_id=document.id, title="Статья", published_at=START, text=text
            )
            for document, text in zip(documents, texts, strict=True)
        ]
        session.add_all(articles)
        session.flush()
        runs = [
            ArticleExtractionRunRecord(
                article_id=article.id,
                article_content_hash=f"h{article.id}",
                extractor_name="rule-based",
                extractor_version="1",
                normalizer_version="1",
                status="succeeded",
                started_at=START,
            )
            for article in articles
        ]
        session.add_all(runs)
        session.flush()
        groups = {
            name: EntityGroupRecord(
                key=name.lower(),
                name=name,
                variants=[],
                mention_count=events,
                article_count=events,
                event_types={},
            )
            for name, events in plan
        }
        session.add_all(groups.values())
        session.flush()
        session.add_all(
            EntityGroupRoleRecord(
                group_id=group.id, role="figurant", kind=None, method="article", reason="", quote=""
            )
            for group in groups.values()
        )
        mentions = [
            EntityMentionRecord(
                extraction_run_id=run.id,
                entity_type="person",
                surface_text=name,
                normalized_text=name.lower(),
                start_offset=0,
                end_offset=len(name),
                confidence=0.9,
                normalized_data={},
                extractor_name="rule-based",
                extractor_version="1",
                normalizer_version="1",
            )
            for run, (name, _) in zip(runs, flat, strict=True)
        ]
        session.add_all(mentions)
        events = [
            ExtractedEventRecord(
                extraction_run_id=run.id,
                event_type=KINDS[step % len(KINDS)],
                event_date=START + timedelta(days=30 * step),
                start_offset=0,
                end_offset=len(text),
                confidence=0.8,
                attributes={},
                extractor_name="rule-based-events",
                extractor_version="1",
            )
            for run, (_, step), text in zip(runs, flat, texts, strict=True)
        ]
        session.add_all(events)
        session.flush()
        for mention, event, (name, step) in zip(mentions, events, flat, strict=True):
            session.add(
                EventEntityMentionRecord(event_id=event.id, mention_id=mention.id, role="target")
            )
            session.add(EntityGroupMentionRecord(group_id=groups[name].id, mention_id=mention.id))
            session.add(
                EntityGroupChargeRecord(
                    group_id=groups[name].id,
                    event_id=event.id,
                    publication_id=event.extraction_run_id,
                    article="205.2",
                    part="3",
                    clause=None,
                    event_type=event.event_type,
                    other_targets=0,
                    quote="",
                )
            )
        session.commit()
    return len(flat)
