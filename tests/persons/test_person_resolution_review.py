"""Human review of ER v2 decisions on PostgreSQL."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.person_resolution_fixtures import seed_mentions, seed_person

from db.orm_models import (
    EntityMentionRecord,
    EventEntityMentionRecord,
    ExtractedEventRecord,
    PersonEventLinkRecord,
    PersonMergeRecord,
    PersonRecord,
    PersonResolutionDecisionRecord,
    ReviewRecordModel,
)
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.factory import build_person_resolution_service
from persons.resolution.review import (
    PersonResolutionReviewService,
    ResolutionReviewAction,
    ResolutionReviewStateError,
)

Action = ResolutionReviewAction


def _pending(session_factory: sessionmaker[Session], surface: str) -> tuple[int, int]:
    """Resolve `surface` into a pending review; returns (decision id, mention id)."""
    run_id, (mention_id,) = seed_mentions(session_factory, surface)
    with session_factory.begin() as session:
        event = ExtractedEventRecord(
            extraction_run_id=run_id,
            event_type="detention",
            start_offset=0,
            end_offset=len(surface),
            confidence=0.8,
            attributes={},
            extractor_name="test",
            extractor_version="1",
        )
        session.add(event)
        session.flush()
        session.add(
            EventEntityMentionRecord(event_id=event.id, mention_id=mention_id, role="subject")
        )
    service = build_person_resolution_service(session_factory, {})
    with session_factory.begin() as session:
        outcome = service.resolve_mention(session, session.get_one(EntityMentionRecord, mention_id))
    assert outcome is not None and outcome.decision_id is not None
    assert outcome.person_id is None
    return outcome.decision_id, mention_id


def _reviews(session_factory: sessionmaker[Session]) -> PersonResolutionReviewService:
    return PersonResolutionReviewService(SqlAlchemyPersonPersistence(session_factory))


def test_review_view_shows_structured_comparison_and_source(
    session_factory: sessionmaker[Session],
) -> None:
    ivan = seed_person(session_factory, "Иван Иванов")
    seed_person(session_factory, "Илья Иванов")
    decision_id, mention_id = _pending(session_factory, "И. Иванов")

    with session_factory() as session:
        view = _reviews(session_factory).get(session, decision_id)
        (listed,) = _reviews(session_factory).list_pending(session)

    assert listed.decision_id == decision_id
    assert view.mention_id == mention_id
    assert view.incoming_name == "И. Иванов"
    assert view.normalized_form == "и иванов"
    assert view.source.source_name == "ОВД-Инфо" and view.source.url
    assert view.review_id is not None
    assert "initials_only" in view.reasons
    by_id = {candidate.person_id: candidate for candidate in view.candidates}
    assert by_id[ivan].surname.match == "exact"
    assert by_id[ivan].given_name.match == "initial_compatible"
    assert by_id[ivan].conflicts == []
    assert by_id[ivan].person_status == "active"
    assert by_id[ivan].score_rules


def test_link_to_person_applies_once_and_links_events(
    session_factory: sessionmaker[Session],
) -> None:
    ivan = seed_person(session_factory, "Иван Иванов")
    seed_person(session_factory, "Илья Иванов")
    decision_id, mention_id = _pending(session_factory, "И. Иванов")
    reviews = _reviews(session_factory)

    with session_factory.begin() as session:
        result = reviews.apply(
            session, decision_id, Action.LINK_TO_PERSON, person_id=ivan, note="same court case"
        )

    assert (result.person_id, result.events_linked) == (ivan, 1)
    with session_factory() as session:
        assert session.get_one(EntityMentionRecord, mention_id).person_id == ivan
        decision = session.get_one(PersonResolutionDecisionRecord, decision_id)
        assert (decision.status, decision.review_action, decision.selected_person_id) == (
            "reviewed",
            "link_to_person",
            ivan,
        )
        assert decision.reviewed_at is not None
        review = session.scalars(select(ReviewRecordModel)).one()
        assert review.decision == "approved"
        assert session.scalars(select(PersonEventLinkRecord.person_id)).all() == [ivan]

    with session_factory.begin() as session, pytest.raises(ResolutionReviewStateError):
        reviews.apply(session, decision_id, Action.LINK_TO_PERSON, person_id=ivan)


def test_create_new_person_from_review(session_factory: sessionmaker[Session]) -> None:
    seed_person(session_factory, "Иван Иванов")
    seed_person(session_factory, "Илья Иванов")
    decision_id, mention_id = _pending(session_factory, "И. Иванов")

    with session_factory.begin() as session:
        result = _reviews(session_factory).apply(session, decision_id, Action.CREATE_NEW_PERSON)

    assert result.created_person
    with session_factory() as session:
        person = session.get_one(PersonRecord, result.person_id)
        assert person.normalized_name == "И. Иванов"
        assert session.get_one(EntityMentionRecord, mention_id).person_id == person.id


def test_create_new_person_refuses_an_existing_matching_key(
    session_factory: sessionmaker[Session],
) -> None:
    seed_person(session_factory, "Иван Иванов")
    seed_person(session_factory, "Илья Иванов")
    decision_id, _ = _pending(session_factory, "И. Иванов")
    seed_person(session_factory, "И. Иванов")  # appeared after the decision

    with session_factory.begin() as session, pytest.raises(ResolutionReviewStateError):
        _reviews(session_factory).apply(session, decision_id, Action.CREATE_NEW_PERSON)


def test_merge_persons_is_explicit_and_audited(session_factory: sessionmaker[Session]) -> None:
    target = seed_person(session_factory, "Иван Иванович Иванов")
    source = seed_person(session_factory, "Иванов Иван Иванович")
    decision_id, mention_id = _pending(session_factory, "Иван Иванович Иваноф")

    with session_factory.begin() as session:
        result = _reviews(session_factory).apply(
            session,
            decision_id,
            Action.MERGE_PERSONS,
            person_id=target,
            source_person_id=source,
            note="duplicates",
        )

    assert result.merge_record_id is not None
    with session_factory() as session:
        merged = session.get_one(PersonRecord, source)
        assert (merged.status, merged.merged_into_id) == ("merged", target)
        record = session.get_one(PersonMergeRecord, result.merge_record_id)
        assert (record.source_person_id, record.target_person_id, record.status) == (
            source,
            target,
            "applied",
        )
        assert "decision" in (record.reason or "")
        assert session.get_one(EntityMentionRecord, mention_id).person_id == target


def test_keep_separate_links_without_merging(session_factory: sessionmaker[Session]) -> None:
    first = seed_person(session_factory, "Иван Иванович Иванов")
    second = seed_person(session_factory, "Иванов Иван Иванович")
    decision_id, _ = _pending(session_factory, "Иван Иванович Иваноф")

    with session_factory.begin() as session:
        _reviews(session_factory).apply(
            session, decision_id, Action.KEEP_SEPARATE, person_id=second
        )

    with session_factory() as session:
        statuses = session.scalars(
            select(PersonRecord.status).where(PersonRecord.id.in_([first, second]))
        ).all()
        assert session.scalars(select(PersonMergeRecord)).all() == []
    assert statuses == ["active", "active"]


def test_review_rejects_inactive_or_missing_person(session_factory: sessionmaker[Session]) -> None:
    seed_person(session_factory, "Иван Иванов")
    seed_person(session_factory, "Илья Иванов")
    decision_id, _ = _pending(session_factory, "И. Иванов")
    reviews = _reviews(session_factory)

    with session_factory.begin() as session, pytest.raises(ResolutionReviewStateError):
        reviews.apply(session, decision_id, Action.LINK_TO_PERSON, person_id=999_999)
    with session_factory.begin() as session, pytest.raises(ResolutionReviewStateError):
        reviews.apply(session, decision_id, Action.LINK_TO_PERSON)
