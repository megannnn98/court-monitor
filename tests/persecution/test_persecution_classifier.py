"""Tests for the persecution classifier."""

from datetime import UTC, datetime
from typing import Any

import pytest

from persecution.classifier import RuleBasedPersecutionClassifier
from persecution.models import PersecutionClassificationStatus, PersecutionEvidenceType


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


def test_classifier_does_not_match_article_number_substrings() -> None:
    """Test that article 280 does not match unrelated numeric substrings."""
    classifier = RuleBasedPersecutionClassifier()

    classification = classifier.classify(
        person_id=1,
        events=[
            {
                "id": 1,
                "event_type": "charge",
                "event_date": datetime(2026, 1, 1, tzinfo=UTC),
                "confidence": 0.9,
                "attributes": {"charge": "ст. 1280 УК РФ"},
            }
        ],
        articles=[],
    )

    assert classification.status == PersecutionClassificationStatus.NON_POLITICAL
    assert PersecutionEvidenceType.POLITICAL_CHARGE not in classification.evidence_types


def test_classifier_detects_political_keywords_in_article() -> None:
    """Test that classifier detects political keywords in article text."""
    classifier = RuleBasedPersecutionClassifier()

    # POLITICAL needs a persecution event of the person (real-world validation v1).
    events: list[dict[str, Any]] = [
        {"id": 1, "event_type": "detention", "attributes": {}, "confidence": 0.7}
    ]

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


def test_classifier_does_not_treat_memorial_as_political_evidence() -> None:
    """A source name cannot turn an unrelated criminal case into a political one."""
    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1,
        events=[{"id": 1, "event_type": "sentence", "attributes": {"charge": "УК РФ ст. 158"}}],
        articles=[
            {
                "id": 1,
                "title": "Приговор по уголовному делу",
                "text": "Карточку опубликовал Мемориал.",
            }
        ],
    )

    assert classification.status == PersecutionClassificationStatus.NON_POLITICAL
    assert classification.evidence_types == []
    assert classification.reasons == []


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


@pytest.mark.parametrize(
    "text",
    [
        "Сергей Сидоров задержан за кражу велосипеда.",
        "Суд приговорил Олега Смирнова к трём годам колонии за кражу.",
        "Медиатор Пётр Ковалёв помог сторонам договориться о долге.",
    ],
)
def test_keywords_inside_other_words_are_not_evidence(text: str) -> None:
    """«гей» in «Сергей», «сми» in «Смирнов» must not become persecution reasons."""
    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1, events=[], articles=[{"id": 1, "title": "", "text": text}]
    )

    assert classification.evidence_types == []
    assert classification.reasons == []
    assert classification.status == PersecutionClassificationStatus.NON_POLITICAL


@pytest.mark.parametrize(
    ("text", "evidence"),
    [
        ("Гей-активиста задержали после пикета.", PersecutionEvidenceType.LGBT_PERSECUTION),
        ("Главного редактора СМИ вызвали на допрос.", PersecutionEvidenceType.JOURNALIST),
        ("Журналистку задержали на акции.", PersecutionEvidenceType.JOURNALIST),
        ("Антивоенного активиста оштрафовали.", PersecutionEvidenceType.ANTI_WAR_ACTIVITY),
    ],
)
def test_keywords_at_word_start_are_still_evidence(
    text: str, evidence: PersecutionEvidenceType
) -> None:
    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1, events=[], articles=[{"id": 1, "title": "", "text": text}]
    )

    assert evidence in classification.evidence_types


def test_political_context_without_an_own_event_is_uncertain_not_political() -> None:
    from persecution.classifier import RuleBasedPersecutionClassifier
    from persecution.models import PersecutionClassificationStatus

    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1,
        events=[],
        articles=[
            {
                "title": "",
                "text": "Решение вынес судья, который ранее признал антивоенное движение "
                "экстремистским, правозащитники назвали дело политическим преследованием.",
            }
        ],
    )

    assert classification.status is PersecutionClassificationStatus.UNCERTAIN


def test_news_outlet_attribution_is_not_persecution_evidence() -> None:
    """Real cases: «сообщили ОВД-Инфо», «пишет SOTAvision» counted as political and
    human-rights evidence for anyone with an event in the sentence."""
    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1,
        events=[{"id": 1, "event_type": "detention", "attributes": {}, "confidence": 0.7}],
        articles=[
            {
                "title": "",
                "text": "Мужчину задержали за мелкое хулиганство, сообщили ОВД-Инфо и SOTA, "
                "пишет «Медиазона».",
            }
        ],
    )

    assert classification.status is PersecutionClassificationStatus.NON_POLITICAL


@pytest.mark.parametrize(
    "charge",
    [
        "УК РФ ст. 212.1",
        "УК РФ ст. 282.3 ч. 1",
        "УК РФ ст. 282.4 ч. 1",
        "УК РФ ст. 330.1 ч. 2",
        "УК РФ ст. 354.1 ч. 4",
        "КоАП РФ ст. 20.3.3 ч. 1",
        "КоАП РФ ст. 20.33",
        "КоАП РФ ст. 19.34",
    ],
)
def test_well_known_political_articles_are_political_charges(charge: str) -> None:
    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1,
        events=[{"id": 1, "event_type": "charge", "attributes": {"charge": charge}}],
        articles=[],
    )

    assert classification.status is PersecutionClassificationStatus.POLITICAL


@pytest.mark.parametrize("charge", ["КоАП РФ ст. 282", "УК РФ ст. 20.3.3", "УК РФ ст. 19.3"])
def test_political_article_numbers_only_count_in_their_own_code(charge: str) -> None:
    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1,
        events=[{"id": 1, "event_type": "charge", "attributes": {"charge": charge}}],
        articles=[],
    )

    assert PersecutionEvidenceType.POLITICAL_CHARGE not in classification.evidence_types


@pytest.mark.parametrize(
    "text",
    [
        "Его осудили за госизмену.",
        "Суд признал его виновным в государственной измене.",
        "По версии следствия, он получил задание от сотрудника ГУР.",
        "Он передавал сведения СБУ.",
    ],
)
def test_treason_and_ukrainian_services_are_signs_of_political_persecution(text: str) -> None:
    """The customer counts treason and GUR/SBU cases as political persecution."""
    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1,
        events=[],
        articles=[{"title": "", "text": text}],
    )

    assert PersecutionEvidenceType.POLITICAL_ARTICLE in classification.evidence_types


def test_a_word_starting_like_an_intelligence_service_is_not_one() -> None:
    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1,
        events=[],
        articles=[{"title": "", "text": "Гуров и Сбуев пришли на заседание."}],
    )

    assert classification.evidence_types == []


def test_a_charge_listing_several_articles_is_political_if_one_of_them_is() -> None:
    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1,
        events=[
            {
                "id": 1,
                "event_type": "sentence",
                "attributes": {"charge": "УК РФ ст. 222.1 ч. 4; УК РФ ст. 275"},
            }
        ],
        articles=[],
    )

    assert classification.status == PersecutionClassificationStatus.POLITICAL
    # КоАП and УК numbers stay apart inside one listing.
    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1,
        events=[
            {
                "id": 1,
                "event_type": "sentence",
                "attributes": {"charge": "КоАП РФ ст. 20.1; УК РФ ст. 158"},
            }
        ],
        articles=[],
    )
    assert classification.status == PersecutionClassificationStatus.NON_POLITICAL
    classification = RuleBasedPersecutionClassifier().classify(
        person_id=1,
        events=[
            {
                "id": 1,
                "event_type": "sentence",
                "attributes": {"charge": "КоАП РФ ст. 20.1; УК РФ ст. 275"},
            }
        ],
        articles=[],
    )
    assert classification.status == PersecutionClassificationStatus.POLITICAL
