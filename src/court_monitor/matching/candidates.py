"""Candidate generation: find potential matches between facts and registry records.

Uses surname-based pre-filtering to avoid O(N*M) comparisons.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from court_monitor.matching.name_normalizer import normalize_name
from court_monitor.matching.score import (
    ALGORITHM_VERSION,
    CANDIDATE_THRESHOLD,
    score_match,
)
from court_monitor.storage.orm import ExtractedFact, MatchCandidate, PersonRecord


def generate_matches(session: Session) -> dict[str, int]:
    """Generate match candidates for all person facts not yet matched.

    Returns stats: {processed, facts, created, existed, no_candidates, errors}.
    """
    stats = {
        "documents_processed": 0,
        "facts_person": 0,
        "candidates_created": 0,
        "already_existed": 0,
        "no_candidates": 0,
        "errors": 0,
    }

    # Get all person facts
    person_facts = _get_person_facts(session)
    stats["facts_person"] = len(person_facts)

    # Build surname index from PersonRecord for fast lookup
    surname_index = _build_surname_index(session)

    for fact in person_facts:
        try:
            doc_name_raw = _extract_name_from_fact(fact)
            if not doc_name_raw:
                stats["errors"] += 1
                continue

            doc_name = normalize_name(doc_name_raw)
            if not doc_name.surname:
                stats["errors"] += 1
                continue

            # Pre-filter: find records with matching surname
            candidates = surname_index.get(doc_name.surname, [])

            doc_birth_date = _extract_date_from_fact(fact)
            doc_birth_place = _extract_place_from_fact(fact)

            created_any = False
            for record in candidates:
                record_name = normalize_name(record.normalized_name)
                result = score_match(
                    doc_name, record_name,
                    doc_birth_date, record.birth_date,
                    doc_birth_place=doc_birth_place,
                    record_birth_place=record.birth_place,
                )

                if result.score < CANDIDATE_THRESHOLD:
                    continue

                # Check if candidate already exists
                existing = _find_existing_candidate(session, fact.id, record.id)
                if existing is not None:
                    stats["already_existed"] += 1
                    # Update score only for pending candidates
                    if existing.status == "pending":
                        existing.score = result.score
                        existing.name_score = result.name_score
                        existing.birth_date_score = result.birth_date_score
                        existing.birthplace_score = result.birthplace_score
                        existing.reasons_json = json.dumps(result.reasons, ensure_ascii=False)
                        existing.conflicts_json = json.dumps(result.conflicts, ensure_ascii=False)
                    continue

                candidate = MatchCandidate(
                    extracted_fact_id=fact.id,
                    person_record_id=record.id,
                    score=result.score,
                    status="pending",
                    name_score=result.name_score,
                    birth_date_score=result.birth_date_score,
                    birthplace_score=result.birthplace_score,
                    reasons_json=json.dumps(result.reasons, ensure_ascii=False),
                    conflicts_json=json.dumps(result.conflicts, ensure_ascii=False),
                    algorithm_version=ALGORITHM_VERSION,
                )
                session.add(candidate)
                stats["candidates_created"] += 1
                created_any = True

            if not created_any and not candidates:
                stats["no_candidates"] += 1

        except Exception:
            stats["errors"] += 1

    session.flush()
    return stats


def _get_person_facts(session: Session) -> list[ExtractedFact]:
    """Get all ExtractedFact with field='full_name_original'."""
    stmt = select(ExtractedFact).where(ExtractedFact.field == "full_name_original")
    return list(session.execute(stmt).scalars())


def _build_surname_index(session: Session) -> dict[str, list[PersonRecord]]:
    """Build a dict: surname → list of PersonRecord for fast lookup."""
    records = list(session.execute(select(PersonRecord)).scalars())
    index: dict[str, list[PersonRecord]] = {}
    for rec in records:
        norm = normalize_name(rec.normalized_name)
        if norm.surname:
            index.setdefault(norm.surname, []).append(rec)
    return index


def _extract_name_from_fact(fact: ExtractedFact) -> str | None:
    """Extract name string from a fact's value."""
    if isinstance(fact.value, str):
        return fact.value
    if isinstance(fact.value, dict):
        return fact.value.get("name") or fact.value.get("value")
    return None


def _extract_date_from_fact(fact: ExtractedFact) -> str | None:
    """Try to find a birth date near the name fact."""
    # Check if the fact's document has date facts
    if fact.document:
        for f in fact.document.facts:
            if f.field == "date" and isinstance(f.value, dict):
                return f.value.get("date")
    return None


def _extract_place_from_fact(fact: ExtractedFact) -> str | None:
    """Try to find a birth place near the name fact."""
    # Not implemented yet — birth place is rarely in press releases
    return None


def _find_existing_candidate(
    session: Session, fact_id: int, record_id: int
) -> MatchCandidate | None:
    """Find an existing candidate for this fact+record pair."""
    stmt = select(MatchCandidate).where(
        MatchCandidate.extracted_fact_id == fact_id,
        MatchCandidate.person_record_id == record_id,
    )
    return session.execute(stmt).scalar_one_or_none()
