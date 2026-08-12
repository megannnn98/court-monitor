"""End-to-end orchestrator for court press release processing."""

from __future__ import annotations

import contextlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from court_monitor.config.loader import SourceConfig, get_source
from court_monitor.domain.models import ParserStatus
from court_monitor.extraction.articles import extract_articles
from court_monitor.extraction.dates import extract_dates
from court_monitor.extraction.event_classifier import PressEventType, classify_press_event
from court_monitor.extraction.names import extract_name_candidates
from court_monitor.matching.case_matching import match_press_release_to_case
from court_monitor.observability import get_logger
from court_monitor.parsers.sud_delo import parse_case_card
from court_monitor.services.case_match_service import create_case_match_candidate
from court_monitor.services.case_persistence import persist_case_card
from court_monitor.sources.sudrf_case_search import SudrfCaseSearchAdapter
from court_monitor.sources.sudrf_dto import SudrfCaseSearchCriteria
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import (
    Case,
    CaseMatchCandidate,
    SourceDocument,
)

_log = get_logger(__name__)

MAX_RESULTS = 10


def process_court_press_document(
    session: Session,
    document: SourceDocument,
    court_config: SourceConfig,
) -> list[Case]:
    """Process a court press release document end-to-end."""
    _log.info("orchestrator.start", document_id=document.id, court=court_config.name)

    extraction_text = document.text or ""

    article_facts = extract_articles(extraction_text, source_url=document.url)
    date_facts = extract_dates(extraction_text, source_url=document.url)
    name_facts = extract_name_candidates(extraction_text, source_url=document.url)

    event_classification = classify_press_event(extraction_text)

    article = article_facts[0].value.get("article") if article_facts else None
    person_name = _pick_person_name(name_facts, article_facts, extraction_text)

    decision_date = _pick_decision_date(
        event_classification, date_facts, extraction_text, document.published_at
    )

    _log.info(
        "orchestrator.extracted",
        document_id=document.id,
        article=article,
        decision_date=decision_date,
        event_type=event_classification.event_type,
        person_name=person_name,
    )

    adapter = SudrfCaseSearchAdapter(court_config)
    criteria = SudrfCaseSearchCriteria(
        court=court_config.name,
        article=article,
        decision_date=decision_date,
        person_name=person_name
        if event_classification.event_type == PressEventType.sentence_delivered
        else None,
    )

    search_results = adapter.search(criteria)
    _log.info("orchestrator.search_results", document_id=document.id, count=len(search_results))

    if not search_results:
        _log.warning("orchestrator.no_results", document_id=document.id)
        return []

    matched_cases: list[Case] = []
    court_name = court_config.court_name or court_config.name

    for result in search_results[:MAX_RESULTS]:
        try:
            with session.begin_nested():
                case = _process_search_result(
                    session=session,
                    result=result,
                    adapter=adapter,
                    court_config=court_config,
                    court_name=court_name,
                    article=article,
                    decision_date=decision_date,
                    person_name=person_name,
                    press_doc=document,
                )
                if case is not None:
                    matched_cases.append(case)
                session.commit()
        except Exception as e:
            _log.exception("orchestrator.case_error", document_id=document.id, error=str(e))

    _log.info("orchestrator.complete", document_id=document.id, matched=len(matched_cases))
    return matched_cases


def _process_search_result(
    *,
    session,
    result,
    adapter,
    court_config,
    court_name,
    article,
    decision_date,
    person_name,
    press_doc,
):
    case_html = adapter.fetch_case_card_html(result)
    if not case_html:
        return None

    parsed_card = parse_case_card(case_html, case_uid=result.case_uid, court=court_name)

    card_doc = repo.upsert_document_html(
        session,
        url=result.url,
        html=case_html,
        source_name=court_config.name or "sudrf",
        source_type="sudrf",
    )

    case, _created = persist_case_card(
        session,
        parsed_card,
        source_url=result.url,
        court_name=court_name,
        source_document=card_doc,
    )

    match_result = match_press_release_to_case(
        article=article,
        decision_date=decision_date,
        court=court_name,
        person_name=person_name,
        case_card=parsed_card,
    )

    if not match_result.signals:
        _log.info("orchestrator.zero_signals", document_id=press_doc.id, case_uid=result.case_uid)
        return None

    _create_match_and_review(session, press_doc, case, match_result)
    return case


def _create_match_and_review(
    session: Session,
    press_doc: SourceDocument,
    case: Case,
    match_result,
) -> None:
    candidate, created = create_case_match_candidate(
        session,
        source_document=press_doc,
        case=case,
        match_result=match_result,
    )

    if created and candidate.status == "pending":
        repo.upsert_review_item(
            session,
            item_type="court_case_match",
            priority="medium",
            document_id=press_doc.id,
            source_id=str(case.id),
            source_url=press_doc.url,
            data={
                "candidate_id": candidate.id,
                "case_number": case.case_number,
                "case_uid": case.case_uid,
                "score": match_result.confidence,
                "signals": len(match_result.signals),
            },
        )


def _pick_decision_date(event_classification, date_facts, text, published_at):
    """Pick decision date based on event type and proximity to event phrase."""
    if event_classification.event_type in (
        PressEventType.sentence_delivered,
        PressEventType.appeal_decided,
    ):
        quote = event_classification.quote
        if quote:
            best_date = _date_closest_to(text, date_facts, quote)
            if best_date:
                return best_date

    if date_facts:
        for df in date_facts:
            date_type = df.value.get("type") if isinstance(df.value, dict) else None
            if date_type in ("verdict_date", "effective_date"):
                date_str = df.value.get("date")
                if date_str:
                    try:
                        return datetime.fromisoformat(date_str).date()
                    except (ValueError, TypeError):
                        _log.warning("orchestrator.date_parse_failed", date_str=date_str)

    if published_at:
        if hasattr(published_at, "date"):
            return published_at.date()
        return published_at

    return None


def _date_closest_to(text, date_facts, phrase):
    """Find date fact closest to a phrase in text."""
    phrase_pos = text.find(phrase)
    if phrase_pos < 0:
        return None
    best_date = None
    best_dist = float("inf")
    for df in date_facts:
        date_str = df.value.get("date") if isinstance(df.value, dict) else None
        if not date_str:
            continue
        quote = df.quote or ""
        pos = text.find(quote)
        if pos >= 0:
            dist = abs(pos - phrase_pos)
            if dist < best_dist:
                best_dist = dist
                with contextlib.suppress(ValueError, TypeError):
                    best_date = datetime.fromisoformat(date_str).date()
    return best_date


def _pick_person_name(name_facts, article_facts, text):
    if not name_facts:
        return None
    if not article_facts:
        return name_facts[0].value
    article_quote = article_facts[0].quote
    if not article_quote:
        return name_facts[0].value
    article_pos = text.find(article_quote)
    if article_pos < 0:
        return name_facts[0].value
    best_name = None
    best_dist = float("inf")
    for nf in name_facts:
        name_str = str(nf.value) if nf.value else ""
        name_pos = text.find(name_str)
        if name_pos >= 0:
            dist = abs(name_pos - article_pos)
            if dist < best_dist:
                best_dist = dist
                best_name = name_str
    return best_name or name_facts[0].value


def process_all_pending_court_documents(session: Session, court_name: str = "2zovs") -> int:
    """Process all pending court press release documents."""
    court_config = get_source(court_name)
    if not court_config:
        _log.error("orchestrator.court_not_found", court=court_name)
        return 0

    pending_docs = list(
        session.execute(
            select(SourceDocument).where(
                SourceDocument.source_type == "sudrf",
                SourceDocument.source_name == court_name,
                SourceDocument.parser_status == ParserStatus.parsed.value,
            )
        )
        .scalars()
        .all()
    )

    # Exclude docs that already have existing CaseMatchCandidates
    processed_ids = {
        row[0]
        for row in session.execute(select(CaseMatchCandidate.source_document_id).distinct()).all()
    }
    pending_docs = [d for d in pending_docs if d.id not in processed_ids]

    _log.info("orchestrator.pending", court=court_name, count=len(pending_docs))

    processed = 0
    for doc in pending_docs:
        try:
            process_court_press_document(session, doc, court_config)
            processed += 1
        except Exception as e:
            _log.exception("orchestrator.document_error", document_id=doc.id, error=str(e))

    _log.info(
        "orchestrator.batch_complete",
        court=court_name,
        processed=processed,
        total=len(pending_docs),
    )
    return processed
