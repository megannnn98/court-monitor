from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from orm_models import (
    ArticleExtractionRunRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonEventLinkRecord,
)
from persecution_classifier import RuleBasedPersecutionClassifier
from persecution_models import PersecutionClassification


class PersecutionClassificationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        classifier: RuleBasedPersecutionClassifier | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.classifier = classifier or RuleBasedPersecutionClassifier()

    def classify_person(self, person_id: int) -> PersecutionClassification:
        """Classify a person based on their events and articles."""
        with self.session_factory() as session:
            events = self._get_person_events(session, person_id)
            articles = self._get_person_articles(session, person_id)

            classification = self.classifier.classify(
                person_id=person_id,
                events=events,
                articles=articles,
            )

            self._save_classification(session, classification)
            session.commit()

            return classification

    def classify_all_persons(self) -> list[PersecutionClassification]:
        """Classify all persons in the database."""
        with self.session_factory() as session:
            person_ids = session.scalars(select(PersonEventLinkRecord.person_id).distinct()).all()

            classifications = []
            for person_id in person_ids:
                classification = self.classify_person(person_id)
                classifications.append(classification)

            return classifications

    def get_person_classification(
        self,
        person_id: int,
        classifier_name: str | None = None,
    ) -> PersecutionClassificationRecord | None:
        """Get the latest classification for a person."""
        with self.session_factory() as session:
            query = select(PersecutionClassificationRecord).where(
                PersecutionClassificationRecord.person_id == person_id
            )

            if classifier_name:
                query = query.where(
                    PersecutionClassificationRecord.classifier_name == classifier_name
                )

            query = query.order_by(PersecutionClassificationRecord.classified_at.desc()).limit(1)

            return session.scalar(query)

    def _get_person_events(self, session: Session, person_id: int) -> Sequence[dict[str, Any]]:
        """Get all events linked to a person."""
        event_ids = session.scalars(
            select(PersonEventLinkRecord.event_id).where(
                PersonEventLinkRecord.person_id == person_id
            )
        ).all()

        if not event_ids:
            return []

        events = session.scalars(
            select(ExtractedEventRecord).where(ExtractedEventRecord.id.in_(event_ids))
        ).all()

        return [
            {
                "id": event.id,
                "event_type": event.event_type,
                "event_date": event.event_date,
                "confidence": event.confidence,
                "attributes": event.attributes,
            }
            for event in events
        ]

    def _get_person_articles(self, session: Session, person_id: int) -> Sequence[dict[str, Any]]:
        """Get all articles containing events linked to a person."""
        event_ids = session.scalars(
            select(PersonEventLinkRecord.event_id).where(
                PersonEventLinkRecord.person_id == person_id
            )
        ).all()

        if not event_ids:
            return []

        run_ids = session.scalars(
            select(ExtractedEventRecord.extraction_run_id)
            .where(ExtractedEventRecord.id.in_(event_ids))
            .distinct()
        ).all()

        article_ids = session.scalars(
            select(ArticleExtractionRunRecord.article_id)
            .where(ArticleExtractionRunRecord.id.in_(run_ids))
            .distinct()
        ).all()

        if not article_ids:
            return []

        articles = session.scalars(
            select(ParsedArticleRecord).where(ParsedArticleRecord.id.in_(article_ids))
        ).all()

        return [
            {
                "id": article.id,
                "title": article.title,
                "text": article.text,
                "published_at": article.published_at,
            }
            for article in articles
        ]

    def _save_classification(
        self,
        session: Session,
        classification: PersecutionClassification,
    ) -> None:
        """Save or update a classification in the database."""
        existing = session.scalar(
            select(PersecutionClassificationRecord).where(
                PersecutionClassificationRecord.person_id == classification.person_id,
                PersecutionClassificationRecord.classifier_name == classification.classifier_name,
                PersecutionClassificationRecord.classifier_version
                == classification.classifier_version,
            )
        )

        if existing:
            existing.status = classification.status
            existing.confidence = classification.confidence
            existing.reasons = classification.reasons
            existing.evidence_types = [et for et in classification.evidence_types]
            existing.classified_at = classification.classified_at or datetime.now(UTC)
        else:
            record = PersecutionClassificationRecord(
                person_id=classification.person_id,
                status=classification.status,
                confidence=classification.confidence,
                reasons=classification.reasons,
                evidence_types=[et for et in classification.evidence_types],
                classifier_name=classification.classifier_name,
                classifier_version=classification.classifier_version,
                classified_at=classification.classified_at or datetime.now(UTC),
            )
            session.add(record)
