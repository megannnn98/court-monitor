"""Referential integrity of the SQLite engine built by make_engine().

These deliberately go through ``make_engine`` rather than the ``db_session``
fixture: the fixture builds its engine with a bare ``create_engine`` and so
would not exercise the PRAGMA that make_engine installs.
"""

from __future__ import annotations

import threading

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from court_monitor.storage.db import make_engine
from court_monitor.storage.orm import (
    Base,
    ExtractedFact,
    MatchCandidate,
    PersonRecord,
    SourceDocument,
)


def _session_for(tmp_path) -> Session:
    engine = make_engine(f"sqlite:///{tmp_path / 'integrity.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session, future=True)()


def test_sqlite_foreign_keys_are_enforced(tmp_path):
    session = _session_for(tmp_path)
    try:
        enabled = session.execute(text("PRAGMA foreign_keys")).scalar()
        assert enabled == 1
    finally:
        session.close()


def _seed_candidate(session: Session) -> tuple[int, int]:
    doc = SourceDocument(
        url="https://example.invalid/1",
        source_type="telegram",
        content_hash="hash-1",
        parser_status="parsed",
    )
    session.add(doc)
    session.flush()

    fact = ExtractedFact(
        document_id=doc.id,
        entity="person",
        field="full_name_original",
        value="Иванов Иван Иванович",
        extraction_method="regex:name:full_fio",
    )
    record = PersonRecord(
        source="rfm",
        raw_name="Иванов Иван Иванович",
        search_name="иванов иван иванович",
        normalized_name="иванов иван иванович",
    )
    session.add_all([fact, record])
    session.flush()

    session.add(
        MatchCandidate(
            extracted_fact_id=fact.id,
            person_record_id=record.id,
            score=0.5,
            algorithm_version="match-v3",
        )
    )
    session.commit()
    return fact.id, doc.id


def test_deleting_a_fact_removes_its_match_candidates(tmp_path):
    """``reprocess`` drops a document's facts; candidates must not outlive them.

    Without FK enforcement the candidate row survived pointing at a deleted
    fact id, and surfaced in list-matches with an empty name column.
    """
    session = _session_for(tmp_path)
    try:
        fact_id, _ = _seed_candidate(session)
        assert session.execute(select(MatchCandidate)).scalars().all()

        session.delete(session.get(ExtractedFact, fact_id))
        session.commit()

        assert session.execute(select(MatchCandidate)).scalars().all() == []
    finally:
        session.close()


def test_deleting_a_document_removes_facts_and_candidates(tmp_path):
    session = _session_for(tmp_path)
    try:
        _, doc_id = _seed_candidate(session)

        session.delete(session.get(SourceDocument, doc_id))
        session.commit()

        assert session.execute(select(ExtractedFact)).scalars().all() == []
        assert session.execute(select(MatchCandidate)).scalars().all() == []
    finally:
        session.close()


def test_sqlite_uses_wal_and_a_long_busy_timeout(tmp_path):
    """A background job holds its write transaction for as long as it takes to
    fetch every source. Under the default rollback journal that blocked every
    reader, so opening the UI mid-run failed."""
    session = _session_for(tmp_path)
    try:
        assert session.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert session.execute(text("PRAGMA busy_timeout")).scalar() >= 30000
    finally:
        session.close()


def test_reads_are_not_blocked_by_an_open_write(tmp_path):
    """The concrete regression: reproduced as 'database is locked' before WAL."""
    engine = make_engine(f"sqlite:///{tmp_path / 'wal.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session, future=True)

    writing = threading.Event()
    release = threading.Event()

    def hold_write() -> None:
        with factory() as s:
            s.add(
                SourceDocument(
                    url="https://example.invalid/w",
                    source_type="telegram",
                    content_hash="wal-1",
                    parser_status="pending",
                )
            )
            s.flush()
            writing.set()
            release.wait(10)
            s.commit()

    writer = threading.Thread(target=hold_write, daemon=True)
    writer.start()
    try:
        assert writing.wait(5)
        with factory() as reader:
            reader.execute(select(SourceDocument)).scalars().all()  # must not block
    finally:
        release.set()
        writer.join(10)
        engine.dispose()
