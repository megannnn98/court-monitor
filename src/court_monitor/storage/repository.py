"""Repository helpers: persistence of SourceDocument / ExtractedFact.

The repository is the ONLY place that mutates storage. Extraction and source
layers produce plain DTOs; services call the repository.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from court_monitor.domain.facts import ExtractedFactDTO
from court_monitor.domain.models import ParserStatus
from court_monitor.storage.orm import ExtractedFact, MatchCandidate, PersonRecord, SourceDocument


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


def upsert_person_record(session: Session, rec: PersonRecord) -> tuple[PersonRecord, bool]:
    """Insert a person record unless the same (source, name, birth_date) exists.

    Returns (record, created).
    """
    existing = find_person_record(
        session,
        source=rec.source,
        normalized_name=rec.normalized_name,
        birth_date=rec.birth_date,
    )
    if existing is not None:
        return existing, False
    session.add(rec)
    session.flush()
    return rec, True


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
) -> MatchCandidate | None:
    """Update a match candidate's status (confirm/reject)."""
    candidate = session.get(MatchCandidate, candidate_id)
    if candidate is None:
        return None
    candidate.status = new_status
    candidate.reviewed_at = datetime.now(UTC)
    candidate.review_comment = comment
    session.flush()
    return candidate
