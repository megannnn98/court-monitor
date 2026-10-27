"""«Без имени в базе»: the nameless records of the operator's own base, and who on the
Rosfinmonitoring list they may be — for a person to confirm.

Each card: the record as the base writes it, what the base says of the case (sex, place,
articles, the day it was opened), the entries of the list that fit and why, and the
person's word: «Это он», «Не он». The record with the freshest candidate stands first,
so what a new snapshot of the list brought is on top."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from analysis import read as analysis_read
from analysis.operator_base.base_candidates import article_numbers
from analysis.operator_base.base_unnamed import NamelessCase, counts, nameless_cases, say
from analysis.operator_base.jurisdiction import CourtHints, Jurisdiction
from analysis.unnamed.figurants import CANDIDATE_KEY_LENGTH, DIFFERENT, SAME

PAGE_SIZE = 20

_STATUSES = {"open": "Не разобраны", "found": "Опознаны", "all": "Все"}

CLEAR = "clear"


def facts_text(case: NamelessCase) -> str:
    """What the base says of the case, as one line; read by this page and the API."""
    record = case.record
    parts = [f"{case.age} лет"] if case.age is not None else []
    if record.gender:
        parts.append("мужчина" if record.gender == "male" else "женщина")
    place = ", ".join(part for part in (record.region, record.city) if part)
    if place:
        parts.append(place)
    if record.case_opened_on:
        parts.append(f"дело возбуждено {record.case_opened_on:%d.%m.%Y}")
    if record.articles:
        parts.append(record.articles)
    return " · ".join(parts)


def case_courts(courts: Jurisdiction, case: NamelessCase) -> CourtHints:
    record = case.record
    return courts.courts(
        f"{record.region or ''} {record.city or ''}", article_numbers(record.articles or "")
    )


def base_listing(
    db: Session, status: str
) -> tuple[list[NamelessCase], dict[str, int], list[NamelessCase]]:
    """Every case, the tabs' counts and the tab's cases; read by this page and the API."""
    cases = nameless_cases(db)
    chosen = [
        case
        for case in cases
        if status == "all"
        or (status == "found" and case.confirmed is not None)
        or (status == "open" and case.confirmed is None and case.open)
    ]
    return cases, counts(cases), chosen


def apply_decide(db: Session, fields: dict[str, str]) -> str:
    """«Это он», «Не он» or «Отменить» of a candidate; commits. The record's id."""
    record = fields.get("record", "")
    candidate = fields.get("candidate", "")
    decision = fields.get("decision", "")
    if not analysis_read.known_base_record(db, record):
        raise HTTPException(status_code=400, detail="Неизвестная запись базы")
    if not 0 < len(candidate) <= CANDIDATE_KEY_LENGTH or decision not in (SAME, DIFFERENT, CLEAR):
        raise HTTPException(status_code=400, detail="Неполное решение")
    say(db, record, candidate, None if decision == CLEAR else decision)
    db.commit()
    return record
