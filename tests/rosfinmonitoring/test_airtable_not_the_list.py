"""The Airtable list must not become the basis of the products query.

Three doors led there and each is shut separately, so closing one does not leave
another open:

1. the candidates page picks a snapshot by default (`latest_snapshot_id`);
2. an operator can pass a snapshot id in the URL or the API;
3. a manual sync changes the Airtable list, and the probable matches must follow at
   once rather than at the next monitoring run.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.airtable_fakes import FakeAirtable, fake_source
from support.person_resolution_fixtures import seed_person

from airtable.client import AirtableRecord
from airtable.models import TABLES
from airtable.service import AirtableSyncService
from candidates.models import DEFAULT_MIN_PERSECUTION_CONFIDENCE, RosfinmonitoringStatus
from candidates.service import CandidateQueryService
from db.orm_models import RosfinMatchRecord, RosfinmonitoringSnapshotRecord
from rosfinmonitoring.matcher_models import RosfinMatchStatus
from rosfinmonitoring.models import AIRTABLE_SNAPSHOT_SOURCE_URL
from rosfinmonitoring.parser import _create_matching_key, _normalize_name
from web.candidate_rows import latest_snapshot_id

PERSON = "Петров Петр Петрович"
OFFICIAL_URL = "https://www.fedsfm.ru/documents/terrorists-catalog-portal-act"


def _official(session_factory: sessionmaker[Session], *, date: datetime) -> int:
    with session_factory.begin() as session:
        from db.orm_models import RosfinmonitoringEntryRecord

        seed = __import__("support.db_fixtures", fromlist=["DatabaseSeeder"]).DatabaseSeeder(
            session
        )
        snapshot_id = seed.snapshot(content_hash="official")
        snapshot = session.get_one(RosfinmonitoringSnapshotRecord, snapshot_id)
        snapshot.source_url = OFFICIAL_URL
        snapshot.snapshot_date = date
        normalized = _normalize_name(PERSON)
        session.add(
            RosfinmonitoringEntryRecord(
                snapshot_id=snapshot_id,
                full_name=PERSON,
                normalized_name=normalized,
                matching_key=_create_matching_key(normalized),
                status="active",
                raw_data={},
            )
        )
    return snapshot_id


def _absent_officially(
    session_factory: sessionmaker[Session], person_id: int, official: int
) -> None:
    """The published list does not have them: a confirmed absence to soften later."""
    with session_factory.begin() as session:
        session.add(
            RosfinMatchRecord(
                person_id=person_id,
                snapshot_id=official,
                status=RosfinMatchStatus.NOT_MATCHED.value,
                confidence=0.8,
                candidate_entries=[],
                reasons=[],
                matched_at=datetime.now(UTC),
                matcher_name="rule-based-rosfinmonitoring-matcher",
                matcher_version="1.3.0",
            )
        )


def _classify(session_factory: sessionmaker[Session], person_id: int) -> None:
    from support.db_fixtures import DatabaseSeeder

    with session_factory.begin() as session:
        DatabaseSeeder(session).classification(
            person_id, "political", DEFAULT_MIN_PERSECUTION_CONFIDENCE
        )


def _service(session_factory: sessionmaker[Session], fake: FakeAirtable) -> AirtableSyncService:
    return AirtableSyncService(session_factory, fake_source(fake))


class TestTheSnapshotTheProductsQueryUses:
    def test_a_newer_airtable_snapshot_is_not_chosen_by_default(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, date=datetime(2024, 1, 1, tzinfo=UTC))
        # Synced today, so by date alone it is the newest snapshot in the table.
        fake = FakeAirtable({"RFM": [AirtableRecord("recA", {"full_name": "Кто-то Другой"})]})
        _service(session_factory, fake).sync()
        with session_factory() as session:
            airtable_id = session.scalar(
                select(RosfinmonitoringSnapshotRecord.id).where(
                    RosfinmonitoringSnapshotRecord.source_url == AIRTABLE_SNAPSHOT_SOURCE_URL
                )
            )
            assert session.get_one(RosfinmonitoringSnapshotRecord, airtable_id).snapshot_date > (
                datetime(2024, 1, 1, tzinfo=UTC)
            )
        with session_factory() as session:
            assert latest_snapshot_id(session) == official

    def test_the_products_query_refuses_the_airtable_snapshot(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        _official(session_factory, date=datetime(2024, 1, 1, tzinfo=UTC))
        fake = FakeAirtable({"RFM": [AirtableRecord("recA", {"full_name": "Кто-то Другой"})]})
        _service(session_factory, fake).sync()
        with session_factory() as session:
            airtable_id = session.scalar(
                select(RosfinmonitoringSnapshotRecord.id).where(
                    RosfinmonitoringSnapshotRecord.source_url == AIRTABLE_SNAPSHOT_SOURCE_URL
                )
            )
        # Asked for by id and refused: the Airtable list has no official `not_matched`
        # rows, so a person would look confirmed absent from a list the state never
        # published.
        with pytest.raises(ValueError, match="Airtable"):
            CandidateQueryService(session_factory).get_candidates(airtable_id)

    def test_a_person_officially_absent_is_a_candidate_against_the_official_list(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, date=datetime(2024, 1, 1, tzinfo=UTC))
        person = seed_person(session_factory, PERSON)
        _classify(session_factory, person)
        _absent_officially(session_factory, person, official)
        # The Airtable list has him; the official one does not.
        _service(
            session_factory,
            FakeAirtable({"RFM": [AirtableRecord("recA", {"full_name": PERSON})]}),
        ).sync()

        result = CandidateQueryService(session_factory).get_candidates(official)

        # Only because the probable match followed the sync immediately; without that
        # he would still be here, waiting for a monitoring run.
        assert result.candidates == []

    def test_removing_the_name_from_airtable_makes_them_a_candidate_again(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, date=datetime(2024, 1, 1, tzinfo=UTC))
        person = seed_person(session_factory, PERSON)
        _classify(session_factory, person)
        _absent_officially(session_factory, person, official)
        fake = FakeAirtable({"RFM": [AirtableRecord("recA", {"full_name": PERSON})]})
        service = _service(session_factory, fake)
        service.sync()
        assert CandidateQueryService(session_factory).get_candidates(official).candidates == []

        # The operator deleted the row in Airtable and pressed the button again.
        fake.tables["RFM"] = []
        service.sync()

        result = CandidateQueryService(session_factory).get_candidates(official)
        assert [c.person_id for c in result.candidates] == [person]
        assert result.candidates[0].rosfinmonitoring_status is RosfinmonitoringStatus.NOT_MATCHED

    def test_a_failing_rfm_table_leaves_probable_matches_untouched(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        from airtable.client import AirtableError

        official = _official(session_factory, date=datetime(2024, 1, 1, tzinfo=UTC))
        person = seed_person(session_factory, PERSON)
        _classify(session_factory, person)
        _absent_officially(session_factory, person, official)
        _service(
            session_factory,
            FakeAirtable({"RFM": [AirtableRecord("recA", {"full_name": PERSON})]}),
        ).sync()

        # The list could not be read: the previous state stands rather than being
        # wiped by a sync that never got the data.
        report = _service(
            session_factory, FakeAirtable({"RFM": AirtableError("Airtable answered 500")})
        ).sync()

        assert report.tables["rfm_persons"].error is not None
        assert CandidateQueryService(session_factory).get_candidates(official).candidates == []

    def test_a_sync_with_no_official_list_refreshes_nothing_but_does_not_fail(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        report = _service(
            session_factory, FakeAirtable({"RFM": [AirtableRecord("recA", {"full_name": PERSON})]})
        ).sync()
        assert report.status == "success"
        assert all(name in report.tables for name in TABLES)


class TestTheSyncIsTheFreshnessBoundary:
    def test_a_second_sync_keeps_the_candidate_out(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, date=datetime(2024, 1, 1, tzinfo=UTC))
        person = seed_person(session_factory, PERSON)
        _classify(session_factory, person)
        _absent_officially(session_factory, person, official)
        service = _service(
            session_factory,
            FakeAirtable({"RFM": [AirtableRecord("recA", {"full_name": PERSON})]}),
        )
        for _ in range(3):
            service.sync()
            assert CandidateQueryService(session_factory).get_candidates(official).candidates == []

    def test_a_name_the_official_list_has_is_never_a_candidate(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, date=datetime(2024, 1, 1, tzinfo=UTC))
        person = seed_person(session_factory, PERSON)
        _classify(session_factory, person)
        with session_factory.begin() as session:
            session.add(
                RosfinMatchRecord(
                    person_id=person,
                    snapshot_id=official,
                    status=RosfinMatchStatus.MATCHED.value,
                    confidence=1.0,
                    matched_entry_name=PERSON,
                    candidate_entries=[],
                    reasons=[],
                    matched_at=datetime.now(UTC),
                    matcher_name="rule-based-rosfinmonitoring-matcher",
                    matcher_version="1.3.0",
                )
            )
        _service(
            session_factory,
            FakeAirtable({"RFM": [AirtableRecord("recA", {"full_name": PERSON})]}),
        ).sync()

        result = CandidateQueryService(session_factory).get_candidates(official)

        assert result.candidates == []
        with session_factory() as session:
            status = session.scalar(
                select(RosfinMatchRecord.status).where(
                    RosfinMatchRecord.person_id == person,
                    RosfinMatchRecord.snapshot_id == official,
                )
            )
        # Still the published list's word, not softened by the operator's list.
        assert status == RosfinMatchStatus.MATCHED.value
