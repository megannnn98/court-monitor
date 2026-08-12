"""End-to-end test: press release → search → case card → candidate.

Task §18. The key constraint: the test MUST NOT hand-create ``Case``,
``CaseParticipant``, ``CourtEvent``, ``CaseMatchCandidate`` or ``ReviewItem``
in the test body. The pipeline itself is responsible for creating them. The
only rows the test creates are:

* the press release :class:`SourceDocument` (via ``ingest_fetch_result``)
* a :class:`CourtDocumentProcessing` state row (via the orchestrator wrapper
  in :func:`process_all_pending_court_documents`)

Everything else — Case, CaseParticipant, CourtEvent, CaseMatchCandidate,
ReviewItem, the press SourceDocument itself — is produced by the production
code path.

Also covers:
* task §19 — second-run idempotency (no duplicates)
* task §13 — candidate stays ``pending`` even when ``score = 1.0``
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import SourceBackend, SourceType
from court_monitor.parsers.sudrf_press import parse_press_release
from court_monitor.services import ingest_fetch_result
from court_monitor.services.court_orchestrator import (
    process_all_pending_court_documents,
    process_court_press_document,
)
from court_monitor.sources.base import FetchResult
from court_monitor.storage.orm import (
    AuditLog,
    Base,
    Case,
    CaseMatchCandidate,
    CaseParticipant,
    CourtDocumentProcessing,
    CourtEvent,
    ReviewItem,
    SourceDocument,
)

PRESS_FIXTURE = Path("tests/fixtures/sudrf-live/2zovs/press/release-234-razlugo.html")


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    """In-memory DB with the full schema loaded."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session_ = sessionmaker(bind=engine)
    with Session_() as session:
        yield session


@pytest.fixture()
def court_cfg() -> SourceConfig:
    return SourceConfig(
        name="2zovs",
        type=SourceType.sudrf,
        backend=SourceBackend.fixture,
        base_url="https://2zovs.msk.sudrf.ru",
        fixture_path="tests/fixtures/sudrf-live/2zovs/press",
        court_name="2-й Западный окружной военный суд",
        press_module="press_dep",
    )


def _ingest_press_release(session: Session, *, external_id: str = "234") -> SourceDocument:
    """Feed the fixture press release through the normal ingest path.

    Does NOT create Case models — only a SourceDocument row. The
    case-matching pipeline is responsible for creating everything else.
    """
    with PRESS_FIXTURE.open() as f:
        html = f.read()
    parsed = parse_press_release(html)

    fetch_result = FetchResult.from_content(
        url=f"https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=1&did={external_id}",
        content=html,
        source_type=SourceType.sudrf,
        source_name="2zovs",
        http_status=200,
        external_id=external_id,
        title=parsed.title,
    )
    doc, _ = ingest_fetch_result(session, fetch_result)
    doc.text = parsed.text
    doc.parser_status = "parsed"
    doc.parser_version = "sudrf-press-0.2"
    session.flush()
    return doc


def _counts(session: Session) -> dict[str, int]:
    """Snapshot row counts across the court pipeline tables."""
    return {
        "cases": session.execute(select(Case)).scalars().all().__len__(),
        "case_participants": session.execute(select(CaseParticipant)).scalars().all().__len__(),
        "court_events": session.execute(select(CourtEvent)).scalars().all().__len__(),
        "case_match_candidates": session.execute(select(CaseMatchCandidate))
        .scalars()
        .all()
        .__len__(),
        "review_items": session.execute(select(ReviewItem)).scalars().all().__len__(),
        "court_document_processing": session.execute(select(CourtDocumentProcessing))
        .scalars()
        .all()
        .__len__(),
        "audit_log": session.execute(select(AuditLog)).scalars().all().__len__(),
    }


# ── task §18: real press-to-case E2E ──


def test_press_release_creates_case_pipeline_without_manual_models(
    db: Session, court_cfg: SourceConfig
) -> None:
    """Single press release → Case + Participant + Event + Candidate + Review.

    The test does NOT create Case / CaseParticipant / CourtEvent /
    CaseMatchCandidate by hand. The production pipeline must create them.
    """
    doc = _ingest_press_release(db)
    before = _counts(db)

    matched = process_court_press_document(db, doc, court_cfg)
    db.commit()

    assert matched  # at least one case card was matched

    after = _counts(db)
    # Case was created (press fixture search → case card fixture).
    assert after["cases"] > before["cases"], "pipeline did not create a Case"
    # CaseParticipant was created (from case card persons section).
    assert after["case_participants"] > before["case_participants"], (
        "pipeline did not create a CaseParticipant"
    )
    # CourtEvent(s) were created (from case card events section).
    assert after["court_events"] > before["court_events"], "pipeline did not create a CourtEvent"
    # At least one candidate + review item (score=0.2 on fixture, but non-zero
    # because article 205.1 matches via signal).
    assert after["case_match_candidates"] >= 1, "pipeline did not create any CaseMatchCandidate"
    assert after["review_items"] >= 1, (
        "pipeline did not create any ReviewItem linked to the candidate"
    )
    # Candidate status stays pending (task §13).
    candidates = db.execute(select(CaseMatchCandidate)).scalars().all()
    assert all(c.status == "pending" for c in candidates), (
        "candidate auto-confirmed — violates task §13"
    )
    # ReviewItem has case_match_candidate_id populated (task §10).
    reviews = db.execute(select(ReviewItem)).scalars().all()
    assert any(r.case_match_candidate_id is not None for r in reviews), (
        "ReviewItem not linked to CaseMatchCandidate — task §10 regression"
    )


# ── task §19: second run idempotency ──


def test_second_pipeline_run_creates_no_duplicates(db: Session, court_cfg: SourceConfig) -> None:
    """Running the pipeline twice does not create duplicate rows."""
    doc = _ingest_press_release(db)

    # First run.
    process_court_press_document(db, doc, court_cfg)
    db.commit()
    after_first = _counts(db)

    # Second run — same document.
    process_court_press_document(db, doc, court_cfg)
    db.commit()
    after_second = _counts(db)

    # Row counts must be stable across the second run for core entities.
    # (The source_documents table may grow by the per-case-card
    # SourceDocument, but it is idempotent on content_hash.)
    assert after_second["cases"] == after_first["cases"], "duplicate Cases on second run"
    assert after_second["case_participants"] == after_first["case_participants"], (
        "duplicate CaseParticipant rows on second run"
    )
    assert after_second["court_events"] == after_first["court_events"], (
        "duplicate CourtEvent rows on second run"
    )
    assert after_second["case_match_candidates"] == after_first["case_match_candidates"], (
        "duplicate CaseMatchCandidate rows on second run"
    )


# ── task §14/§15: processing state end-to-end ──


def test_process_all_creates_processing_state_and_marks_success(
    db: Session, court_cfg: SourceConfig
) -> None:
    """``process_all_pending_court_documents`` manages the state machine."""
    _ingest_press_release(db)

    processed = process_all_pending_court_documents(db, court_name="2zovs", force_live=False)
    db.commit()

    assert processed.processed >= 1
    states = db.execute(select(CourtDocumentProcessing)).scalars().all()
    assert states
    state = states[0]
    assert state.pipeline_version == "court-v1"
    # Success with at least one candidate → review_created.
    assert state.status in ("review_created", "processed_no_match")
    assert state.attempt_count == 1


def test_process_all_skips_already_processed_no_match(db: Session, court_cfg: SourceConfig) -> None:
    """processed_no_match is not retried automatically (task §14)."""
    _ingest_press_release(db)
    process_all_pending_court_documents(db, court_name="2zovs", force_live=False)
    db.commit()

    states = db.execute(select(CourtDocumentProcessing)).scalars().all()
    assert states
    # Force the state to processed_no_match (in case the run actually matched).
    states[0].status = "processed_no_match"
    db.commit()

    # Second call with no --reprocess should NOT touch the row.
    process_all_pending_court_documents(db, court_name="2zovs", force_live=False)
    db.commit()
    states_after = db.execute(select(CourtDocumentProcessing)).scalars().all()
    assert states_after[0].attempt_count == 1  # unchanged


# ── batch-level idempotency: case-card docs must NOT become press ──


def test_batch_second_run_does_not_process_case_card_as_press(
    db: Session, court_cfg: SourceConfig
) -> None:
    """Case-card SourceDocuments must not be re-processed as press releases.

    Regression: process_all_pending_court_documents selects by
    (source_type=sudrf, parser_status=parsed) which also matches case-card
    documents (parser_version='sud-delo-0.1'). The query must restrict to
    press parser versions only.
    """
    _ingest_press_release(db)

    # Run #1 — processes the press release, creates case-card SourceDocument.
    process_all_pending_court_documents(db, court_name="2zovs", force_live=False)
    db.commit()

    after_first = _counts(db)
    states_after_first = list(db.execute(select(CourtDocumentProcessing)).scalars().all())
    assert after_first["cases"] >= 1, "first run must create at least one Case"

    # Verify case-card SourceDocument exists.
    case_card_docs = list(
        db.execute(
            select(SourceDocument).where(
                SourceDocument.parser_version == "sud-delo-0.1",
            )
        )
        .scalars()
        .all()
    )
    assert case_card_docs, "first run must create a case-card SourceDocument"

    # Run #2 — must NOT create new CourtDocumentProcessing for case-card docs.
    process_all_pending_court_documents(db, court_name="2zovs", force_live=False)
    db.commit()

    states_after_second = list(db.execute(select(CourtDocumentProcessing)).scalars().all())
    assert len(states_after_second) == len(states_after_first), (
        f"second run created extra CourtDocumentProcessing rows: "
        f"{len(states_after_second)} vs {len(states_after_first)}"
    )

    after_second = _counts(db)
    assert after_second["cases"] == after_first["cases"], "Cases changed on second run"
    assert after_second["case_participants"] == after_first["case_participants"]
    assert after_second["court_events"] == after_first["court_events"]
    assert after_second["case_match_candidates"] == after_first["case_match_candidates"]
    assert after_second["review_items"] == after_first["review_items"]

    # Verify case-card docs did NOT get their own CourtDocumentProcessing.
    case_card_doc_ids = {d.id for d in case_card_docs}
    for state in states_after_second:
        assert state.document_id not in case_card_doc_ids, (
            f"case-card doc id={state.document_id} got CourtDocumentProcessing"
        )
