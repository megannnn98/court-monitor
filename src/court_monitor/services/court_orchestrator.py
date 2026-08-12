"""End-to-end orchestrator for court press release processing."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
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
    CourtProcessingStats,
    ProcessingStatus,
    get_or_create_processing_state,
    mark_processing_failed,
    mark_processing_success,
    mark_processing_temporary_failure,
    should_process,
)
from court_monitor.sources.sudrf_case_search import (
    SudrfCaseSearchAdapter,
    SudrfPermanentError,
    SudrfTemporaryError,
    SudrfTransportError,
)
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


@dataclass
class PipelineOutcome:
    """Outcome of processing a single press document through the pipeline.

    Tracks both successful matches and failure modes so that the caller can
    make correct state-transition decisions:

    * ``search_result_count == 0`` after a successful search → no_match
    * all cards failed → temporary_failure (or permanent if permanent error)
    * partial card failure without candidates → temporary_failure (not no_match)
    * candidates found → review_created
    """

    matched_cases: list[Case] = field(default_factory=list)
    search_result_count: int = 0
    cards_evaluated: int = 0
    cards_transport_failed: int = 0
    cards_parse_failed: int = 0
    last_transport_error: SudrfTransportError | None = None
    search_succeeded: bool = False


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
      * success with 0 candidates (all cards evaluated, none matched) →
        ``processed_no_match``
      * search succeeded but ALL cards failed → ``temporary_failure``
      * search succeeded, partial card failure, no candidates →
        ``temporary_failure`` (not no_match — not all cards were checked)
      * permanent transport error → ``failed``
      * no search criteria available → ``failed``
      * transient exception → ``temporary_failure`` (caller may retry)
    """
    _log.info("orchestrator.start", document_id=document.id, court=court_config.name)

    try:
        outcome = _run_press_pipeline(session, document, court_config)
    except SudrfPermanentError as exc:
        _log.exception(
            "orchestrator.document_permanent_error",
            document_id=document.id,
            error=str(exc),
        )
        if processing_state is not None:
            mark_processing_failed(session, processing_state, error=str(exc))
        raise
    except SudrfTemporaryError as exc:
        _log.exception(
            "orchestrator.document_error",
            document_id=document.id,
            error=str(exc),
        )
        if processing_state is not None:
            mark_processing_temporary_failure(session, processing_state, error=str(exc))
        raise
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
        _apply_outcome_to_state(session, processing_state, outcome, document.id)

    return outcome.matched_cases


def _apply_outcome_to_state(
    session: Session,
    state: CourtDocumentProcessing,
    outcome: PipelineOutcome,
    document_id: int,
) -> None:
    """Decide the processing state based on the pipeline outcome."""
    from sqlalchemy import func as _func  # noqa: PLC0415

    candidate_count = session.execute(
        select(_func.count(CaseMatchCandidate.id)).where(
            CaseMatchCandidate.source_document_id == document_id,
        )
    ).scalar_one()

    # No search criteria → cannot determine match → failed.
    if not outcome.search_succeeded and outcome.search_result_count == 0:
        mark_processing_failed(
            session, state, error="no search criteria (article and person both absent)"
        )
        return

    # Search returned 0 results → legitimate no-match.
    if outcome.search_result_count == 0:
        mark_processing_success(session, state, result_count=0, candidate_count=0)
        return

    # Cards were fetched — check if any were evaluated vs failed.
    all_cards_failed = outcome.cards_evaluated == 0 and outcome.cards_transport_failed > 0
    partial_failure = outcome.cards_transport_failed > 0 and outcome.cards_evaluated > 0

    if all_cards_failed:
        error = str(outcome.last_transport_error or "all case cards failed")
        if isinstance(outcome.last_transport_error, SudrfPermanentError):
            mark_processing_failed(session, state, error=error)
        else:
            mark_processing_temporary_failure(session, state, error=error)
        return

    if partial_failure and candidate_count == 0:
        error = (
            f"partial card failure: {outcome.cards_evaluated} evaluated, "
            f"{outcome.cards_transport_failed} failed, no candidate found"
        )
        mark_processing_temporary_failure(session, state, error=error)
        return

    mark_processing_success(
        session,
        state,
        result_count=outcome.search_result_count,
        candidate_count=candidate_count,
    )


def _run_press_pipeline(
    session: Session,
    document: SourceDocument,
    court_config: SourceConfig,
) -> PipelineOutcome:
    """Inner pipeline — pure logic, no processing-state I/O."""
    outcome = PipelineOutcome()
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

    if article is None and person_name is None:
        _log.warning(
            "orchestrator.no_search_criteria",
            document_id=document.id,
            event_type=event_classification.event_type,
        )
        return outcome

    adapter = SudrfCaseSearchAdapter(court_config)
    search_results, attempts = progressive_search(
        adapter=adapter,
        court=court_config.name,
        article=article,
        result_date=result_date,
        publication_date_hint=publication_date_hint,
        person_name=person_name if _should_search_by_person(event_classification) else None,
    )

    outcome.search_succeeded = True
    outcome.search_result_count = len(search_results)

    _log.info(
        "orchestrator.search_results",
        document_id=document.id,
        count=len(search_results),
        attempts=len(attempts),
    )

    if not search_results:
        _log.warning("orchestrator.no_results", document_id=document.id)
        return outcome

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
                outcome.cards_evaluated += 1
                if case is not None:
                    outcome.matched_cases.append(case)
        except SudrfTemporaryError as e:
            outcome.cards_transport_failed += 1
            outcome.last_transport_error = e
            _log.warning(
                "orchestrator.card_transport_error",
                document_id=document.id,
                error=str(e),
            )
        except SudrfPermanentError as e:
            outcome.cards_transport_failed += 1
            outcome.last_transport_error = e
            _log.warning(
                "orchestrator.card_permanent_error",
                document_id=document.id,
                error=str(e),
            )
        except Exception as e:
            outcome.cards_parse_failed += 1
            _log.exception("orchestrator.case_error", document_id=document.id, error=str(e))

    _log.info(
        "orchestrator.complete",
        document_id=document.id,
        matched=len(outcome.matched_cases),
        evaluated=outcome.cards_evaluated,
        transport_failed=outcome.cards_transport_failed,
        parse_failed=outcome.cards_parse_failed,
    )
    return outcome


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
      C. person + article         (last resort; modern cards often hide the
                                   defendant name, so this is rarely useful)

    If ALL attempts fail with transport errors, the last error is re-raised
    (caller should NOT interpret this as "no cases found"). If at least one
    attempt succeeds and returns 0 results, that is a legitimate no-match.

    Each attempt is logged as :class:`SearchAttempt` and the collected results
    are deduplicated on ``case_uid`` (fallback: ``court + case_number``). The
    function returns the ordered list of results plus the list of attempts so
    the orchestrator can emit the audit trail.
    """
    from court_monitor.sources.sudrf_case_search import (  # noqa: PLC0415
        SudrfTransportError,
    )
    from court_monitor.sources.sudrf_dto import (  # noqa: PLC0415
        SudrfCaseSearchCriteria,
    )

    attempts: list[SearchAttempt] = []
    seen_keys: set[tuple[str | None, str | None]] = set()
    results: list[SudrfCaseSearchResult] = []
    any_successful_search = False
    last_transport_error: Exception | None = None

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
        except SudrfTransportError as e:
            _log.warning(
                "progressive_search.error",
                strategy=strategy_name,
                error=str(e),
            )
            attempts.append(SearchAttempt(strategy_name, criteria, 0, error=str(e)))
            last_transport_error = e
            continue

        any_successful_search = True
        attempts.append(SearchAttempt(strategy_name, criteria, len(found)))

        for r in found:
            key = (r.case_uid, None) if r.case_uid else (court, r.case_number)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            results.append(r)

        if results:
            break

    if not any_successful_search and last_transport_error is not None:
        raise last_transport_error

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
) -> CourtProcessingStats:
    """Process all pending court press release documents.

    With ``force_live=True`` the court source's backend is switched to ``http``
    regardless of what the config file says; without it the backend stays as
    configured (typically ``fixture`` for regression runs).

    With ``reprocess=True`` documents in ``processed_no_match`` are re-run;
    otherwise they are skipped (task §14).

    Returns:
        :class:`CourtProcessingStats` with per-status counters.
    """
    court_config = get_source(court_name)
    if not court_config:
        _log.error("orchestrator.court_not_found", court=court_name)
        return CourtProcessingStats()

    if force_live and court_config.backend != SourceBackend.http:
        court_config = _dcreplace(court_config, backend=SourceBackend.http)

    pending_docs = list(
        session.execute(
            select(SourceDocument).where(
                SourceDocument.source_type == "sudrf",
                SourceDocument.source_name == court_name,
                SourceDocument.parser_status == ParserStatus.parsed.value,
                SourceDocument.parser_version.like("sudrf-press-%"),
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

    stats = CourtProcessingStats()
    for doc, state in to_process:
        try:
            process_court_press_document(session, doc, court_config, processing_state=state)
            stats.processed += 1
            if state.status == ProcessingStatus.REVIEW_CREATED:
                stats.review_created += 1
            elif state.status == ProcessingStatus.PROCESSED_NO_MATCH:
                stats.no_match += 1
        except Exception as exc:
            _log.exception(
                "orchestrator.document_error",
                document_id=doc.id,
                error=str(exc),
            )
            if state.status == ProcessingStatus.FAILED:
                stats.failed += 1
            elif state.status == ProcessingStatus.TEMPORARY_FAILURE:
                stats.temporary_failures += 1
            elif (
                state.status not in ProcessingStatus.TERMINAL
                and state.status != ProcessingStatus.TEMPORARY_FAILURE
            ):
                mark_processing_failed(session, state, error=str(exc))
                stats.failed += 1

    _log.info(
        "orchestrator.batch_complete",
        court=court_name,
        processed=stats.processed,
        review_created=stats.review_created,
        no_match=stats.no_match,
        temporary_failures=stats.temporary_failures,
        failed=stats.failed,
        total=len(to_process),
    )
    return stats
