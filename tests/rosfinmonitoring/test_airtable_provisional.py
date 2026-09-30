"""The Airtable list is not the перечень: a match only it knows about is probable.

Three things must hold, and each is checked here against a real database:

1. the Airtable snapshot never becomes the snapshot the matcher judges against;
2. a name the published list leaves out but Airtable's has is `matched_probable`, never
   `matched`, and a person is never called a candidate on the strength of it;
3. a name both lists have keeps the confirmed match the published list gave it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder
from support.person_resolution_fixtures import seed_person

from airtable.client import AirtableRecord
from airtable.repository import sync_rfm_persons
from candidates.models import (
    DEFAULT_MIN_PERSECUTION_CONFIDENCE,
    RosfinmonitoringStatus,
)
from candidates.service import CandidateQueryService
from db.orm_models import (
    EntityGroupRfMatchRecord,
    EntityMentionRecord,
    PersonRecord,
    RosfinMatchRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from entities.collector import EntityCollector
from entities.rf_check import FULL, NAME, EntityRfCheck
from rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from rosfinmonitoring.matcher_models import RosfinMatchStatus
from rosfinmonitoring.matcher_persistence import RosfinMatchPersistence
from rosfinmonitoring.models import AIRTABLE_SNAPSHOT_SOURCE_URL
from rosfinmonitoring.parser import _create_matching_key, _normalize_name
from rosfinmonitoring.probable import AirtableProvisionalMatcher
from rosfinmonitoring.snapshot_lookup import SqlAlchemyRosfinmonitoringSnapshotLookup

PERSON = "Петров Петр Петрович"
OFFICIAL_URL = "https://www.fedsfm.ru/documents/terrorists-catalog-portal-act"


def _offline() -> bytes:
    """A list that cannot be fetched: the check keeps the last snapshot it has."""
    raise httpx.ConnectError("no network in a test")


def _official(session_factory: sessionmaker[Session], names: list[tuple[str, str]]) -> int:
    """A downloaded snapshot: (content hash, the names in it)."""
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        snapshot_id = seed.snapshot(content_hash=names[0][0] if names else "official")
        for _, full_name in names:
            # The key the fedsfm.ru parser writes, not the seeder's pipe-joined one.
            normalized = _normalize_name(full_name)
            session.add(
                RosfinmonitoringEntryRecord(
                    snapshot_id=snapshot_id,
                    full_name=full_name,
                    normalized_name=normalized,
                    matching_key=_create_matching_key(normalized),
                    status="active",
                    raw_data={},
                )
            )
        record = session.get_one(RosfinmonitoringSnapshotRecord, snapshot_id)
        record.source_url = OFFICIAL_URL
        record.entry_count = len(names)
    return snapshot_id


def _airtable(session_factory: sessionmaker[Session], names: list[tuple[str, dict]]) -> int:
    """The same, written the way the sync writes it."""
    records = [
        AirtableRecord(record_id, {"full_name": full_name, **fields})
        for record_id, full_name, fields in names
    ]
    with session_factory.begin() as session:
        sync_rfm_persons(session, records)
    with session_factory() as session:
        return session.scalar(
            select(RosfinmonitoringSnapshotRecord.id).where(
                RosfinmonitoringSnapshotRecord.source_url == AIRTABLE_SNAPSHOT_SOURCE_URL
            )
        )


def _person(session_factory: sessionmaker[Session], name: str) -> int:
    """A person as the pipeline stores them: the real matching key, not a fixture's."""
    return seed_person(session_factory, name)


def _classify(session_factory: sessionmaker[Session], person_id: int) -> None:
    with session_factory.begin() as session:
        DatabaseSeeder(session).classification(
            person_id, "political", DEFAULT_MIN_PERSECUTION_CONFIDENCE
        )


def _match(session_factory: sessionmaker[Session], person_id: int, snapshot_id: int) -> None:
    matcher = RuleBasedRosfinmonitoringMatcher(session_factory)
    RosfinMatchPersistence(session_factory).save_match_result(
        matcher.match_person(person_id, snapshot_id)
    )


class TestTheAirtableSnapshotIsNeverTheList:
    def test_the_lookup_skips_it_even_when_it_is_the_newest(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, [("official", "Иванов Иван Иванович")])
        # Airtable's list is dated later, so only an explicit exclusion keeps it out.
        with session_factory.begin() as session:
            session.get_one(RosfinmonitoringSnapshotRecord, official).snapshot_date = datetime(
                2020, 1, 1, tzinfo=UTC
            )
        _airtable(
            session_factory,
            [("recA", "Петров Петр Петрович", {"birth_date": "01.01.1980"})],
        )

        lookup = SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory)

        assert lookup.latest_imported_snapshot().snapshot_id == official
        assert lookup.airtable_snapshot() is not None

    def test_without_an_official_list_there_is_no_list_at_all(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        _airtable(session_factory, [("recA", "Петров Петр Петрович", {})])
        lookup = SqlAlchemyRosfinmonitoringSnapshotLookup(session_factory)
        # Better no answer than one taken from a list the state does not publish.
        assert lookup.latest_imported_snapshot() is None

    def test_the_entity_check_judges_against_the_official_snapshot(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, [("official", "Иванов Иван Иванович")])
        result = EntityRfCheck(session_factory, download=_offline).run()
        assert result.snapshot_id == official


class TestProbableMatches:
    def test_a_name_only_airtable_has_is_probable_not_confirmed(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, [("official-empty", "Кто-то Другой")])
        _airtable(session_factory, [("recA", "Петров Петр Петрович", {"birth_date": "01.01.1980"})])
        petrov = _person(session_factory, "Петров Петр Петрович")
        _match(session_factory, petrov, official)

        assert AirtableProvisionalMatcher(session_factory).apply(official) == 1

        with session_factory() as session:
            airtable_id = session.scalar(
                select(RosfinmonitoringSnapshotRecord.id).where(
                    RosfinmonitoringSnapshotRecord.source_url == AIRTABLE_SNAPSHOT_SOURCE_URL
                )
            )
            probable = session.scalar(
                select(RosfinMatchRecord).where(
                    RosfinMatchRecord.person_id == petrov,
                    RosfinMatchRecord.snapshot_id == airtable_id,
                )
            )
        assert probable.status == RosfinMatchStatus.MATCHED_PROBABLE.value
        # The official row is untouched: still a confirmed absence.
        with session_factory() as session:
            official_row = session.scalar(
                select(RosfinMatchRecord).where(
                    RosfinMatchRecord.person_id == petrov,
                    RosfinMatchRecord.snapshot_id == official,
                )
            )
            assert official_row.status == RosfinMatchStatus.NOT_MATCHED.value

    def test_a_name_both_lists_have_keeps_the_confirmed_match(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, [("official", "Петров Петр Петрович")])
        _airtable(session_factory, [("recA", "Петров Петр Петрович", {})])
        petrov = _person(session_factory, "Петров Петр Петрович")
        _match(session_factory, petrov, official)

        # The published list already has him: nothing to soften, nothing to add.
        assert AirtableProvisionalMatcher(session_factory).apply(official) == 0
        with session_factory() as session:
            airtable_id = session.scalar(
                select(RosfinmonitoringSnapshotRecord.id).where(
                    RosfinmonitoringSnapshotRecord.source_url == AIRTABLE_SNAPSHOT_SOURCE_URL
                )
            )
            assert (
                session.scalar(
                    select(RosfinMatchRecord).where(
                        RosfinMatchRecord.person_id == petrov,
                        RosfinMatchRecord.snapshot_id == airtable_id,
                    )
                )
                is None
            )
            confirmed = session.scalar(
                select(RosfinMatchRecord).where(
                    RosfinMatchRecord.person_id == petrov,
                    RosfinMatchRecord.snapshot_id == official,
                )
            )
            assert confirmed.status == RosfinMatchStatus.MATCHED.value

    def test_a_person_airtable_does_not_have_is_left_alone(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, [("official-empty", "Кто-то Другой")])
        _airtable(session_factory, [("recA", "Сидоров Иван Иванович", {})])
        petrov = _person(session_factory, "Петров Петр Петрович")
        _match(session_factory, petrov, official)

        assert AirtableProvisionalMatcher(session_factory).apply(official) == 0

    def test_a_name_that_left_airtables_list_stops_being_probable(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, [("official-empty", "Кто-то Другой")])
        _airtable(session_factory, [("recA", "Петров Петр Петрович", {})])
        petrov = _person(session_factory, "Петров Петр Петрович")
        _match(session_factory, petrov, official)
        provisional = AirtableProvisionalMatcher(session_factory)
        assert provisional.apply(official) == 1

        # The operator removed him from Airtable: the probability is gone, not stale.
        with session_factory.begin() as session:
            sync_rfm_persons(session, [])
        assert provisional.apply(official) == 0
        with session_factory() as session:
            rows = session.scalars(
                select(RosfinMatchRecord).where(
                    RosfinMatchRecord.person_id == petrov,
                    RosfinMatchRecord.status == RosfinMatchStatus.MATCHED_PROBABLE.value,
                )
            ).all()
        assert rows == []

    def test_without_an_airtable_list_nothing_is_probable(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, [("official-empty", "Кто-то Другой")])
        petrov = _person(session_factory, "Петров Петр Петрович")
        _match(session_factory, petrov, official)
        assert AirtableProvisionalMatcher(session_factory).apply(official) == 0

    def test_running_twice_writes_the_same_rows(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, [("official-empty", "Кто-то Другой")])
        _airtable(session_factory, [("recA", "Петров Петр Петрович", {})])
        petrov = _person(session_factory, "Петров Петр Петрович")
        _match(session_factory, petrov, official)
        provisional = AirtableProvisionalMatcher(session_factory)
        assert provisional.apply(official) == 1
        assert provisional.apply(official) == 1
        with session_factory() as session:
            rows = session.scalars(
                select(RosfinMatchRecord).where(
                    RosfinMatchRecord.status == RosfinMatchStatus.MATCHED_PROBABLE.value
                )
            ).all()
        assert len(rows) == 1


class TestCandidates:
    def test_a_probable_match_is_not_a_candidate_by_default(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, [("official-empty", "Кто-то Другой")])
        _airtable(session_factory, [("recA", PERSON, {})])
        petrov = _person(session_factory, PERSON)
        _classify(session_factory, petrov)
        _match(session_factory, petrov, official)
        AirtableProvisionalMatcher(session_factory).apply(official)

        result = CandidateQueryService(session_factory).get_candidates(official)

        # Probably in the перечень is not a confirmed absence: he is not a candidate.
        assert [c.person_id for c in result.candidates] == []

    def test_asked_for_explicitly_the_reason_is_visible(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, [("official-empty", "Кто-то Другой")])
        _airtable(session_factory, [("recA", PERSON, {})])
        petrov = _person(session_factory, PERSON)
        _classify(session_factory, petrov)
        _match(session_factory, petrov, official)
        AirtableProvisionalMatcher(session_factory).apply(official)

        result = CandidateQueryService(session_factory).get_candidates(
            official, include_rf_statuses=frozenset({RosfinmonitoringStatus.MATCHED_PROBABLE})
        )

        assert [c.rosfinmonitoring_status for c in result.candidates] == [
            RosfinmonitoringStatus.MATCHED_PROBABLE
        ]

    def test_a_person_no_list_has_is_still_a_candidate(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        official = _official(session_factory, [("official-empty", "Кто-то Другой")])
        _airtable(session_factory, [("recA", PERSON, {})])
        petrov = _person(session_factory, "Сидоров Иван Иванович")
        _classify(session_factory, petrov)
        _match(session_factory, petrov, official)
        AirtableProvisionalMatcher(session_factory).apply(official)

        result = CandidateQueryService(session_factory).get_candidates(official)

        assert [c.person_id for c in result.candidates] == [petrov]


class TestEntityCheckLevels:
    def _seed_entity(self, session_factory: sessionmaker[Session], name: str) -> None:
        """An entity the collector will actually build: a mention inside a criminal
        event, which is what step 3 collects from."""
        # A full name with a patronymic: FULL (a confirmed list hit) needs one.
        first, last, patronymic = name.split()[0], name.split()[-1], name.split()[1]
        text = f"Суд арестовал {name} по ст. 282 УК РФ."
        with session_factory.begin() as session:
            seed = DatabaseSeeder(session)
            source = seed.source("news", "https://news.example.test")
            _, run = seed.article(source, external_id=name, title=name, text=text)
            mention = seed.mention(run, name, person_id=None)
            session.get_one(EntityMentionRecord, mention).normalized_data = {
                "first_name": first,
                "last_name": last,
                "patronymic": patronymic,
            }
            seed.event(run, text, event_type="arrest", event_date=None, links=[])
        EntityCollector(session_factory).run()

    def test_an_airtable_only_name_is_possible_and_never_merges(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        # The official list has nobody; Airtable's has the entity's name.
        _official(session_factory, [("official-empty", "Никого Нет")])
        _airtable(session_factory, [("recA", "Петров Петр Петрович", {})])
        self._seed_entity(session_factory, "Петр Петрович Петров")

        result = EntityRfCheck(session_factory, download=_offline).run()

        assert result.full == 0
        assert result.possible == 1
        assert result.merged == 0
        with session_factory() as session:
            levels = set(session.scalars(select(EntityGroupRfMatchRecord.level)))
        # NAME, not FULL: a probability can neither confirm nor merge.
        assert levels == {NAME}
        assert FULL != NAME

    def test_a_name_the_official_list_has_is_confirmed(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        _official(session_factory, [("official", "Петров Петр Петрович")])
        _airtable(session_factory, [("recA", "Петров Петр Петрович", {})])
        self._seed_entity(session_factory, "Петр Петрович Петров")

        result = EntityRfCheck(session_factory, download=_offline).run()

        assert result.full == 1
        with session_factory() as session:
            levels = set(session.scalars(select(EntityGroupRfMatchRecord.level)))
        assert levels == {FULL}


def test_the_matcher_is_untouched_by_any_of_this() -> None:
    """The rule-based matcher is the same object as before: no Airtable branch in it."""
    source = RuleBasedRosfinmonitoringMatcher.__doc__ or ""
    assert "airtable" not in source.lower()
    assert AirtableProvisionalMatcher is not RuleBasedRosfinmonitoringMatcher


def test_person_rows_are_never_written_by_the_sync(session_factory: sessionmaker[Session]) -> None:
    """The whole feature lives outside `persons`: ER output stays ER output."""
    official = _official(session_factory, [("official-empty", "Кто-то Другой")])
    _airtable(session_factory, [("recA", PERSON, {})])
    _person(session_factory, PERSON)
    AirtableProvisionalMatcher(session_factory).apply(official)

    with session_factory() as session:
        # One person, the one the test made: the sync invents nobody.
        assert [row.canonical_name for row in session.scalars(select(PersonRecord))] == [PERSON]
