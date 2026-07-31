"""Integration test: database bootstrap — end-to-end with temporary SQLite."""

from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text

from court_monitor.domain.models import VerificationStatus
from court_monitor.services import import_rfm_records
from court_monitor.sources.fedsfm import load_fixture_rows
from court_monitor.storage import repository as repo
from court_monitor.storage.db import make_engine, make_session_factory
from court_monitor.storage.migrations import upgrade_head
from court_monitor.storage.orm import ExtractedFact, SourceDocument


class TestDatabaseBootstrap:
    """End-to-end tests using a temporary SQLite database."""

    def test_full_lifecycle(self, tmp_path):
        """Full lifecycle: create DB → migrate → insert → query → CLI."""
        db_path = tmp_path / "test.db"
        db_url = f"sqlite:///{db_path}"

        # Step 1: Run migrations
        upgrade_head(db_url)
        assert db_path.exists(), "Database file should exist after migration"

        # Step 2: Verify tables exist
        engine = create_engine(db_url, future=True)
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            )
            tables = {row[0] for row in result}
            assert "source_documents" in tables
            assert "extracted_facts" in tables
            assert "person_records" in tables
            assert "person_match_candidates" in tables
            assert "review_items" in tables
            assert "audit_log" in tables

        # Step 3: Create a document via repository
        factory = make_session_factory(make_engine(db_url))
        with factory() as session:
            doc = SourceDocument(
                url="https://test/doc1",
                source_type="sudrf",
                content_hash="test_hash_123",
                parser_status="parsed",
                content="<html><body>Test document</body></html>",
            )
            session.add(doc)
            session.flush()

            fact = ExtractedFact(
                document_id=doc.id,
                entity="person",
                field="full_name_original",
                value="Иванов Иван Иванович",
                verification_status=VerificationStatus.inferred.value,
                confidence=0.95,
                quote="...Иванов Иван Иванович...",
                extraction_method="regex:name",
            )
            session.add(fact)
            session.commit()

            doc_id = doc.id
            fact_id = fact.id

        # Step 4: Open new session and verify data persists
        with factory() as session:
            loaded_doc = repo.get_document(session, doc_id)
            assert loaded_doc is not None
            assert loaded_doc.url == "https://test/doc1"
            assert loaded_doc.parser_status == "parsed"

            loaded_fact = session.get(ExtractedFact, fact_id)
            assert loaded_fact is not None
            assert loaded_fact.value == "Иванов Иван Иванович"

            doc_count = repo.count_documents(session)
            fact_count = repo.count_facts(session)
            assert doc_count == 1
            assert fact_count == 1

    def test_main_db_not_modified(self):
        """Ensure the main project database is not touched by tests."""
        main_db = Path("court_monitor.db")
        main_db_exists = main_db.exists()
        main_db_mtime = main_db.stat().st_mtime if main_db_exists else None

        # Run migration on a temp DB
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "temp.db"
            upgrade_head(f"sqlite:///{db_path}")

        # Verify main DB unchanged
        if main_db_exists:
            assert main_db.stat().st_mtime == main_db_mtime
        else:
            assert not main_db.exists(), "Main DB should not be created by tests"

    def test_rfm_import_and_matching(self, tmp_path):
        """Test RFM import + matching on temporary database."""
        db_path = tmp_path / "test_rfm.db"
        db_url = f"sqlite:///{db_path}"

        upgrade_head(db_url)
        factory = make_session_factory(make_engine(db_url))

        # Import RFM records
        rows = load_fixture_rows()
        with factory() as session:
            stats = import_rfm_records(session, rows, source="rfm")
            session.commit()
            assert stats.imported == len(rows)

        # Create a matching person fact
        with factory() as session:
            doc = SourceDocument(
                url="https://test/doc2",
                source_type="sudrf",
                content_hash="test_hash_456",
                parser_status="parsed",
                content="<html>test</html>",
            )
            session.add(doc)
            session.flush()
            fact = ExtractedFact(
                document_id=doc.id,
                entity="person",
                field="full_name_original",
                value="Иванов Иван Иванович",
                verification_status=VerificationStatus.inferred.value,
                confidence=0.95,
                quote="...Иванов...",
                extraction_method="regex:name",
            )
            session.add(fact)
            session.commit()

        # Generate matches
        from court_monitor.matching.candidates import generate_matches  # noqa: PLC0415

        with factory() as session:
            match_stats = generate_matches(session)
            session.commit()
            assert match_stats.candidates_created >= 1

        # Verify match candidate exists
        with factory() as session:
            candidates = repo.list_match_candidates(session)
            assert len(candidates) >= 1
            assert candidates[0].score > 0
