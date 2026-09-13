"""Tests for the persecution classifier."""

from datetime import UTC, datetime
from typing import Any

from persecution_classifier import RuleBasedPersecutionClassifier
from persecution_models import PersecutionClassificationStatus, PersecutionEvidenceType


def test_classifier_detects_political_charge() -> None:
    """Test that classifier detects political charges."""
    classifier = RuleBasedPersecutionClassifier()

    events = [
        {
            "id": 1,
            "event_type": "charge",
            "event_date": datetime(2026, 1, 1, tzinfo=UTC),
            "confidence": 0.9,
            "attributes": {"charge": "ст. 280 УК РФ"},
        }
    ]

    articles: list[dict[str, Any]] = []

    classification = classifier.classify(
        person_id=1,
        events=events,
        articles=articles,
    )

    assert classification.status == PersecutionClassificationStatus.POLITICAL
    assert classification.confidence >= 0.7
    assert PersecutionEvidenceType.POLITICAL_CHARGE in classification.evidence_types
    assert len(classification.reasons) > 0


def test_classifier_detects_political_keywords_in_article() -> None:
    """Test that classifier detects political keywords in article text."""
    classifier = RuleBasedPersecutionClassifier()

    events: list[dict[str, Any]] = []

    articles = [
        {
            "id": 1,
            "title": "Правозащитник задержан на митинге",
            "text": "Известный правозащитник был задержан на антивоенном митинге в Москве.",
            "published_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    ]

    classification = classifier.classify(
        person_id=1,
        events=events,
        articles=articles,
    )

    assert classification.status == PersecutionClassificationStatus.POLITICAL
    assert PersecutionEvidenceType.POLITICAL_ARTICLE in classification.evidence_types
    assert PersecutionEvidenceType.HUMAN_RIGHTS_DEFENDER in classification.evidence_types
    assert PersecutionEvidenceType.ANTI_WAR_ACTIVITY in classification.evidence_types


def test_classifier_detects_religious_persecution() -> None:
    """Test that classifier detects religious persecution."""
    classifier = RuleBasedPersecutionClassifier()

    events: list[dict[str, Any]] = []

    articles = [
        {
            "id": 1,
            "title": "Свидетели Иеговы преследуются",
            "text": "Верующие Свидетели Иеговы подвергаются преследованиям по всей России.",
            "published_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    ]

    classification = classifier.classify(
        person_id=1,
        events=events,
        articles=articles,
    )

    assert PersecutionEvidenceType.RELIGIOUS_PERSECUTION in classification.evidence_types


def test_classifier_detects_lgbt_persecution() -> None:
    """Test that classifier detects LGBT persecution."""
    classifier = RuleBasedPersecutionClassifier()

    events: list[dict[str, Any]] = []

    articles = [
        {
            "id": 1,
            "title": "ЛГБТ активист задержан",
            "text": "Известный ЛГБТ активист был задержан полицией.",
            "published_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    ]

    classification = classifier.classify(
        person_id=1,
        events=events,
        articles=articles,
    )

    assert PersecutionEvidenceType.LGBT_PERSECUTION in classification.evidence_types


def test_classifier_returns_non_political_for_neutral_content() -> None:
    """Test that classifier returns non-political for neutral content."""
    classifier = RuleBasedPersecutionClassifier()

    events = [
        {
            "id": 1,
            "event_type": "arrest",
            "event_date": datetime(2026, 1, 1, tzinfo=UTC),
            "confidence": 0.9,
            "attributes": {"charge": "ст. 158 УК РФ (кража)"},
        }
    ]

    articles = [
        {
            "id": 1,
            "title": "Местный житель задержан",
            "text": "Полиция задержала подозреваемого в краже.",
            "published_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    ]

    classification = classifier.classify(
        person_id=1,
        events=events,
        articles=articles,
    )

    assert classification.status == PersecutionClassificationStatus.NON_POLITICAL


def test_classifier_handles_empty_inputs() -> None:
    """Test that classifier handles empty inputs gracefully."""
    classifier = RuleBasedPersecutionClassifier()

    classification = classifier.classify(
        person_id=1,
        events=[],
        articles=[],
    )

    assert classification.status == PersecutionClassificationStatus.NON_POLITICAL
    assert classification.confidence == 0.0
    assert len(classification.evidence_types) == 0
    assert len(classification.reasons) == 0


def test_classifier_multiple_evidence_types() -> None:
    """Test that classifier can detect multiple evidence types."""
    classifier = RuleBasedPersecutionClassifier()

    events = [
        {
            "id": 1,
            "event_type": "charge",
            "event_date": datetime(2026, 1, 1, tzinfo=UTC),
            "confidence": 0.9,
            "attributes": {"charge": "ст. 282 УК РФ"},
        }
    ]

    articles = [
        {
            "id": 1,
            "title": "Журналист обвиняется в экстремизме",
            "text": "Известный журналист и правозащитник обвиняется по статье 282 УК РФ.",
            "published_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    ]

    classification = classifier.classify(
        person_id=1,
        events=events,
        articles=articles,
    )

    assert classification.status == PersecutionClassificationStatus.POLITICAL
    assert PersecutionEvidenceType.POLITICAL_CHARGE in classification.evidence_types
    assert PersecutionEvidenceType.POLITICAL_ARTICLE in classification.evidence_types
    assert PersecutionEvidenceType.JOURNALIST in classification.evidence_types
    assert PersecutionEvidenceType.HUMAN_RIGHTS_DEFENDER in classification.evidence_types
    assert len(classification.evidence_types) >= 4


def test_classifier_confidence_scales_with_evidence() -> None:
    """Test that classifier confidence increases with more evidence."""
    classifier = RuleBasedPersecutionClassifier()

    # Single evidence type
    events_1 = [
        {
            "id": 1,
            "event_type": "charge",
            "event_date": datetime(2026, 1, 1, tzinfo=UTC),
            "confidence": 0.9,
            "attributes": {"charge": "ст. 280 УК РФ"},
        }
    ]

    classification_1 = classifier.classify(
        person_id=1,
        events=events_1,
        articles=[],
    )

    # Multiple evidence types
    events_2 = [
        {
            "id": 1,
            "event_type": "charge",
            "event_date": datetime(2026, 1, 1, tzinfo=UTC),
            "confidence": 0.9,
            "attributes": {"charge": "ст. 280 УК РФ"},
        },
        {
            "id": 2,
            "event_type": "sentence",
            "event_date": datetime(2026, 1, 2, tzinfo=UTC),
            "confidence": 0.95,
            "attributes": {"sentence_years": 3},
        },
    ]

    articles_2 = [
        {
            "id": 1,
            "title": "Правозащитник осужден",
            "text": "Известный правозащитник осужден по политическим мотивам.",
            "published_at": datetime(2026, 1, 3, tzinfo=UTC),
        }
    ]

    classification_2 = classifier.classify(
        person_id=2,
        events=events_2,
        articles=articles_2,
    )

    assert classification_2.confidence >= classification_1.confidence
