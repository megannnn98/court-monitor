from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    ArticleExtractionRunRecord,
    EntityMentionRecord,
    ExtractedEventRecord,
    ParsedArticleRecord,
    PersonAliasRecord,
    PersonEventLinkRecord,
    PersonMergeRecord,
    PersonRecord,
    Source,
    SourceDocument,
)
from persons.models import AliasOrigin, MergeStatus, PersonStatus
from persons.persistence import PersonMergeConflictError, SqlAlchemyPersonPersistence


def test_create_person_persists_and_returns_id(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)

    person_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )

    assert person_id > 0
    with session_factory() as session:
        person = session.scalar(select(PersonRecord).where(PersonRecord.id == person_id))
    assert person is not None
    assert person.canonical_name == "Иван Иванов"
    assert person.status == PersonStatus.ACTIVE.value


def test_create_alias_links_to_person(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )

    alias_id = persistence.create_alias(
        person_id=person_id,
        surface_text="Ивана Иванова",
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )

    assert alias_id > 0
    with session_factory() as session:
        alias = session.scalar(select(PersonAliasRecord).where(PersonAliasRecord.id == alias_id))
    assert alias is not None
    assert alias.person_id == person_id
    assert alias.surface_text == "Ивана Иванова"


def test_find_person_by_matching_key_returns_person(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )

    found_id = persistence.find_person_by_matching_key("иваниванов")

    assert found_id == person_id


def test_find_person_by_matching_key_returns_none_when_not_found(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)

    found_id = persistence.find_person_by_matching_key("несуществующий")

    assert found_id is None


def test_merge_persons_updates_status_and_creates_record(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    source_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )
    target_id = persistence.create_person(
        canonical_name="И. И. Иванов",
        normalized_name="Иван Иванов",
        matching_key="иииванов",
    )

    merge_id = persistence.merge_persons(
        source_person_id=source_id,
        target_person_id=target_id,
        reason="same person",
    )

    assert merge_id > 0
    with session_factory() as session:
        source = session.scalar(select(PersonRecord).where(PersonRecord.id == source_id))
        merge_record = session.scalar(
            select(PersonMergeRecord).where(PersonMergeRecord.id == merge_id)
        )
    assert source is not None
    assert source.status == PersonStatus.MERGED.value
    assert source.merged_into_id == target_id
    assert merge_record is not None
    assert merge_record.status == MergeStatus.APPLIED.value


def test_merge_persons_moves_links_mentions_and_aliases_to_target(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    source_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )
    target_id = persistence.create_person(
        canonical_name="Иван Петрович Иванов",
        normalized_name="Иван Петрович Иванов",
        matching_key="иванпетровичиванов",
    )
    persistence.create_alias(
        person_id=source_id,
        surface_text="Ивана Иванова",
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )

    with session_factory() as session:
        source = Source(name="test", base_url="https://example.com")
        session.add(source)
        session.flush()
        document = SourceDocument(
            source_id=source.id,
            external_id="merge-links",
            canonical_url="https://example.com/merge-links",
            fetched_at=datetime.now(UTC),
            content_type="text/html",
            raw_content=b"",
        )
        session.add(document)
        session.flush()
        article = ParsedArticleRecord(
            document_id=document.id,
            title="Test",
            published_at=datetime.now(UTC),
            text="Ивана Иванова задержали.",
        )
        session.add(article)
        session.flush()
        run = ArticleExtractionRunRecord(
            article_id=article.id,
            article_content_hash="mergehash",
            extractor_name="test",
            extractor_version="1",
            normalizer_version="1",
            status="succeeded",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        mention = EntityMentionRecord(
            extraction_run_id=run.id,
            entity_type="person",
            surface_text="Ивана Иванова",
            normalized_text="Иван Иванов",
            start_offset=0,
            end_offset=13,
            confidence=0.9,
            normalized_data={"matching_key": "иваниванов"},
            extractor_name="test",
            extractor_version="1",
            normalizer_version="1",
            person_id=source_id,
        )
        session.add(mention)
        event = ExtractedEventRecord(
            extraction_run_id=run.id,
            event_type="detention",
            event_date=datetime(2026, 1, 1, tzinfo=UTC),
            start_offset=0,
            end_offset=13,
            confidence=0.9,
            attributes={},
            extractor_name="test",
            extractor_version="1",
        )
        session.add(event)
        session.flush()
        session.add(
            PersonEventLinkRecord(
                person_id=source_id,
                event_id=event.id,
                role="target",
                confidence=0.9,
            )
        )
        session.commit()

    persistence.merge_persons(
        source_person_id=source_id,
        target_person_id=target_id,
        reason="same person",
    )

    with session_factory() as session:
        source_links = session.scalars(
            select(PersonEventLinkRecord).where(PersonEventLinkRecord.person_id == source_id)
        ).all()
        target_links = session.scalars(
            select(PersonEventLinkRecord).where(PersonEventLinkRecord.person_id == target_id)
        ).all()
        retrieved_mention = session.scalar(select(EntityMentionRecord))
        source_aliases = session.scalars(
            select(PersonAliasRecord).where(PersonAliasRecord.person_id == source_id)
        ).all()
        target_aliases = session.scalars(
            select(PersonAliasRecord).where(PersonAliasRecord.person_id == target_id)
        ).all()

    assert source_links == []
    assert len(target_links) == 1
    assert retrieved_mention is not None
    assert retrieved_mention.person_id == target_id
    assert source_aliases == []
    assert len(target_aliases) == 1


def test_list_aliases_for_person_returns_all_aliases(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )
    persistence.create_alias(
        person_id=person_id,
        surface_text="Ивана Иванова",
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )
    persistence.create_alias(
        person_id=person_id,
        surface_text="И. Иванов",
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        origin=AliasOrigin.MANUAL,
        confidence=1.0,
    )

    aliases = persistence.list_aliases_for_person(person_id)

    assert len(aliases) == 2
    assert all(alias.person_id == person_id for alias in aliases)


def test_duplicate_alias_raises_error(
    session_factory: sessionmaker[Session],
) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )
    persistence.create_alias(
        person_id=person_id,
        surface_text="Иван Иванов",
        normalized_text="Иван Иванов",
        matching_key="иваниванов",
        origin=AliasOrigin.EXTRACTION,
        confidence=0.9,
    )

    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        persistence.create_alias(
            person_id=person_id,
            surface_text="Иван Иванов",
            normalized_text="Иван Иванов",
            matching_key="иваниванов",
            origin=AliasOrigin.MANUAL,
            confidence=1.0,
        )


def test_merge_person_into_itself_is_rejected(session_factory: sessionmaker[Session]) -> None:
    persistence = SqlAlchemyPersonPersistence(session_factory)
    person_id = persistence.create_person(
        canonical_name="Иван Иванов",
        normalized_name="Иван Иванов",
        matching_key="иваниванов",
    )

    with pytest.raises(PersonMergeConflictError):
        persistence.merge_persons(source_person_id=person_id, target_person_id=person_id)

    with session_factory() as session:
        person = session.get_one(PersonRecord, person_id)
        assert (person.status, person.merged_into_id) == (PersonStatus.ACTIVE.value, None)
        assert session.scalars(select(PersonMergeRecord)).all() == []
