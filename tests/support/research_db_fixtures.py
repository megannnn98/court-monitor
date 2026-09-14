"""Database seeding helpers for research layer tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonRecord,
    RosfinMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
    Source,
    SourceDocument,
)

FIXED_TIME = datetime(2024, 1, 1, tzinfo=UTC)


class ResearchSeeder:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._texts: dict[int, str] = {}

    def _add[T](self, record: T) -> T:
        self.session.add(record)
        self.session.flush()
        return record

    def source(self, name: str, base_url: str) -> int:
        return self._add(Source(name=name, base_url=base_url)).id

    def article(
        self,
        source_id: int,
        *,
        external_id: str,
        title: str,
        text: str,
        published_at: datetime | None = FIXED_TIME,
    ) -> tuple[int, int]:
        """Create a document, parsed article and succeeded extraction run.

        Returns (article_id, extraction_run_id).
        """
        document = self._add(
            SourceDocument(
                source_id=source_id,
                external_id=external_id,
                canonical_url=f"https://example.test/{external_id}",
                fetched_at=FIXED_TIME,
                content_type="text/html",
                raw_content=b"<html></html>",
            )
        )
        article = self._add(
            ParsedArticleRecord(
                document_id=document.id,
                title=title,
                published_at=published_at,
                text=text,
            )
        )
        run = self._add(
            ArticleExtractionRunRecord(
                article_id=article.id,
                article_content_hash=f"hash-{external_id}",
                extractor_name="rule-based",
                extractor_version="1.0.0",
                normalizer_version="1.0.0",
                status="succeeded",
                started_at=FIXED_TIME,
                finished_at=FIXED_TIME,
            )
        )
        self._texts[run.id] = text
        return article.id, run.id

    def person(self, canonical_name: str, *, status: str = "active") -> int:
        normalized = canonical_name.lower()
        return self._add(
            PersonRecord(
                canonical_name=canonical_name,
                normalized_name=normalized,
                matching_key=normalized.replace(" ", "|"),
                status=status,
            )
        ).id

    def alias(self, person_id: int, surface_text: str) -> int:
        normalized = surface_text.lower()
        return self._add(
            PersonAliasRecord(
                person_id=person_id,
                surface_text=surface_text,
                normalized_text=normalized,
                matching_key=normalized.replace(" ", "|"),
                origin="extraction",
                confidence=0.9,
            )
        ).id

    def _span(self, run_id: int, fragment: str) -> tuple[int, int]:
        start = self._texts[run_id].index(fragment)
        return start, start + len(fragment)

    def mention(
        self,
        run_id: int,
        surface_text: str,
        *,
        person_id: int | None,
        entity_type: str = "person",
    ) -> int:
        start, end = self._span(run_id, surface_text)
        return self._add(
            EntityMentionRecord(
                extraction_run_id=run_id,
                entity_type=entity_type,
                surface_text=surface_text,
                normalized_text=surface_text.lower(),
                start_offset=start,
                end_offset=end,
                confidence=0.9,
                normalized_data={},
                extractor_name="rule-based",
                extractor_version="1.0.0",
                normalizer_version="1.0.0",
                person_id=person_id,
            )
        ).id

    def event(
        self,
        run_id: int,
        span: str,
        *,
        event_type: str,
        event_date: datetime | None,
        links: list[tuple[int, str]],
        attributes: dict[str, Any] | None = None,
        entity_links: list[tuple[int, str]] | None = None,
    ) -> int:
        start, end = self._span(run_id, span)
        event = self._add(
            ExtractedEventRecord(
                extraction_run_id=run_id,
                event_type=event_type,
                event_date=event_date,
                start_offset=start,
                end_offset=end,
                confidence=0.8,
                attributes=attributes or {},
                extractor_name="rule-based-events",
                extractor_version="1.0.0",
            )
        )
        for person_id, role in links:
            self._add(
                PersonEventLinkRecord(
                    person_id=person_id, event_id=event.id, role=role, confidence=0.8
                )
            )
        for mention_id, role in entity_links or []:
            self._add(EventEntityMentionRecord(event_id=event.id, mention_id=mention_id, role=role))
        return event.id

    def classification(
        self,
        person_id: int,
        status: str,
        confidence: float,
        *,
        classifier_version: str = "1.0.0",
        classified_at: datetime = FIXED_TIME,
        reasons: list[str] | None = None,
        evidence_types: list[str] | None = None,
    ) -> int:
        return self._add(
            PersecutionClassificationRecord(
                person_id=person_id,
                status=status,
                confidence=confidence,
                reasons=reasons or [],
                evidence_types=evidence_types or [],
                classifier_name="rule-based",
                classifier_version=classifier_version,
                classified_at=classified_at,
            )
        ).id

    def snapshot(self, content_hash: str = "snapshot-hash") -> int:
        return self._add(
            RosfinmonitoringSnapshotRecord(
                snapshot_date=FIXED_TIME,
                source_url="https://fedsfm.test/list",
                content_hash=content_hash,
                entry_count=1,
                fetched_at=FIXED_TIME,
            )
        ).id

    def entry(self, snapshot_id: int, full_name: str) -> int:
        normalized = full_name.lower()
        return self._add(
            RosfinmonitoringEntryRecord(
                snapshot_id=snapshot_id,
                full_name=full_name,
                normalized_name=normalized,
                matching_key=normalized.replace(" ", "|"),
                status="active",
                raw_data={},
            )
        ).id

    def match(
        self,
        person_id: int,
        snapshot_id: int,
        status: str,
        confidence: float,
        *,
        matched_entry_id: int | None = None,
        matched_entry_name: str | None = None,
        reasons: list[str] | None = None,
    ) -> int:
        return self._add(
            RosfinMatchRecord(
                person_id=person_id,
                snapshot_id=snapshot_id,
                status=status,
                confidence=confidence,
                matched_entry_id=matched_entry_id,
                matched_entry_name=matched_entry_name,
                candidate_entries=[],
                reasons=reasons or [],
                matched_at=FIXED_TIME,
            )
        ).id
