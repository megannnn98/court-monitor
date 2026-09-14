"""Tests for person-scoped persecution classification."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersonEventLinkRecord,
    PersonRecord,
    Source,
    SourceDocument,
)
from persecution.classification_service import PersecutionClassificationService
from persecution.models import PersecutionClassificationStatus, PersecutionEvidenceType


def _create_article(session: Session, text: str, external_id: str) -> int:
    source = Source(name="test", base_url=f"https://example.com/{external_id}")
    session.add(source)
    session.flush()
    document = SourceDocument(
        source_id=source.id,
        external_id=external_id,
        canonical_url=f"https://example.com/{external_id}",
        fetched_at=datetime.now(UTC),
        content_type="text/html",
        raw_content=b"",
    )
    session.add(document)
    session.flush()
    article = ParsedArticleRecord(
        document_id=document.id,
        title="Test article",
        published_at=datetime.now(UTC),
        text=text,
    )
    session.add(article)
    session.flush()
    return article.id


def _create_run(session: Session, article_id: int) -> int:
    run = ArticleExtractionRunRecord(
        article_id=article_id,
        article_content_hash="hash",
        extractor_name="test",
        extractor_version="1",
        normalizer_version="1",
        status="succeeded",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    return run.id


def _create_person(session: Session, canonical_name: str, matching_key: str) -> int:
    person = PersonRecord(
        canonical_name=canonical_name,
        normalized_name=canonical_name,
        matching_key=matching_key,
        status="active",
    )
    session.add(person)
    session.flush()
    return person.id


def _create_person_mention(
    session: Session,
    *,
    run_id: int,
    person_id: int,
    surface_text: str,
    start_offset: int,
    end_offset: int,
) -> int:
    mention = EntityMentionRecord(
        extraction_run_id=run_id,
        entity_type="person",
        surface_text=surface_text,
        normalized_text=surface_text,
        start_offset=start_offset,
        end_offset=end_offset,
        confidence=0.9,
        normalized_data={"full_name": surface_text, "matching_key": surface_text.lower()},
        extractor_name="test",
        extractor_version="1",
        normalizer_version="1",
        person_id=person_id,
    )
    session.add(mention)
    session.flush()
    return mention.id


def _create_legal_reference_mention(
    session: Session,
    *,
    run_id: int,
    surface_text: str,
    start_offset: int,
    end_offset: int,
) -> int:
    mention = EntityMentionRecord(
        extraction_run_id=run_id,
        entity_type="legal_reference",
        surface_text=surface_text,
        normalized_text=surface_text,
        start_offset=start_offset,
        end_offset=end_offset,
        confidence=0.95,
        normalized_data={"code": "УК РФ"},
        extractor_name="test",
        extractor_version="1",
        normalizer_version="1",
    )
    session.add(mention)
    session.flush()
    return mention.id


def _create_event(
    session: Session,
    *,
    run_id: int,
    event_type: str,
    start_offset: int,
    end_offset: int,
) -> int:
    event = ExtractedEventRecord(
        extraction_run_id=run_id,
        event_type=event_type,
        event_date=None,
        start_offset=start_offset,
        end_offset=end_offset,
        confidence=0.9,
        attributes={},
        extractor_name="test",
        extractor_version="1",
    )
    session.add(event)
    session.flush()
    return event.id


def _link_person_event(session: Session, person_id: int, event_id: int, role: str) -> None:
    session.add(
        PersonEventLinkRecord(
            person_id=person_id,
            event_id=event_id,
            role=role,
            confidence=0.9,
        )
    )
    session.flush()


def test_classification_does_not_leak_evidence_between_people_in_same_article(
    session_factory: sessionmaker[Session],
) -> None:
    """Two people in one article: political context near one must not spill
    onto the other, even though both mentions are in the same article text.
    """
    filler = "Ничего интересного тут нет. " * 60  # ~1740 chars of neutral padding
    segment1 = "Иванов задержан за кражу."
    segment2 = "Известный правозащитник Петров задержан на антивоенном пикете."
    text = segment1 + filler + segment2

    with session_factory() as session:
        article_id = _create_article(session, text, external_id="two-people")
        run_id = _create_run(session, article_id)

        person1_id = _create_person(session, "Иванов", "иванов")
        person2_id = _create_person(session, "Петров", "петров")

        start1 = text.index("Иванов")
        _create_person_mention(
            session,
            run_id=run_id,
            person_id=person1_id,
            surface_text="Иванов",
            start_offset=start1,
            end_offset=start1 + len("Иванов"),
        )
        event1_id = _create_event(
            session,
            run_id=run_id,
            event_type="detention",
            start_offset=start1,
            end_offset=start1 + len("Иванов"),
        )
        _link_person_event(session, person1_id, event1_id, role="target")

        start2 = text.index("Петров")
        _create_person_mention(
            session,
            run_id=run_id,
            person_id=person2_id,
            surface_text="Петров",
            start_offset=start2,
            end_offset=start2 + len("Петров"),
        )
        event2_id = _create_event(
            session,
            run_id=run_id,
            event_type="detention",
            start_offset=start2,
            end_offset=start2 + len("Петров"),
        )
        _link_person_event(session, person2_id, event2_id, role="target")
        session.commit()

    service = PersecutionClassificationService(session_factory)

    classification1 = service.classify_person(person1_id)
    classification2 = service.classify_person(person2_id)

    assert classification1.status == PersecutionClassificationStatus.NON_POLITICAL
    assert PersecutionEvidenceType.ANTI_WAR_ACTIVITY not in classification1.evidence_types

    assert classification2.status == PersecutionClassificationStatus.POLITICAL
    assert PersecutionEvidenceType.ANTI_WAR_ACTIVITY in classification2.evidence_types


def test_classification_links_nearby_legal_reference_to_event_as_charge(
    session_factory: sessionmaker[Session],
) -> None:
    """A legal_reference mention near a person's event becomes that event's
    `attributes["charge"]`, so a political article code is picked up as
    POLITICAL_CHARGE evidence even though the extractor never set "charge".
    """
    text = "Обвинили Сидорова по ст. 282 УК РФ за экстремизм."

    with session_factory() as session:
        article_id = _create_article(session, text, external_id="legal-ref-link")
        run_id = _create_run(session, article_id)
        person_id = _create_person(session, "Сидоров", "сидоров")

        event_start = text.index("Обвинили")
        event_end = event_start + len("Обвинили")
        event_id = _create_event(
            session,
            run_id=run_id,
            event_type="charge",
            start_offset=event_start,
            end_offset=event_end,
        )
        _link_person_event(session, person_id, event_id, role="target")

        legal_start = text.index("ст. 282 УК РФ")
        _create_legal_reference_mention(
            session,
            run_id=run_id,
            surface_text="ст. 282 УК РФ",
            start_offset=legal_start,
            end_offset=legal_start + len("ст. 282 УК РФ"),
        )
        session.commit()

    service = PersecutionClassificationService(session_factory)

    classification = service.classify_person(person_id)

    assert PersecutionEvidenceType.POLITICAL_CHARGE in classification.evidence_types
    assert classification.status == PersecutionClassificationStatus.POLITICAL


def _seed_people_in_text(
    session_factory: sessionmaker[Session], text: str, names: list[str]
) -> list[int]:
    with session_factory() as session:
        article_id = _create_article(session, text, external_id="adjacent")
        run_id = _create_run(session, article_id)
        person_ids = []
        for name in names:
            person_id = _create_person(session, name, name.lower())
            start = text.index(name)
            _create_person_mention(
                session,
                run_id=run_id,
                person_id=person_id,
                surface_text=name,
                start_offset=start,
                end_offset=start + len(name),
            )
            event_id = _create_event(
                session,
                run_id=run_id,
                event_type="detention",
                start_offset=start,
                end_offset=start + len(name),
            )
            _link_person_event(session, person_id, event_id, role="target")
            person_ids.append(person_id)
        session.commit()
    return person_ids


def test_political_context_of_the_next_persons_sentence_does_not_leak_back(
    session_factory: sessionmaker[Session],
) -> None:
    """Short article, adjacent sentences: the window stops at another person's sentence."""
    text = (
        "Утром полиция задержала Егорова за кражу велосипеда. "
        "Вечером на одиночном пикете против войны задержали Никитина."
    )
    egorov, nikitin = _seed_people_in_text(session_factory, text, ["Егорова", "Никитина"])
    service = PersecutionClassificationService(session_factory)

    assert service.classify_person(egorov).status != PersecutionClassificationStatus.POLITICAL
    assert service.classify_person(nikitin).status == PersecutionClassificationStatus.POLITICAL


def test_context_in_a_following_sentence_without_other_persons_still_counts(
    session_factory: sessionmaker[Session],
) -> None:
    text = (
        "Сидорова задержали на митинге в центре города. "
        "Правозащитники считают дело политически мотивированным и антивоенным."
    )
    [sidorov] = _seed_people_in_text(session_factory, text, ["Сидорова"])

    classification = PersecutionClassificationService(session_factory).classify_person(sidorov)

    assert classification.status == PersecutionClassificationStatus.POLITICAL
