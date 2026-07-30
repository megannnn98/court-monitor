"""Service layer: orchestrates adapters → storage → extraction.

This is the ONLY layer that mutates storage as a result of business decisions.
Sources, parsers and extractors are pure / side-effect free.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from court_monitor.config.loader import MonitoringConfig, SourceConfig, load_monitoring
from court_monitor.config.settings import settings
from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import ParserStatus, SourceType, VerificationStatus
from court_monitor.extraction.articles import extract_articles
from court_monitor.extraction.dates import extract_dates
from court_monitor.extraction.filtering import evaluate_relevance, relevance_as_facts
from court_monitor.extraction.names import extract_name_candidates
from court_monitor.extraction.ner_names import extract_name_candidates_ner
from court_monitor.normalization import normalize_fio
from court_monitor.observability import correlation_scope, get_logger
from court_monitor.parsers.sudrf_press import PARSER_VERSION, parse_press_release, to_datetime
from court_monitor.sources.base import FetchProblem, FetchResult, get_adapter
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import SourceDocument

_log = get_logger(__name__)


@dataclass
class SourceStats:
    """Counters for one fetch pass, and the only place that knows their names.

    Accumulation and rendering live here rather than in each caller. They used
    to be reimplemented per entry point over a plain dict, and the copies had
    already drifted: the CLI totalled ``blocked`` while the background job
    silently dropped it, so a blocked source was invisible to an operator who
    started the run from the web — the exact failure D-011 exists to prevent.
    """

    fetched: int = 0
    new_documents: int = 0
    duplicates: int = 0
    parsed: int = 0
    irrelevant: int = 0
    failed: int = 0
    blocked: int = 0
    already_exists_ids: list[int] = dataclass_field(default_factory=list)
    changed_ids: list[int] = dataclass_field(default_factory=list)

    def accumulate(self, other: SourceStats) -> None:
        """Add another pass's counters into this one."""
        self.fetched += other.fetched
        self.new_documents += other.new_documents
        self.duplicates += other.duplicates
        self.parsed += other.parsed
        self.irrelevant += other.irrelevant
        self.failed += other.failed
        self.blocked += other.blocked

    def as_dict(self) -> dict[str, int]:
        """Counters only — for JSON results and templates."""
        return {
            "fetched": self.fetched,
            "new": self.new_documents,
            "duplicates": self.duplicates,
            "parsed": self.parsed,
            "irrelevant": self.irrelevant,
            "failed": self.failed,
            "blocked": self.blocked,
        }

    def summary(self) -> str:
        return (
            f"fetched={self.fetched} new={self.new_documents} "
            f"duplicates={self.duplicates} parsed={self.parsed} "
            f"irrelevant={self.irrelevant} failed={self.failed} blocked={self.blocked}"
        )

    @property
    def needs_attention(self) -> bool:
        """Something an operator should look at, rather than a clean pass."""
        return bool(self.failed or self.blocked)


def _report_fetch_problem(session: Session, problem: FetchProblem) -> None:
    """Turn a FetchProblem into an operator-facing ReviewItem (D-011).

    Dedups on source_id via upsert_review_item so a source that stays
    blocked across repeated fetch-source runs doesn't pile up duplicates.
    """
    repo.upsert_review_item(
        session,
        item_type="source_blocked",
        priority="high",
        source_id=problem.source_id,
        source_url=problem.url,
        data={"health": str(problem.health), "http_status": problem.http_status},
    )


def ingest_fetch_result(session: Session, result: FetchResult) -> tuple[SourceDocument, bool]:
    """Insert a fetched document unless the same material already exists.

    Identity (in priority order): ``external_id`` → ``canonical_url`` →
    ``(url, content_hash)``. Returns ``(document, created)``. When the material
    exists but its content changed, the original is preserved untouched and the
    change is recorded in stats (the caller surfaces ``already_exists``).
    """
    existing, match_kind = repo.find_existing_document(
        session,
        source_type=str(result.source_type),
        source_id=result.source_id,
        external_id=result.external_id,
        canonical_url=result.canonical_url,
        url=result.url,
        content_hash=result.content_hash,
    )
    if existing is not None:
        return existing, False

    doc = SourceDocument(
        url=result.url,
        canonical_url=result.canonical_url,
        source_type=str(result.source_type),
        source_name=result.source_name,
        source_id=result.source_id,
        external_id=result.external_id,
        title=result.title,
        published_at=result.published_at,
        fetched_at=result.fetched_at,
        http_status=result.http_status,
        content_type=result.content_type,
        content=result.content,
        text=result.text,
        content_hash=result.content_hash,
        parser_status=ParserStatus.pending.value,
        adapter_version=result.adapter_version,
    )
    repo.upsert_document(session, doc)
    return doc, True


def _parse_structural_html(
    doc: SourceDocument,
    source_url: str | None,
    facts: list[ExtractedFactDTO],
) -> tuple[str, str]:
    """Parse sudrf-style structural HTML (title/date/body selectors).

    On parser failure, sets ``doc.parser_status``/``doc.parser_error`` and
    returns ``("", "")`` — the caller checks parser_status to short-circuit.
    """
    try:
        parsed = parse_press_release(doc.content or "")
    except Exception as exc:  # pragma: no cover - parser resilience
        _log.exception("pipeline.parse_failed", document_id=doc.id, error=str(exc))
        doc.parser_status = ParserStatus.parser_failed.value
        doc.parser_error = str(exc)
        return "", ""

    text = parsed.text or (doc.text or "")

    if parsed.title:
        doc.title = parsed.title
        facts.append(
            ExtractedFactDTO(
                entity="document",
                field="title",
                value=parsed.title,
                verification_status=VerificationStatus.confirmed,
                confidence=0.95,
                quote=parsed.title,
                source_url=source_url,
                extraction_method="parser:sudrf_press:title",
            )
        )

    if parsed.published_at is not None:
        doc.published_at = to_datetime(parsed.published_at)
        facts.append(
            ExtractedFactDTO(
                entity="document",
                field="published_at",
                value=parsed.published_at.isoformat(),
                verification_status=VerificationStatus.confirmed,
                confidence=0.95,
                quote=parsed.published_at.isoformat(),
                source_url=source_url,
                extraction_method="parser:sudrf_press:date",
            )
        )

    # Build full text for extraction: title + date text + body.
    # The body selector may not cover elements outside it (e.g. <time>),
    # so include parsed title and date representation for broader coverage.
    extraction_parts = []
    if parsed.title:
        extraction_parts.append(parsed.title)
    if parsed.published_at is not None:
        extraction_parts.append(parsed.published_at.strftime("%d.%m.%Y"))
    extraction_parts.append(text)
    extraction_text = " ".join(extraction_parts)

    doc.parser_version = PARSER_VERSION
    return text, extraction_text


def _dedupe_against(
    candidates: list[ExtractedFactDTO], existing: list[ExtractedFactDTO]
) -> list[ExtractedFactDTO]:
    """Drop candidate name facts already produced by another extractor.

    The regex and NER extractors overlap heavily — on the live corpus both
    named the same person in the same document often enough that ~29% of the
    generated match candidates were the same (document, name, record) triple
    twice, making an operator review one person two times. Comparison goes
    through ``normalize_fio`` so "Воробьёв"/"Воробьев" count as one name.
    """
    seen = {normalize_fio(str(fact.value)) for fact in existing}
    out: list[ExtractedFactDTO] = []
    for fact in candidates:
        key = normalize_fio(str(fact.value))
        if key in seen:
            continue
        seen.add(key)
        out.append(fact)
    return out


def parse_and_extract(
    session: Session,
    doc: SourceDocument,
    monitoring: MonitoringConfig,
) -> list[ExtractedFactDTO]:
    """Parse a document and persist extracted facts. Updates parser_status."""
    source_url = doc.canonical_url or doc.url
    facts: list[ExtractedFactDTO] = []

    if str(doc.source_type) == str(SourceType.sudrf):
        text, extraction_text = _parse_structural_html(doc, source_url, facts)
        if doc.parser_status == ParserStatus.parser_failed.value:
            repo.upsert_review_item(
                session,
                item_type="parser_failed",
                priority="high",
                document_id=doc.id,
                source_id=doc.source_id,
                source_url=source_url,
                data={"error": doc.parser_error},
            )
            return []
    else:
        # Non-HTML-document sources (e.g. Telegram) already carry a clean,
        # adapter-parsed body in doc.text. Re-running the sudrf structural
        # HTML parser on their raw content would mix UI chrome (author line,
        # view counters, "VIEW IN TELEGRAM" labels) into the extraction text
        # instead of the actual message — title/published_at are already set
        # at ingestion time from the adapter's FetchResult.
        text = doc.text or ""
        extraction_text = text

    # Articles + names + dates operate on the broad extraction text.
    article_facts = extract_articles(extraction_text, source_url=source_url)
    name_facts = extract_name_candidates(extraction_text, source_url=source_url)
    date_facts = extract_dates(extraction_text, source_url=source_url)

    facts.extend(article_facts)
    facts.extend(name_facts)
    facts.extend(date_facts)

    if settings.ner_mode == "spacy":
        try:
            ner_facts = extract_name_candidates_ner(extraction_text, source_url=source_url)
        except OSError:
            # Model not downloaded (`python -m spacy download ru_core_news_lg`) —
            # degrade to regex-only names rather than failing the whole document.
            _log.error("pipeline.ner_model_missing", source_url=source_url)
        else:
            facts.extend(_dedupe_against(ner_facts, name_facts))

    rel = evaluate_relevance(text, monitoring, source_url=source_url)
    facts.extend(relevance_as_facts(rel, source_url=source_url))

    if not rel.relevant:
        doc.parser_status = ParserStatus.irrelevant.value
    else:
        doc.parser_status = ParserStatus.parsed.value
    doc.relevant = bool(rel.relevant)

    repo.add_facts_from_dtos(session, doc.id, facts)
    _log.info(
        "pipeline.parsed",
        document_id=doc.id,
        facts=len(facts),
        relevant=rel.relevant,
        articles=len(article_facts),
        dates=len(date_facts),
        names=len(name_facts),
        matched_articles=rel.matched_articles,
        matched_keywords=rel.matched_keywords,
    )
    return facts


def _consume_fetch_results(
    session: Session,
    results: Iterable[FetchResult | FetchProblem],
    monitoring: MonitoringConfig,
    stats: SourceStats,
    *,
    parse_immediately: bool,
    limit: int | None = None,
    on_duplicate: Callable[[SourceDocument], None] | None = None,
) -> None:
    """Ingest one adapter's output into storage, counting as it goes.

    Both source flavours (legacy ``sources.yaml`` entries and registry
    entries) ran identical loops here; keeping one copy means a fix to the
    counting or the problem-reporting lands in both. Only what genuinely
    differs stays with the caller: how the adapter is built, and what a
    duplicate should additionally do.
    """
    for result in results:
        if limit is not None and stats.fetched >= max(0, limit):
            break
        stats.fetched += 1

        if isinstance(result, FetchProblem):
            stats.blocked += 1
            _report_fetch_problem(session, result)
            continue

        doc, created = ingest_fetch_result(session, result)
        if not created:
            stats.duplicates += 1
            if on_duplicate is not None:
                on_duplicate(doc)
            continue

        stats.new_documents += 1
        if not parse_immediately:
            continue

        parse_and_extract(session, doc, monitoring)
        if doc.parser_status == ParserStatus.parsed.value:
            stats.parsed += 1
        elif doc.parser_status == ParserStatus.irrelevant.value:
            stats.irrelevant += 1
        elif doc.parser_status == ParserStatus.parser_failed.value:
            stats.failed += 1


def process_source(
    session: Session,
    source_cfg: SourceConfig,
    monitoring: MonitoringConfig,
    *,
    parse_immediately: bool = True,
    limit: int | None = None,
) -> SourceStats:
    stats = SourceStats()
    with correlation_scope() as cid:
        _log.info("pipeline.source.start", source=source_cfg.name, correlation_id=cid)
        _consume_fetch_results(
            session,
            get_adapter(source_cfg).fetch_new(),
            monitoring,
            stats,
            parse_immediately=parse_immediately,
            limit=limit,
        )
        _log.info("pipeline.source.done", source=source_cfg.name, **stats.__dict__)
    return stats


def process_registry_source(
    session: Session,
    entry,
    monitoring: MonitoringConfig,
    *,
    live: bool = False,
    limit: int | None = None,
    fixture_path: str | None = None,
    parse_immediately: bool = True,
) -> SourceStats:
    """Fetch + ingest + parse one registry source (Telegram channel, …).

    Emits one ``already_exists`` line per duplicate material. ``live`` toggles
    real HTTP; without it a saved fixture is required.
    """
    stats = SourceStats()
    with correlation_scope() as cid:
        _log.info(
            "pipeline.registry_source.start",
            source=entry.id,
            live=live,
            limit=limit,
            correlation_id=cid,
        )
        adapter = _build_registry_adapter(entry, fixture_path=fixture_path)
        if adapter is None:
            _log.warning(
                "pipeline.registry_source.unsupported", source=entry.id, type=entry.source_type
            )
            return stats

        def _note_duplicate(doc: SourceDocument) -> None:
            _log.info(
                "pipeline.registry_source.already_exists", source=entry.id, document_id=doc.id
            )
            if doc.id:
                stats.already_exists_ids.append(doc.id)

        _consume_fetch_results(
            session,
            adapter.fetch_new(live=live, limit=limit),
            monitoring,
            stats,
            parse_immediately=parse_immediately,
            on_duplicate=_note_duplicate,
        )
        _log.info("pipeline.registry_source.done", source=entry.id, **stats.__dict__)
    return stats


def _build_registry_adapter(entry, *, fixture_path: str | None = None):
    if entry.source_type == "telegram":
        # Deferred to avoid an import cycle through sources.base.
        from court_monitor.sources.telegram_channel import (  # noqa: PLC0415
            adapter_from_registry_entry,
        )

        return adapter_from_registry_entry(entry, fixture_path=fixture_path)
    return None


def process_pending(session: Session, monitoring: MonitoringConfig | None = None) -> SourceStats:
    monitoring = monitoring or load_monitoring()
    stats = SourceStats()
    while True:
        pending = repo.list_pending_documents(session)
        if not pending:
            break
        for doc in pending:
            parse_and_extract(session, doc, monitoring)
            if doc.parser_status == ParserStatus.parsed.value:
                stats.parsed += 1
            elif doc.parser_status == ParserStatus.irrelevant.value:
                stats.irrelevant += 1
            elif doc.parser_status == ParserStatus.parser_failed.value:
                stats.failed += 1
        session.flush()
    return stats


class ReprocessWouldDiscardDecisions(RuntimeError):
    """Re-parsing would delete match candidates an operator has already ruled on."""

    def __init__(self, document_id: int | None, decided: int) -> None:
        where = f"Документ {document_id}: " if document_id is not None else "В базе "
        super().__init__(
            f"{where}{decided} совпадений уже рассмотрены оператором. "
            "Повторный разбор удалит эти решения."
        )
        self.document_id = document_id
        self.decided = decided


def count_decided_candidates(session: Session, document_id: int) -> int:
    """How many confirmed/rejected candidates hang off this document's facts."""
    from court_monitor.storage.orm import ExtractedFact, MatchCandidate  # noqa: PLC0415

    stmt = (
        select(func.count(MatchCandidate.id))
        .join(ExtractedFact, MatchCandidate.extracted_fact_id == ExtractedFact.id)
        .where(ExtractedFact.document_id == document_id, MatchCandidate.status != "pending")
    )
    return int(session.execute(stmt).scalar_one())


def count_all_decided_candidates(session: Session) -> int:
    """How many confirmed/rejected candidates exist anywhere in the database."""
    from court_monitor.storage.orm import MatchCandidate  # noqa: PLC0415

    stmt = select(func.count(MatchCandidate.id)).where(MatchCandidate.status != "pending")
    return int(session.execute(stmt).scalar_one())


@dataclass
class ReprocessAllStats:
    """Outcome of a corpus-wide re-parse."""

    documents: int = 0
    parsed: int = 0
    irrelevant: int = 0
    failed: int = 0
    facts_before: int = 0
    facts_after: int = 0

    @property
    def facts_delta(self) -> int:
        return self.facts_after - self.facts_before


def reprocess_all(
    session: Session,
    *,
    force: bool = False,
    limit: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> ReprocessAllStats:
    """Re-parse every stored document against the current extractors.

    Extraction rules improve, but the corpus does not follow: facts stay as
    they were pulled at fetch time, so a fix in the extractor lives in the code
    and never reaches the data already collected. This applies one to the other.

    The same protection as :func:`reprocess` applies, checked once for the whole
    corpus rather than per document — a run that would wipe operator decisions
    should not delete half of them before hitting the first protected document.
    """
    if not force:
        decided = count_all_decided_candidates(session)
        if decided:
            raise ReprocessWouldDiscardDecisions(None, decided)

    stats = ReprocessAllStats(facts_before=repo.count_facts(session))
    monitoring = load_monitoring()

    doc_ids = [doc.id for doc in repo.list_documents(session, limit=limit or 1_000_000)]
    total = len(doc_ids)

    for index, doc_id in enumerate(doc_ids, start=1):
        doc = repo.get_document(session, doc_id)
        if doc is None:  # deleted between listing and processing
            continue
        for fact in list(doc.facts):
            session.delete(fact)
        doc.parser_status = ParserStatus.pending.value
        session.flush()
        parse_and_extract(session, doc, monitoring)

        stats.documents += 1
        if doc.parser_status == ParserStatus.parsed.value:
            stats.parsed += 1
        elif doc.parser_status == ParserStatus.irrelevant.value:
            stats.irrelevant += 1
        elif doc.parser_status == ParserStatus.parser_failed.value:
            stats.failed += 1
        if on_progress is not None:
            on_progress(index, total)

    session.flush()
    stats.facts_after = repo.count_facts(session)
    return stats


def reprocess(session: Session, document_id: int, *, force: bool = False) -> int | None:
    """Re-parse a single document: drop old facts, re-extract.

    Deleting the facts cascades to their MatchCandidates, which since foreign
    keys became enforced includes ones an operator already confirmed or
    rejected. Those decisions are human judgements about whether a named person
    is in a terrorist registry — losing them silently is not acceptable, so a
    document carrying any is refused unless ``force`` is passed. Untouched
    (pending) candidates are regenerated by the next match run, so the ordinary
    case is unaffected.
    """
    doc = repo.get_document(session, document_id)
    if doc is None:
        return None

    if not force:
        decided = count_decided_candidates(session, document_id)
        if decided:
            raise ReprocessWouldDiscardDecisions(document_id, decided)

    for fact in list(doc.facts):
        session.delete(fact)
    doc.parser_status = ParserStatus.pending.value
    session.flush()
    parse_and_extract(session, doc, load_monitoring())
    return doc.id


# ---------------------------------------------------------------------------
# Rosfinmonitoring (RFM) import
# ---------------------------------------------------------------------------


@dataclass
class RfmImportStats:
    total: int = 0
    imported: int = 0
    updated: int = 0
    duplicates: int = 0


class PurgeWouldDiscardDecisions(RuntimeError):
    """Replacing the registry would delete records an operator has ruled on."""

    def __init__(self, source: str, decided: int) -> None:
        super().__init__(
            f"Перечень «{source}»: {decided} совпадений уже рассмотрены оператором. "
            "Замена перечня удалит эти решения."
        )
        self.source = source
        self.decided = decided


def count_decided_candidates_for_source(session: Session, source: str) -> int:
    """Decided candidates hanging off the registry records of one source."""
    from court_monitor.storage.orm import MatchCandidate, PersonRecord  # noqa: PLC0415

    stmt = (
        select(func.count(MatchCandidate.id))
        .join(PersonRecord, MatchCandidate.person_record_id == PersonRecord.id)
        .where(PersonRecord.source == source, MatchCandidate.status != "pending")
    )
    return int(session.execute(stmt).scalar_one())


def purge_person_records(session: Session, *, source: str, force: bool = False) -> int:
    """Delete every registry record of ``source``. Returns how many were removed.

    Rosfinmonitoring publishes a full list, not a delta, so importing on top of
    an older one is only correct when both carry the same fields. They do not:
    the CSV export has no birth date at all while the live page supplies one
    for everybody, so the same human produces two different dedup keys and
    lands twice — measured, 21 277 of 22 156 names overlapped and the registry
    grew to 43 667. Replacing is the operation that actually matches how the
    source publishes.

    Deleting records cascades into their match candidates, so the same
    protection as re-parsing applies: decisions are human judgements and are
    not thrown away without ``force``.
    """
    from court_monitor.storage.orm import PersonRecord  # noqa: PLC0415

    if not force:
        decided = count_decided_candidates_for_source(session, source)
        if decided:
            raise PurgeWouldDiscardDecisions(source, decided)

    records = session.execute(select(PersonRecord).where(PersonRecord.source == source))
    removed = 0
    for record in records.scalars():
        session.delete(record)
        removed += 1
    session.flush()
    _log.info("rfm.purge.done", source=source, removed=removed)
    return removed


def import_rfm_records(
    session: Session,
    rows: list,
    *,
    source: str = "rfm",
    source_url: str | None = None,
) -> RfmImportStats:
    """Import Rosfinmonitoring person records into the database.

    Deduplicates by (source, normalized_name, birth_date).
    """
    from court_monitor.storage.orm import PersonRecord  # noqa: PLC0415

    stats = RfmImportStats()
    for row in rows:
        stats.total += 1
        rec = PersonRecord(
            source=source,
            raw_name=row.raw_name,
            search_name=row.search_name,
            normalized_name=row.normalized_name,
            normalization_confidence=row.normalization_confidence,
            normalization_method=row.normalization_method,
            birth_date=row.birth_date,
            birth_place=row.birth_place,
            category=row.category,
            source_ref=row.source_ref,
            added_date=row.added_date,
            source_url=source_url,
            raw_line=row.raw_line,
            gender=row.gender,
            country=row.country,
            region=row.region,
            extra_json=row.extra_json,
        )
        _, created, updated = repo.upsert_person_record(session, rec)
        if created:
            stats.imported += 1
        elif updated:
            stats.updated += 1
        else:
            stats.duplicates += 1
    _log.info(
        "rfm.import.done",
        source=source,
        total=stats.total,
        imported=stats.imported,
        updated=stats.updated,
        duplicates=stats.duplicates,
    )
    return stats
