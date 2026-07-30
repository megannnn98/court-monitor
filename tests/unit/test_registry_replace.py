"""Replacing the stored registry, and the dedup key that makes it necessary.

Rosfinmonitoring publishes a full list rather than a delta. Importing on top
of an older one only merges when both carry the same fields — the CSV export
has no birth date at all while the live page supplies one for everyone, so the
same human yields two dedup keys and lands twice.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from court_monitor.services import (
    PurgeWouldDiscardDecisions,
    count_decided_candidates_for_source,
    purge_person_records,
)
from court_monitor.storage import repository as repo
from court_monitor.storage.db import make_engine
from court_monitor.storage.orm import (
    Base,
    ExtractedFact,
    MatchCandidate,
    PersonRecord,
    SourceDocument,
)


def _session_for(tmp_path) -> Session:
    engine = make_engine(f"sqlite:///{tmp_path / 'registry.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session, future=True)()


def _record(name: str, *, birth_date=None, birth_place=None, source="rfm") -> PersonRecord:
    return PersonRecord(
        source=source,
        raw_name=name,
        search_name=name.lower(),
        normalized_name=name.lower(),
        normalization_confidence=1.0,
        normalization_method="test",
        birth_date=birth_date,
        birth_place=birth_place,
    )


def _candidate(session: Session, record: PersonRecord, status: str) -> None:
    doc = SourceDocument(
        url=f"https://e.invalid/{record.id}",
        source_type="telegram",
        content_hash=f"h{record.id}",
        parser_status="parsed",
    )
    session.add(doc)
    session.flush()
    fact = ExtractedFact(
        document_id=doc.id,
        entity="person",
        field="full_name_original",
        value=record.raw_name,
        extraction_method="regex:name:full_fio",
    )
    session.add(fact)
    session.flush()
    session.add(
        MatchCandidate(
            extracted_fact_id=fact.id,
            person_record_id=record.id,
            score=0.5,
            algorithm_version="match-v3",
            status=status,
        )
    )
    session.flush()


# ---------------------------------------------------------------------------
# Dedup key
# ---------------------------------------------------------------------------


def test_namesakes_from_different_regions_are_kept_apart(tmp_path):
    """With no birth date the key degenerates to name-only, and real distinct
    people sharing a common Russian full name collapse into one record."""
    session = _session_for(tmp_path)
    try:
        a = _record("Яковлев Александр Николаевич", birth_place="Россия, Забайкальский край")
        b = _record("Яковлев Александр Николаевич", birth_place="Россия, Краснодарский край")

        _, created_a, _ = repo.upsert_person_record(session, a)
        _, created_b, _ = repo.upsert_person_record(session, b)

        assert created_a and created_b
        assert len(session.execute(select(PersonRecord)).scalars().all()) == 2
    finally:
        session.close()


def test_the_same_person_twice_is_still_deduplicated(tmp_path):
    session = _session_for(tmp_path)
    try:
        repo.upsert_person_record(session, _record("Иванов Иван", birth_place="Россия, Москва"))
        _, created, _ = repo.upsert_person_record(
            session, _record("Иванов Иван", birth_place="Россия, Москва")
        )

        assert not created
        assert len(session.execute(select(PersonRecord)).scalars().all()) == 1
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Replace
# ---------------------------------------------------------------------------


def test_purge_removes_only_the_named_source(tmp_path):
    session = _session_for(tmp_path)
    try:
        session.add_all([_record("Иванов Иван"), _record("Петров Пётр", source="other")])
        session.flush()

        removed = purge_person_records(session, source="rfm")

        assert removed == 1
        left = session.execute(select(PersonRecord)).scalars().all()
        assert [r.source for r in left] == ["other"]
    finally:
        session.close()


def test_purge_refuses_to_discard_decisions(tmp_path):
    """Deleting a registry record cascades into its match candidates, so a
    replace can wipe confirmed decisions — it must not do so silently."""
    session = _session_for(tmp_path)
    try:
        record = _record("Иванов Иван")
        session.add(record)
        session.flush()
        _candidate(session, record, "confirmed")

        with pytest.raises(PurgeWouldDiscardDecisions) as exc:
            purge_person_records(session, source="rfm")

        assert exc.value.decided == 1
        assert len(session.execute(select(PersonRecord)).scalars().all()) == 1
    finally:
        session.close()


def test_purge_ignores_pending_candidates(tmp_path):
    session = _session_for(tmp_path)
    try:
        record = _record("Иванов Иван")
        session.add(record)
        session.flush()
        _candidate(session, record, "pending")

        assert count_decided_candidates_for_source(session, "rfm") == 0
        assert purge_person_records(session, source="rfm") == 1
    finally:
        session.close()


def test_purge_with_force_proceeds(tmp_path):
    session = _session_for(tmp_path)
    try:
        record = _record("Иванов Иван")
        session.add(record)
        session.flush()
        _candidate(session, record, "rejected")

        assert purge_person_records(session, source="rfm", force=True) == 1
        assert session.execute(select(MatchCandidate)).scalars().all() == []
    finally:
        session.close()
