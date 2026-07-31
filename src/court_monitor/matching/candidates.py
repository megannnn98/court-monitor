"""Candidate generation: find potential matches between facts and registry records.

Uses surname-based pre-filtering to avoid O(N*M) comparisons.
Morphological normalization (ru-name-v2) converts oblique cases to nominative.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from court_monitor.domain.models import PERSON_NAME_FIELD
from court_monitor.matching.name_normalizer import NormalizedName, normalize_name_morph
from court_monitor.matching.score import (
    ALGORITHM_VERSION,
    CANDIDATE_THRESHOLD,
    BirthDateEvidence,
    score_match,
)
from court_monitor.observability import get_logger
from court_monitor.storage.orm import ExtractedFact, MatchCandidate, PersonRecord

_log = get_logger(__name__)


@dataclass
class MatchStats:
    """Outcome of one match-generation pass.

    A dataclass like every other pipeline result (``SourceStats``,
    ``RfmImportStats``, ``ReprocessAllStats``). It used to be a bare dict whose
    valid keys lived only in a docstring, and the predictable happened: the CLI
    read a ``documents_processed`` key that was never set, so
    ``generate-matches`` printed an em-dash on every run and nobody could tell
    it apart from a real zero.
    """

    facts_person: int = 0
    candidates_created: int = 0
    already_existed: int = 0
    no_candidates: int = 0
    errors: int = 0

    def as_dict(self) -> dict[str, int]:
        """For JSON job results and templates."""
        return asdict(self)


def generate_matches(session: Session) -> MatchStats:
    """Generate match candidates for all person facts not yet matched."""
    stats = MatchStats()

    person_facts = _get_person_facts(session)
    stats.facts_person = len(person_facts)

    surname_index = _build_surname_index(session)

    for fact in person_facts:
        try:
            doc_name_raw = _extract_name_from_fact(fact)
            if not doc_name_raw:
                stats.errors += 1
                continue

            doc_name = normalize_name_morph(doc_name_raw)
            if not doc_name.surname:
                stats.errors += 1
                continue

            # Pre-filter: find records with matching surname (after normalization)
            candidates = surname_index.get(doc_name.surname, [])

            doc_birth_date = _extract_birth_date_from_fact(fact)
            doc_birth_place = _extract_place_from_fact(fact)

            matched_any = False
            for record, record_name in candidates:
                result = score_match(
                    doc_name,
                    record_name,
                    doc_birth_date,
                    record.birth_date,
                    doc_birth_place=doc_birth_place,
                    record_birth_place=record.birth_place,
                )

                if result.score < CANDIDATE_THRESHOLD:
                    continue

                matched_any = True
                existing = _find_existing_candidate(session, fact.id, record.id)
                if existing is not None:
                    stats.already_existed += 1
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
                stats.candidates_created += 1

            if not matched_any:
                stats.no_candidates += 1

        except Exception as exc:
            stats.errors += 1
            _log.exception("matching.generate_failed", fact_id=fact.id, error=str(exc))

    session.flush()
    return stats


def _get_person_facts(session: Session) -> list[ExtractedFact]:
    """Get all ExtractedFact with field='full_name_original'."""
    stmt = select(ExtractedFact).where(ExtractedFact.field == PERSON_NAME_FIELD)
    return list(session.execute(stmt).scalars())


def _build_surname_index(
    session: Session,
) -> dict[str, list[tuple[PersonRecord, NormalizedName]]]:
    """Build a dict: normalized_surname → [(PersonRecord, its NormalizedName)].

    The normalized form is kept next to the record rather than thrown away:
    building the key already computed it, and the scoring loop needs exactly
    that object. Dropping it here meant re-normalizing every registry record a
    second time inside the loop — on a 22k-row registry, twice the work for
    the same answer.
    """
    records = list(session.execute(select(PersonRecord)).scalars())
    index: dict[str, list[tuple[PersonRecord, NormalizedName]]] = {}
    for rec in records:
        norm = normalize_name_morph(rec.normalized_name)
        if norm.surname:
            index.setdefault(norm.surname, []).append((rec, norm))
    return index


def _extract_name_from_fact(fact: ExtractedFact) -> str | None:
    """Extract name string from a fact's value."""
    if isinstance(fact.value, str):
        return fact.value
    if isinstance(fact.value, dict):
        return fact.value.get("name") or fact.value.get("value")
    return None


def _extract_birth_date_from_fact(fact: ExtractedFact) -> BirthDateEvidence | None:
    """Extract birth date ONLY from the fact's quote context.

    Does NOT use document-level date facts (those are court hearing dates,
    not birth dates). Returns BirthDateEvidence with explicit precision:
    - "1983 года рождения" → BirthDateEvidence(year="1983", precision="year")
    - "1 января 1983 года рождения" → BirthDateEvidence(year="1983", month="01", day="01", precision="full")
    """
    if not fact.quote:
        return None

    # Try to extract full date first (DD.MM.YYYY or YYYY-MM-DD)
    m = re.search(r"(\d{1,2})\s+[а-яё]+\s+(\d{4})", fact.quote, re.IGNORECASE)
    if m:
        day = m.group(1).zfill(2)
        year = m.group(2)
        # Find month
        from court_monitor.extraction.dates import _MONTHS_RU  # noqa: PLC0415

        month_name = m.group(0).split()[1].lower()
        month_num = _MONTHS_RU.get(month_name)
        if month_num:
            return BirthDateEvidence(
                year=year, month=str(month_num).zfill(2), day=day, precision="full"
            )

    # Fall back to year-only
    year = _extract_year_from_text(fact.quote)
    if year:
        return BirthDateEvidence.from_year(year)

    return None


def _extract_year_from_text(text: str) -> str | None:
    """Extract a birth year from text like '1983 года рождения'."""
    # Pattern: year + "года рождения" or "году рождения"
    m = re.search(r"(\d{4})\s+год[ау]?\s+рождени", text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Pattern: year near "рожд" or "г.р." (year before or after)
    m = re.search(r"(\d{4})\s*(?:г\.?\s*р\.?|рожд\.?)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Pattern: "рожд." before year
    m = re.search(r"рожд\.?\s+(\d{4})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


# "уроженец г. Москвы", "уроженка Республики Дагестан", "родился в г. Грозном".
# Read from the fact's quote for the same reason the birth date is: the quote is
# the window around *this* name, so a place mentioned elsewhere in the document
# cannot be attached to the wrong person.
_BIRTHPLACE_RE = re.compile(
    r"(?:урожен(?:ец|ка|цем|кой)\s+|родил(?:ся|ась)\s+в\s+)"
    r"(?P<place>[А-ЯЁа-яё][А-ЯЁа-яё\s.\-]*?)"
    r"(?=[,;)]|\s+(?:и|в|на|осужден|признан|обвиня|привлеч)|$)",
    re.IGNORECASE,
)

# A place is at most a settlement-type abbreviation plus a couple of words; a
# longer run means the regex ate into the following clause.
_MAX_PLACE_TOKENS = 4


def _extract_place_from_fact(fact: ExtractedFact) -> str | None:
    """Find the birth place stated next to this name, if the quote carries one."""
    if not fact.quote:
        return None

    match = _BIRTHPLACE_RE.search(fact.quote)
    if match is None:
        return None

    place = match.group("place").strip(" .,-")
    if not place or len(place.split()) > _MAX_PLACE_TOKENS:
        return None
    return place


def _find_existing_candidate(
    session: Session, fact_id: int, record_id: int
) -> MatchCandidate | None:
    """Find an existing candidate for this fact+record pair."""
    stmt = select(MatchCandidate).where(
        MatchCandidate.extracted_fact_id == fact_id,
        MatchCandidate.person_record_id == record_id,
    )
    return session.execute(stmt).scalar_one_or_none()
