"""Repository helpers: persistence of SourceDocument / ExtractedFact.

The repository is the ONLY place that mutates storage. Extraction and source
layers produce plain DTOs; services call the repository.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import ParserStatus
from court_monitor.normalization import normalize_fio
from court_monitor.storage.orm import (
    AuditLog,
    ExtractedFact,
    MatchCandidate,
    PersonRecord,
    ReviewItem,
    SourceDocument,
)


def find_document_by_hash_url(
    session: Session, content_hash: str, url: str
) -> SourceDocument | None:
    stmt = select(SourceDocument).where(
        SourceDocument.content_hash == content_hash,
        SourceDocument.url == url,
    )
    return session.execute(stmt).scalar_one_or_none()


def find_existing_document(
    session: Session,
    *,
    source_type: str | None,
    source_id: str | None,
    external_id: str | None,
    canonical_url: str | None,
    url: str,
    content_hash: str,
) -> tuple[SourceDocument | None, str]:
    """Find a document that represents the same material.

    Match priority (first hit wins):

    1. ``external_id`` (e.g. Telegram post id) — strongest identity;
    2. ``canonical_url``;
    3. ``(url, content_hash)`` — identical bytes at the same URL.

    Returns ``(document, match_kind)``. ``match_kind`` is ``"exact"`` when the
    content hash also matches, ``"changed"`` when the identity matches but the
    content differs (caller decides how to surface it — it never silently
    overwrites the original).
    """
    if external_id:
        stmt = select(SourceDocument).where(SourceDocument.external_id == external_id)
        if source_id is not None:
            stmt = stmt.where(SourceDocument.source_id == source_id)
        if source_type is not None:
            stmt = stmt.where(SourceDocument.source_type == source_type)
        doc = session.execute(stmt).scalar_one_or_none()
        if doc is not None:
            return doc, "exact" if doc.content_hash == content_hash else "changed"

    if canonical_url:
        stmt = select(SourceDocument).where(SourceDocument.canonical_url == canonical_url)
        doc = session.execute(stmt).scalar_one_or_none()
        if doc is not None:
            return doc, "exact" if doc.content_hash == content_hash else "changed"

    doc = find_document_by_hash_url(session, content_hash, url)
    if doc is not None:
        return doc, "exact"
    return None, "new"


def list_documents(session: Session, *, limit: int = 100, offset: int = 0) -> list[SourceDocument]:
    stmt = select(SourceDocument).order_by(SourceDocument.id.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars())


def get_document(session: Session, doc_id: int) -> SourceDocument | None:
    return session.get(SourceDocument, doc_id)


def count_documents(session: Session) -> int:
    return int(session.execute(select(func.count(SourceDocument.id))).scalar_one())


def count_facts(session: Session) -> int:
    return int(session.execute(select(func.count(ExtractedFact.id))).scalar_one())


def count_relevant_documents(session: Session) -> int:
    return int(
        session.execute(
            select(func.count(SourceDocument.id)).where(SourceDocument.relevant.is_(True))
        ).scalar_one()
    )


def upsert_document(session: Session, doc: SourceDocument) -> SourceDocument:
    """Insert a document. Caller is responsible for dedup pre-check."""
    session.add(doc)
    session.flush()
    return doc


def add_facts_from_dtos(
    session: Session, document_id: int, dtos: list[ExtractedFactDTO]
) -> list[ExtractedFact]:
    rows: list[ExtractedFact] = []
    for dto in dtos:
        row = ExtractedFact(
            document_id=document_id,
            entity=dto.entity,
            field=dto.field,
            value=dto.value,
            verification_status=str(dto.verification_status),
            confidence=float(dto.confidence),
            quote=dto.quote,
            extraction_method=dto.extraction_method,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def list_pending_documents(session: Session, *, limit: int = 100) -> list[SourceDocument]:
    stmt = (
        select(SourceDocument)
        .where(SourceDocument.parser_status == ParserStatus.pending.value)
        .order_by(SourceDocument.id.asc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def count_pending_documents(session: Session) -> int:
    return int(
        session.execute(
            select(func.count(SourceDocument.id)).where(
                SourceDocument.parser_status == ParserStatus.pending.value
            )
        ).scalar_one()
    )


# ---------------------------------------------------------------------------
# PersonRecord (Rosfinmonitoring / external registries)
# ---------------------------------------------------------------------------


def find_person_record(
    session: Session, *, source: str, normalized_name: str, birth_date: str | None
) -> PersonRecord | None:
    stmt = select(PersonRecord).where(
        PersonRecord.source == source,
        PersonRecord.normalized_name == normalized_name,
        PersonRecord.birth_date == birth_date,
    )
    return session.execute(stmt).scalar_one_or_none()


def upsert_person_record(session: Session, rec: PersonRecord) -> tuple[PersonRecord, bool, bool]:
    """Insert a person record unless the same (source, name, birth_date) exists.

    Returns (record, created, updated).
    """
    existing = find_person_record(
        session,
        source=rec.source,
        normalized_name=rec.normalized_name,
        birth_date=rec.birth_date,
    )
    if existing is not None:
        updated = _refresh_person_record(existing, rec)
        if updated:
            session.flush()
        return existing, False, updated
    session.add(rec)
    session.flush()
    return rec, True, False


def _refresh_person_record(existing: PersonRecord, incoming: PersonRecord) -> bool:
    """Refresh mutable registry fields from the newest source import."""
    updates = {
        "raw_name": incoming.raw_name,
        "search_name": incoming.search_name,
        "normalization_confidence": incoming.normalization_confidence,
        "normalization_method": incoming.normalization_method,
        "birth_place": incoming.birth_place,
        "category": incoming.category,
        "source_ref": incoming.source_ref,
        "added_date": incoming.added_date,
        "source_url": incoming.source_url,
        "raw_line": incoming.raw_line,
        "gender": incoming.gender,
        "country": incoming.country,
        "region": incoming.region,
        "extra_json": incoming.extra_json,
    }
    changed = False
    for attr, value in updates.items():
        if getattr(existing, attr) != value:
            setattr(existing, attr, value)
            changed = True
    if changed:
        existing.fetched_at = datetime.now(UTC)
    return changed


def list_person_records(
    session: Session, *, source: str | None = None, limit: int = 100
) -> list[PersonRecord]:
    stmt = select(PersonRecord).order_by(PersonRecord.id.desc())
    if source is not None:
        stmt = stmt.where(PersonRecord.source == source)
    stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars())


def get_person_record(session: Session, record_id: int) -> PersonRecord | None:
    return session.get(PersonRecord, record_id)


def count_person_records(session: Session, *, source: str | None = None) -> int:
    stmt = select(func.count(PersonRecord.id))
    if source is not None:
        stmt = stmt.where(PersonRecord.source == source)
    return int(session.execute(stmt).scalar_one())


# ---------------------------------------------------------------------------
# MatchCandidate
# ---------------------------------------------------------------------------


def list_match_candidates(
    session: Session, *, status: str | None = None, limit: int = 100
) -> list[MatchCandidate]:
    stmt = select(MatchCandidate).order_by(MatchCandidate.score.desc())
    if status is not None:
        stmt = stmt.where(MatchCandidate.status == status)
    stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars())


def get_match_candidate(session: Session, candidate_id: int) -> MatchCandidate | None:
    return session.get(MatchCandidate, candidate_id)


def count_match_candidates(session: Session, *, status: str | None = None) -> int:
    stmt = select(func.count(MatchCandidate.id))
    if status is not None:
        stmt = stmt.where(MatchCandidate.status == status)
    return int(session.execute(stmt).scalar_one())


def update_match_status(
    session: Session,
    candidate_id: int,
    new_status: str,
    comment: str | None = None,
    *,
    actor: str = "unknown",
    correlation_id: str | None = None,
) -> MatchCandidate | None:
    """Update a match candidate's status (confirm/reject) and record an audit entry."""
    candidate = session.get(MatchCandidate, candidate_id)
    if candidate is None:
        return None
    old_status = candidate.status
    candidate.status = new_status
    candidate.reviewed_at = datetime.now(UTC)
    candidate.review_comment = comment
    session.flush()
    create_audit_log_entry(
        session,
        actor=actor,
        action="match_status_change",
        object_type="match_candidate",
        object_id=candidate.id,
        old_value={"status": old_status},
        new_value={"status": new_status, "comment": comment},
        correlation_id=correlation_id,
    )
    return candidate


# ---------------------------------------------------------------------------
# ReviewItem
# ---------------------------------------------------------------------------


def create_review_item(
    session: Session,
    *,
    item_type: str,
    priority: str = "medium",
    document_id: int | None = None,
    source_id: str | None = None,
    source_url: str | None = None,
    data: dict[str, Any] | None = None,
) -> ReviewItem:
    item = ReviewItem(
        item_type=item_type,
        priority=priority,
        document_id=document_id,
        source_id=source_id,
        source_url=source_url,
        data_json=json.dumps(data, ensure_ascii=False) if data is not None else None,
        status="pending",
    )
    session.add(item)
    session.flush()
    return item


def upsert_review_item(
    session: Session,
    *,
    item_type: str,
    priority: str = "medium",
    document_id: int | None = None,
    source_id: str | None = None,
    source_url: str | None = None,
    data: dict[str, Any] | None = None,
) -> ReviewItem:
    """Create a review item, or refresh a still-open one for the same key.

    Without this, a document that keeps failing to parse (e.g. re-run via
    ``reprocess-document`` while the underlying bug is unfixed) — or a source
    that stays blocked across repeated ``fetch-source`` runs — would pile up
    a duplicate pending ReviewItem on every attempt. Mirrors the existing
    pending-candidate check in ``matching/candidates.py`` for MatchCandidate.
    Once an item is resolved/dismissed, the next occurrence opens a new one.

    Dedup key: ``document_id`` when present (document-level problems, e.g.
    ``parser_failed``); otherwise ``source_id`` (source-level problems, e.g.
    ``source_blocked``, which have no document). With neither, every call
    creates a new item — there is no key to dedup on.
    """
    existing: ReviewItem | None = None
    if document_id is not None:
        stmt = select(ReviewItem).where(
            ReviewItem.document_id == document_id,
            ReviewItem.item_type == item_type,
            ReviewItem.status == "pending",
        )
        existing = session.execute(stmt).scalar_one_or_none()
    elif source_id is not None:
        stmt = select(ReviewItem).where(
            ReviewItem.document_id.is_(None),
            ReviewItem.source_id == source_id,
            ReviewItem.item_type == item_type,
            ReviewItem.status == "pending",
        )
        existing = session.execute(stmt).scalar_one_or_none()

    if existing is not None:
        existing.priority = priority
        existing.source_id = source_id
        existing.source_url = source_url
        existing.data_json = json.dumps(data, ensure_ascii=False) if data is not None else None
        session.flush()
        return existing

    return create_review_item(
        session,
        item_type=item_type,
        priority=priority,
        document_id=document_id,
        source_id=source_id,
        source_url=source_url,
        data=data,
    )


def list_review_items(
    session: Session,
    *,
    status: str | None = None,
    item_type: str | None = None,
    limit: int = 100,
) -> list[ReviewItem]:
    stmt = select(ReviewItem).order_by(ReviewItem.id.desc())
    if status is not None:
        stmt = stmt.where(ReviewItem.status == status)
    if item_type is not None:
        stmt = stmt.where(ReviewItem.item_type == item_type)
    stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars())


def get_review_item(session: Session, item_id: int) -> ReviewItem | None:
    return session.get(ReviewItem, item_id)


def count_review_items(
    session: Session, *, status: str | None = None, item_type: str | None = None
) -> int:
    stmt = select(func.count(ReviewItem.id))
    if status is not None:
        stmt = stmt.where(ReviewItem.status == status)
    if item_type is not None:
        stmt = stmt.where(ReviewItem.item_type == item_type)
    return int(session.execute(stmt).scalar_one())


def resolve_review_item(
    session: Session,
    item_id: int,
    *,
    status: str = "resolved",
    resolved_by: str | None = None,
    comment: str | None = None,
    correlation_id: str | None = None,
) -> ReviewItem | None:
    """Resolve/dismiss a review item and record an audit entry."""
    item = session.get(ReviewItem, item_id)
    if item is None:
        return None
    old_status = item.status
    item.status = status
    item.resolved_at = datetime.now(UTC)
    item.resolved_by = resolved_by
    item.resolution_comment = comment
    session.flush()
    create_audit_log_entry(
        session,
        actor=resolved_by or "unknown",
        action="review_item_resolved",
        object_type="review_item",
        object_id=item.id,
        old_value={"status": old_status},
        new_value={"status": status, "comment": comment},
        correlation_id=correlation_id,
    )
    return item


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


def create_audit_log_entry(
    session: Session,
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: int,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=object_id,
        old_value_json=json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
        new_value_json=json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
        correlation_id=correlation_id,
    )
    session.add(entry)
    session.flush()
    return entry


def list_audit_log(
    session: Session,
    *,
    object_type: str | None = None,
    object_id: int | None = None,
    limit: int = 100,
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if object_type is not None:
        stmt = stmt.where(AuditLog.object_type == object_type)
    if object_id is not None:
        stmt = stmt.where(AuditLog.object_id == object_id)
    stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars())


def count_audit_log(
    session: Session, *, object_type: str | None = None, object_id: int | None = None
) -> int:
    stmt = select(func.count(AuditLog.id))
    if object_type is not None:
        stmt = stmt.where(AuditLog.object_type == object_type)
    if object_id is not None:
        stmt = stmt.where(AuditLog.object_id == object_id)
    return int(session.execute(stmt).scalar_one())


def search_person_records(
    session: Session, *, query: str | None = None, source: str | None = None, limit: int = 100
) -> list[PersonRecord]:
    """Find registry records by name.

    Matching goes against ``search_name`` — the normalized form — so a query
    typed in mixed case, with "ё", or with odd spacing still finds the record.
    """
    stmt = select(PersonRecord).order_by(PersonRecord.id.asc())
    if source is not None:
        stmt = stmt.where(PersonRecord.source == source)
    if query:
        needle = normalize_fio(query)
        if needle:
            stmt = stmt.where(PersonRecord.search_name.contains(needle))
    return list(session.execute(stmt.limit(limit)).scalars())


def count_person_records_matching(
    session: Session, *, query: str | None = None, source: str | None = None
) -> int:
    stmt = select(func.count(PersonRecord.id))
    if source is not None:
        stmt = stmt.where(PersonRecord.source == source)
    if query:
        needle = normalize_fio(query)
        if needle:
            stmt = stmt.where(PersonRecord.search_name.contains(needle))
    return int(session.execute(stmt).scalar_one())


def count_registry_namesakes(session: Session, surname: str, *, source: str = "rfm") -> int:
    """How many registry records share this surname.

    A candidate scoring 0.50 means only the name matched. Whether that is worth
    anything depends on how common the surname is in the registry: one Шульман
    is a very different signal from fourteen Кадыровых, and the score alone
    does not say which.
    """
    if not surname:
        return 0
    stmt = select(func.count(PersonRecord.id)).where(
        PersonRecord.source == source,
        PersonRecord.search_name.startswith(f"{surname} "),
    )
    return int(session.execute(stmt).scalar_one())


def find_other_mentions(
    session: Session, value: str, *, exclude_document_id: int | None = None, limit: int = 20
) -> list[ExtractedFact]:
    """Other documents naming the same person.

    Someone appearing across several materials is a different proposition from
    a single passing mention, and that is context the score cannot express.
    """
    needle = normalize_fio(value)
    if not needle:
        return []
    stmt = select(ExtractedFact).where(ExtractedFact.field == "full_name_original")
    if exclude_document_id is not None:
        stmt = stmt.where(ExtractedFact.document_id != exclude_document_id)
    rows = list(session.execute(stmt.limit(2000)).scalars())
    return [f for f in rows if normalize_fio(str(f.value)) == needle][:limit]
