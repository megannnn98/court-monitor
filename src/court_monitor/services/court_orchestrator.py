"""End-to-end orchestrator for court press release processing."""

from __future__ import annotations

import contextlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from court_monitor.config.loader import SourceConfig, get_source
from court_monitor.extraction.articles import extract_articles
from court_monitor.extraction.dates import extract_dates
from court_monitor.extraction.names import extract_name_candidates
from court_monitor.matching.case_matching import match_press_release_to_case
from court_monitor.observability import get_logger
from court_monitor.parsers.sud_delo import parse_case_card
from court_monitor.services.case_match_service import create_case_match_candidate
from court_monitor.services.case_persistence import persist_case_card
from court_monitor.sources.sudrf_case_search import SudrfCaseSearchAdapter
from court_monitor.sources.sudrf_dto import SudrfCaseSearchCriteria
from court_monitor.storage.orm import Case, SourceDocument

_log = get_logger(__name__)


def process_court_press_document(
    session: Session,
    document: SourceDocument,
    court_config: SourceConfig,
) -> list[Case]:
    """Process a court press release document end-to-end.

    Pipeline:
    1. Extract article, date, person name from document
    2. Search for matching cases on sud_delo
    3. Fetch and parse case cards
    4. Persist cases and events
    5. Match press release to cases
    6. Create CaseMatchCandidate for review

    Args:
        session: Database session
        document: Press release SourceDocument
        court_config: Court configuration (with base_url, court_name, etc.)

    Returns:
        List of matched Case objects
    """
    _log.info(
        "orchestrator.start",
        document_id=document.id,
        court=court_config.name,
    )

    # Step 1: Extract facts from document
    extraction_text = document.text or ""

    article_facts = extract_articles(extraction_text, source_url=document.url)
    date_facts = extract_dates(extraction_text, source_url=document.url)
    name_facts = extract_name_candidates(extraction_text, source_url=document.url)

    # Extract primary values
    article = article_facts[0].value.get("article") if article_facts else None
    decision_date = None
    if date_facts:
        # Try to parse date
        date_str = date_facts[0].value.get("date")
        if date_str:
            with contextlib.suppress(ValueError, TypeError):
                decision_date = datetime.fromisoformat(date_str).date()

    person_name = name_facts[0].value if name_facts else None

    _log.info(
        "orchestrator.extracted",
        document_id=document.id,
        article=article,
        decision_date=decision_date,
        person_name=person_name,
    )

    # Step 2: Search for cases on sud_delo
    adapter = SudrfCaseSearchAdapter(court_config)
    criteria = SudrfCaseSearchCriteria(
        court=court_config.name,
        article=article,
        decision_date=decision_date,
        person_name=person_name,
    )

    search_results = adapter.search(criteria)

    _log.info(
        "orchestrator.search_results",
        document_id=document.id,
        results_count=len(search_results),
    )

    if not search_results:
        _log.warning(
            "orchestrator.no_results",
            document_id=document.id,
        )
        return []

    # Step 3-6: Process each search result
    matched_cases: list[Case] = []

    for result in search_results:
        try:
            # Fetch case card HTML
            case_html = adapter.fetch_case_card_html(result)
            if not case_html:
                continue

            # Parse case card
            parsed_card = parse_case_card(
                case_html,
                case_uid=result.case_uid,
                court=court_config.court_name,
            )

            # Persist case and events
            case, created = persist_case_card(
                session,
                parsed_card,
                source_url=result.url,
                court_name=court_config.court_name,
            )

            # Match press release to case
            match_result = match_press_release_to_case(
                article=article,
                decision_date=decision_date,
                court=court_config.court_name,
                person_name=person_name,
                case_card=parsed_card,
            )

            # Create match candidate
            candidate, candidate_created = create_case_match_candidate(
                session,
                source_document=document,
                case=case,
                match_result=match_result,
            )

            _log.info(
                "orchestrator.case_matched",
                document_id=document.id,
                case_id=case.id,
                case_uid=case.case_uid,
                score=match_result.confidence,
                signals_count=len(match_result.signals),
                candidate_created=candidate_created,
            )

            matched_cases.append(case)

        except Exception as e:
            _log.exception(
                "orchestrator.case_error",
                document_id=document.id,
                case_uid=result.case_uid,
                error=str(e),
            )
            continue

    _log.info(
        "orchestrator.complete",
        document_id=document.id,
        matched_cases=len(matched_cases),
    )

    return matched_cases


def process_all_pending_court_documents(
    session: Session,
    court_name: str = "2zovs",
) -> int:
    """Process all pending court press release documents.

    Args:
        session: Database session
        court_name: Court name to process (default: "2zovs")

    Returns:
        Number of documents processed
    """
    # Get court config
    court_config = get_source(court_name)
    if not court_config:
        _log.error("orchestrator.court_not_found", court=court_name)
        return 0

    # Find pending court documents
    pending_docs = list(
        session.execute(
            select(SourceDocument).where(
                SourceDocument.source_type == "sudrf",
                SourceDocument.source_name == court_name,
                SourceDocument.parser_status == "parsed",
            )
        )
        .scalars()
        .all()
    )

    _log.info(
        "orchestrator.pending_documents",
        court=court_name,
        count=len(pending_docs),
    )

    processed = 0
    for doc in pending_docs:
        try:
            process_court_press_document(session, doc, court_config)
            processed += 1
        except Exception as e:
            _log.exception(
                "orchestrator.document_error",
                document_id=doc.id,
                error=str(e),
            )

    _log.info(
        "orchestrator.batch_complete",
        court=court_name,
        processed=processed,
        total=len(pending_docs),
    )

    return processed
