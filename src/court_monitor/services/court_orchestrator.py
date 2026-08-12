"""End-to-end orchestrator for court press release processing."""

from __future__ import annotations

import contextlib
from dataclasses import replace as _dcreplace
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from court_monitor.config.loader import SourceConfig, get_source
from court_monitor.domain.models import ParserStatus, SourceBackend
from court_monitor.extraction.articles import extract_articles
from court_monitor.extraction.dates import extract_dates
from court_monitor.extraction.event_classifier import PressEventType, classify_press_event
from court_monitor.extraction.names import extract_name_candidates
from court_monitor.matching.case_matching import match_press_release_to_case
from court_monitor.observability import get_logger
from court_monitor.parsers.sud_delo import parse_case_card
from court_monitor.services.case_match_service import create_case_match_candidate
from court_monitor.services.case_persistence import persist_case_card
from court_monitor.services.court_processing import (
    ProcessingStatus,
    get_or_create_processing_state,
    mark_processing_failed,
    mark_processing_success,
    mark_processing_temporary_failure,
    should_process,
)
from court_monitor.sources.sudrf_case_search import SudrfCaseSearchAdapter
from court_monitor.sources.sudrf_dto import (
    SearchAttempt,
    SudrfCaseSearchCriteria,
    SudrfCaseSearchResult,
)
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import (
    Case,
    CaseMatchCandidate,
    CourtDocumentProcessing,
    SourceDocument,
)

_log = get_logger(__name__)

MAX_RESULTS = 10


def process_court_press_document(
    session: Session,
    document: SourceDocument,
    court_config: SourceConfig,
    *,
    processing_state: CourtDocumentProcessing | None = None,
) -> list[Case]:
    """Process a court press release document end-to-end.

    When ``processing_state`` is passed, status transitions are recorded into it
    (task §14):
      * success with candidates → ``review_created``
      * success with 0 candidates → ``processed_no_match``
      * transient exception → ``temporary_failure`` (caller may retry)
      * permanent error → ``failed``
    """
    _log.info("orchestrator.start", document_id=document.id, court=court_config.name)

    try:
        matched = _run_press_pipeline(session, document, court_config)
    except Exception as exc:
        _log.exception(
            "orchestrator.document_error",
            document_id=document.id,
            error=str(exc),
        )
        if processing_state is not None:
            mark_processing_temporary_failure(session, processing_state, error=str(exc))
        raise

    if processing_state is not None:
        from sqlalchemy import func as _func  # noqa: PLC0415

        candidate_count = session.execute(
            select(_func.count(CaseMatchCandidate.id)).where(
                CaseMatchCandidate.source_document_id == document.id,
            )
        ).scalar_one()
        mark_processing_success(
            session,
            processing_state,
            result_count=len(matched),
            candidate_count=candidate_count,
        )

    return matched


def _run_press_pipeline(
    session: Session,
    document: SourceDocument,
    court_config: SourceConfig,
) -> list[Case]:
    """Inner pipeline — pure logic, no processing-state I/O."""
    extraction_text = document.text or ""

    article_facts = extract_articles(extraction_text, source_url=document.url)
    date_facts = extract_dates(extraction_text, source_url=document.url)
    name_facts = extract_name_candidates(extraction_text, source_url=document.url)

    event_classification = classify_press_event(extraction_text)

    article = article_facts[0].value.get("article") if article_facts else None
    person_name = _pick_person_name(name_facts, article_facts, extraction_text)

    result_date = _pick_result_date(event_classification, date_facts, extraction_text)
    publication_date_hint = _publication_date(document.published_at)

    _log.info(
        "orchestrator.extracted",
        document_id=document.id,
        article=article,
        result_date=result_date,
        publication_date_hint=publication_date_hint,
        event_type=event_classification.event_type,
        person_name=person_name,
    )

    adapter = SudrfCaseSearchAdapter(court_config)
    search_results, attempts = progressive_search(
        adapter=adapter,
        court=court_config.name,
        article=article,
        result_date=result_date,
        publication_date_hint=publication_date_hint,
        person_name=person_name if _should_search_by_person(event_classification) else None,
    )

    _log.info(
        "orchestrator.search_results",
        document_id=document.id,
        count=len(search_results),
        attempts=len(attempts),
    )

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
                    result_date=result_date,
                    person_name=person_name,
                    press_doc=document,
                )
                if case is not None:
                    matched_cases.append(case)
        except Exception as e:
            _log.exception("orchestrator.case_error", document_id=document.id, error=str(e))

    _log.info("orchestrator.complete", document_id=document.id, matched=len(matched_cases))
    return matched_cases


def progressive_search(
    adapter: SudrfCaseSearchAdapter,
    *,
    court: str,
    article: str | None,
    result_date: date | None,
    publication_date_hint: date | None,
    person_name: str | None,
) -> tuple[list[SudrfCaseSearchResult], list[SearchAttempt]]:
    """Execute a controlled sequence of search attempts.

    Strategy order (verified live on yovs 2026-08-12 — see
    ``.cache/sudrf-live-discovery-2026-08-12.md`` for evidence):
      A. article + result_date    (tightest plausible match for a verdict)
      B. article                  (fallback: drop the date if A returns 0)
      C. article + pub_date_hint  (publication date of the press release,
                                   weak signal — used only when A and B missed)
      D. person + article         (used as a last resort; modern cards often
                                   hide the defendant name, so this is rarely
                                   useful)

    Each attempt is logged as :class:`SearchAttempt` and the collected results
    are deduplicated on ``case_uid`` (fallback: ``court + case_number``). The
    function returns the ordered list of results plus the list of attempts so
    the orchestrator can emit the audit trail.
    """
    from court_monitor.sources.sudrf_dto import (  # noqa: PLC0415
        SudrfCaseSearchCriteria,
    )

    attempts: list[SearchAttempt] = []
    seen_keys: set[tuple[str | None, str | None]] = set()
    results: list[SudrfCaseSearchResult] = []

    strategies: list[tuple[str, SudrfCaseSearchCriteria]] = []

    if article is not None:
        strategies.append(
            (
                "article_result_date",
                SudrfCaseSearchCriteria(
                    court=court,
                    article=article,
                    result_date=result_date,
                ),
            )
        )
        if result_date is not None:
            strategies.append(
                (
                    "article_only",
                    SudrfCaseSearchCriteria(court=court, article=article),
                )
            )
            strategies.append(
                (
                    "article_publication_date",
                    SudrfCaseSearchCriteria(
                        court=court,
                        article=article,
                        result_date=publication_date_hint,
                    ),
                )
            )

    if person_name and article is not None:
        strategies.append(
            (
                "person_article",
                SudrfCaseSearchCriteria(
                    court=court,
                    article=article,
                    person_name=person_name,
                ),
            )
        )

    for strategy_name, criteria in strategies:
        try:
            found = adapter.search(criteria)
        except Exception as e:  # pragma: no cover - transport-level failure
            _log.warning(
                "progressive_search.error",
                strategy=strategy_name,
                error=str(e),
            )
            attempts.append(SearchAttempt(strategy_name, criteria, 0, error=str(e)))
            continue

        attempts.append(SearchAttempt(strategy_name, criteria, len(found)))

        for r in found:
            key = (r.case_uid, None) if r.case_uid else (court, r.case_number)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            results.append(r)

        if results:
            break

    _log.info(
        "progressive_search.summary",
        court=court,
        attempts=[(a.strategy, a.result_count) for a in attempts],
        deduped=len(results),
    )
    return results, attempts


def _should_search_by_person(event_classification) -> bool:
    """Person name only belongs in search queries for verdict events.

    For other event types (hearing_scheduled, case_received) the press text
    describes procedure, and including the person name in the remote search
    would artificially narrow the result set.
    """
    return event_classification.event_type == PressEventType.sentence_delivered


def _publication_date(published_at) -> date | None:
    """Coerce ``document.published_at`` to a plain ``date``."""
    if published_at is None:
        return None
    if hasattr(published_at, "date"):
        return published_at.date()
    return published_at


def _process_search_result(
    *,
    session,
    result,
    adapter,
    court_config,
    court_name,
    article,
    result_date,
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
        decision_date=result_date,
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
            case_match_candidate_id=candidate.id,
        )


def _pick_result_date(event_classification, date_facts, text) -> date | None:
    """Pick a result date from the press text based on the event type.

    Returns ``None`` for event types where the publication date is *not* a
    plausible decision date (``preventive_measure_selected``,
    ``hearing_scheduled``, ``case_received``, ``unknown``). The caller is
    expected to pass ``publication_date_hint`` separately to
    :func:`progressive_search`; it must not be silently substituted for the
    real decision/result date.

    For ``appeal_decided`` the date in the text is the appeal event date,
    not a first-instance verdict date — the caller must not use it as
    ``result_date``.
    """
    if event_classification.event_type != PressEventType.sentence_delivered:
        return None

    quote = event_classification.quote
    if quote:
        best_date = _date_closest_to(text, date_facts, quote)
        if best_date:
            return best_date

    for df in date_facts:
        date_type = df.value.get("type") if isinstance(df.value, dict) else None
        if date_type in ("verdict_date", "effective_date"):
            date_str = df.value.get("date")
            if date_str:
                try:
                    return datetime.fromisoformat(date_str).date()
                except (ValueError, TypeError):
                    _log.warning("orchestrator.date_parse_failed", date_str=date_str)

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


def process_all_pending_court_documents(
    session: Session,
    court_name: str = "2zovs",
    *,
    force_live: bool = False,
    reprocess: bool = False,
) -> int:
    """Process all pending court press release documents.

    With ``force_live=True`` the court source's backend is switched to ``http``
    regardless of what the config file says; without it the backend stays as
    configured (typically ``fixture`` for regression runs).

    With ``reprocess=True`` documents in ``processed_no_match`` are re-run;
    otherwise they are skipped (task §14).
    """
    court_config = get_source(court_name)
    if not court_config:
        _log.error("orchestrator.court_not_found", court=court_name)
        return 0

    if force_live and court_config.backend != SourceBackend.http:
        court_config = _dcreplace(court_config, backend=SourceBackend.http)

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

    to_process: list[tuple[SourceDocument, CourtDocumentProcessing]] = []
    for doc in pending_docs:
        state = get_or_create_processing_state(session, document_id=doc.id, court=court_name)
        should = should_process(state) or (
            reprocess and state.status == ProcessingStatus.PROCESSED_NO_MATCH
        )
        if should:
            to_process.append((doc, state))

    _log.info("orchestrator.pending", court=court_name, count=len(to_process))

    processed = 0
    for doc, state in to_process:
        try:
            process_court_press_document(session, doc, court_config, processing_state=state)
            processed += 1
        except Exception as exc:
            _log.exception(
                "orchestrator.document_error",
                document_id=doc.id,
                error=str(exc),
            )
            if (
                state.status not in ProcessingStatus.TERMINAL
                and state.status != ProcessingStatus.TEMPORARY_FAILURE
            ):
                mark_processing_failed(session, state, error=str(exc))

    _log.info(
        "orchestrator.batch_complete",
        court=court_name,
        processed=processed,
        total=len(to_process),
    )
    return processed
