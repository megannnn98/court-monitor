"""Service layer: orchestrates adapters → storage → extraction.

This is the ONLY layer that mutates storage as a result of business decisions.
Sources, parsers and extractors are pure / side-effect free.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field

from sqlalchemy.orm import Session

from court_monitor.config.loader import MonitoringConfig, SourceConfig, load_monitoring
from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import ParserStatus, VerificationStatus
from court_monitor.extraction.articles import extract_articles
from court_monitor.extraction.dates import extract_dates
from court_monitor.extraction.filtering import evaluate_relevance, relevance_as_facts
from court_monitor.extraction.names import extract_name_candidates
from court_monitor.observability import correlation_scope, get_logger
from court_monitor.parsers.sudrf_press import PARSER_VERSION, parse_press_release, to_datetime
from court_monitor.sources.base import FetchResult, get_adapter
from court_monitor.storage import repository as repo
from court_monitor.storage.orm import SourceDocument

_log = get_logger(__name__)


@dataclass
class SourceStats:
    fetched: int = 0
    new_documents: int = 0
    duplicates: int = 0
    parsed: int = 0
    irrelevant: int = 0
    failed: int = 0
    already_exists_ids: list[int] = dataclass_field(default_factory=list)
    changed_ids: list[int] = dataclass_field(default_factory=list)


def ingest_fetch_result(session: Session, result: FetchResult) -> tuple[SourceDocument, bool]:
    """Insert a fetched document unless the same material already exists.

    Identity (in priority order): ``external_id`` → ``canonical_url`` →
    ``(url, content_hash)``. Returns ``(document, created)``. When the material
    exists but its content changed, the original is preserved untouched and the
    change is recorded in stats (the caller surfaces ``already_exists``).
    """
    existing, match_kind = repo.find_existing_document(
        session,
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


def parse_and_extract(
    session: Session,
    doc: SourceDocument,
    monitoring: MonitoringConfig,
) -> list[ExtractedFactDTO]:
    """Parse a document and persist extracted facts. Updates parser_status."""
    source_url = doc.canonical_url or doc.url

    try:
        parsed = parse_press_release(doc.content or "")
    except Exception as exc:  # pragma: no cover - parser resilience
        _log.exception("pipeline.parse_failed", document_id=doc.id, error=str(exc))
        doc.parser_status = ParserStatus.parser_failed.value
        doc.parser_error = str(exc)
        return []

    text = parsed.text or (doc.text or "")
    facts: list[ExtractedFactDTO] = []

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

    # Articles + names + dates operate on the broad extraction text.
    article_facts = extract_articles(extraction_text, source_url=source_url)
    name_facts = extract_name_candidates(extraction_text, source_url=source_url)
    date_facts = extract_dates(extraction_text, source_url=source_url)

    facts.extend(article_facts)
    facts.extend(name_facts)
    facts.extend(date_facts)

    rel = evaluate_relevance(text, monitoring, source_url=source_url)
    facts.extend(relevance_as_facts(rel, source_url=source_url))

    if not rel.relevant:
        doc.parser_status = ParserStatus.irrelevant.value
    else:
        doc.parser_status = ParserStatus.parsed.value
    doc.relevant = bool(rel.relevant)

    doc.parser_version = PARSER_VERSION
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


def process_source(
    session: Session,
    source_cfg: SourceConfig,
    monitoring: MonitoringConfig,
    *,
    parse_immediately: bool = True,
) -> SourceStats:
    stats = SourceStats()
    with correlation_scope() as cid:
        _log.info("pipeline.source.start", source=source_cfg.name, correlation_id=cid)
        adapter = get_adapter(source_cfg)
        for result in adapter.fetch_new():
            stats.fetched += 1
            doc, created = ingest_fetch_result(session, result)
            if not created:
                stats.duplicates += 1
                continue
            stats.new_documents += 1
            if parse_immediately:
                parse_and_extract(session, doc, monitoring)
                if doc.parser_status == ParserStatus.parsed.value:
                    stats.parsed += 1
                elif doc.parser_status == ParserStatus.irrelevant.value:
                    stats.irrelevant += 1
                elif doc.parser_status == ParserStatus.parser_failed.value:
                    stats.failed += 1
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
        for result in adapter.fetch_new(live=live, limit=limit):
            stats.fetched += 1
            doc, created = ingest_fetch_result(session, result)
            if not created:
                stats.duplicates += 1
                print("already_exists")
                if doc.id:
                    stats.already_exists_ids.append(doc.id)
                continue
            stats.new_documents += 1
            parse_and_extract(session, doc, monitoring)
            if doc.parser_status == ParserStatus.parsed.value:
                stats.parsed += 1
            elif doc.parser_status == ParserStatus.irrelevant.value:
                stats.irrelevant += 1
            elif doc.parser_status == ParserStatus.parser_failed.value:
                stats.failed += 1
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
    for doc in repo.list_pending_documents(session):
        parse_and_extract(session, doc, monitoring)
        if doc.parser_status == ParserStatus.parsed.value:
            stats.parsed += 1
        elif doc.parser_status == ParserStatus.irrelevant.value:
            stats.irrelevant += 1
        elif doc.parser_status == ParserStatus.parser_failed.value:
            stats.failed += 1
    return stats


def reprocess(session: Session, document_id: int) -> int | None:
    """Re-parse a single document: drop old facts, re-extract."""
    doc = repo.get_document(session, document_id)
    if doc is None:
        return None
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
    duplicates: int = 0


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
            normalized_name=row.normalized_name,
            normalization_confidence=row.normalization_confidence,
            birth_date=row.birth_date,
            birth_place=row.birth_place,
            category=row.category,
            source_ref=row.source_ref,
            added_date=row.added_date,
            source_url=source_url,
            raw_line=row.raw_line,
        )
        _, created = repo.upsert_person_record(session, rec)
        if created:
            stats.imported += 1
        else:
            stats.duplicates += 1
    _log.info(
        "rfm.import.done",
        source=source,
        total=stats.total,
        imported=stats.imported,
        duplicates=stats.duplicates,
    )
    return stats
