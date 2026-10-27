"""«Безымянные»: the figurants a publication does not name, and who on the Rosfinmonitoring
list they may be — for a person to confirm.

Each card: the sentence (to its publication), what the text tells (age, sex, place, the
surname's initial, the articles), the entries of the list that fit and why, and the
person's word: «Это он», «Не он», «Никого нет в перечне». The words are kept by key
(`analysis.unnamed.figurants.decide`) through every new search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from analysis import read as analysis_read
from analysis.read import Figurant, Resolution, Screened
from analysis.rosfinmonitoring import read as rf_read
from analysis.rosfinmonitoring.read import SnapshotEntry
from analysis.unnamed.figurants import (
    CANDIDATE_KEY_LENGTH,
    COMMON_CRIME,
    CRIMINAL_MOTIVE,
    DIFFERENT,
    DUPLICATE,
    EXISTING_PERSON,
    IDENTIFIED,
    INSUFFICIENT,
    NO_RF_MATCH,
    RF_ENTRY,
    SUPPLIED_NAME,
    clear_resolution,
    decide,
    forget,
    keep,
    resolve_identity,
)
from analysis.unnamed.matching import candidate_key, candidates
from processing import read as identity
from processing.evidence.unnamed import EVENT_LABELS
from processing.read import Group

PAGE_SIZE = 20

RF_SEARCH_LIMIT = 10

_STATUSES = {
    "open": "Не разобраны",
    # Not matched with the list without an age: out of the queue, still to be read.
    "no_age": "Без возраста",
    "found": "Опознаны",
    "no_rf": "Нет записи РФМ",
    "insufficient": "Недостаточно данных",
    "all": "Все",
}

# Why the search set a person aside, as the operator reads it.
_SCREENED_REASONS = {
    COMMON_CRIME: "обычная уголовная статья",
    CRIMINAL_MOTIVE: "обычное уголовное дело",
    DUPLICATE: "тот же человек уже есть на карточке этой публикации",
}


def rf_entry_key(entry: SnapshotEntry) -> str | None:
    if entry.birth_date is None:
        return None
    return candidate_key(entry.normalized_name, entry.birth_date.date())


def rf_search(db: Session, query: str) -> list[SnapshotEntry]:
    value = query.strip()
    if not value:
        return []
    return rf_read.latest_entries_named(db, value, limit=RF_SEARCH_LIMIT)


def reverse_matches(db: Session, figurants: list[Figurant], rf_key: str) -> list[Figurant]:
    """Unresolved figurants whose existing candidate lookup includes this RF entry."""
    return [
        figurant
        for figurant in figurants
        if any(candidate.key == rf_key for candidate in candidates(db, figurant).shown)
    ]


def _day(moment: datetime | None) -> str:
    return moment.astimezone().strftime("%d.%m.%Y") if moment else "—"


def rf_display_name(full_name: str) -> str:
    return " ".join(word.strip("*").lower().capitalize() for word in full_name.split())


def facts_text(figurant: Figurant) -> str:
    """What the text tells of the figurant, as one line; read by this page and the API."""
    parts = []
    if figurant.age is not None:
        parts.append(f"{figurant.age} лет")
    if figurant.gender:
        parts.append("мужчина" if figurant.gender == "male" else "женщина")
    if figurant.place:
        parts.append(figurant.place)
    if figurant.initial:
        parts.append(f"фамилия на «{figurant.initial}»")
    parts.append(EVENT_LABELS.get(figurant.event_type, figurant.event_type))
    if figurant.articles:
        parts.append("ст. " + ", ".join(str(article) for article in figurant.articles))
    return " · ".join(parts)


def screened(db: Session) -> list[Screened]:
    """Whom the search set aside on its own, latest first."""
    return analysis_read.screened(db)


def screened_reason(reason: str) -> str:
    return _SCREENED_REASONS.get(reason, reason)


def seen_text(item: Any) -> str:
    """Since when the list holds a candidate, or that it no longer does."""
    if item.removed:
        # Said of the row of the operator's table, not of the list: the list no
        # longer holds this person.
        return (
            f"включён {item.added_on:%d.%m.%Y}, позже исключён"
            if item.added_on
            else "был в перечне, исключён"
        ) + " — по таблице оператора"
    return (
        f"в перечне с {_day(item.first_seen)} или раньше"
        if item.first_seen
        else "дата включения неизвестна"
    )


@dataclass(frozen=True)
class Listing:
    """The figurants of one tab of «Безымянные», with every tab's count."""

    words: dict[str, Resolution]
    figurants: list[Figurant]
    counts: dict[str, int]
    chosen: list[Figurant]

    def state(self, figurant: Figurant) -> str:
        resolution = self.words.get(figurant.key, ("", None, None, None, None, None))[0]
        if resolution in IDENTIFIED:
            return "found"
        if resolution == INSUFFICIENT:
            return "insufficient"
        return "open" if figurant.age is not None else "no_age"


def unnamed_listing(db: Session, status: str) -> Listing:
    """The tab's figurants, latest first; read by the legacy page and `GET /api/v1/unnamed`."""
    words: dict[str, Resolution] = analysis_read.resolutions(db)
    figurants = analysis_read.figurants_latest_first(db)
    listing = Listing(words, figurants, {}, [])
    counts = {
        key: sum(listing.state(item) == key for item in figurants)
        for key in ("open", "no_age", "found", "insufficient")
    }
    counts["no_rf"] = sum(
        words.get(item.key, ("", None, None, None, None, None))[0] == NO_RF_MATCH
        for item in figurants
    )
    counts["all"] = len(figurants)
    chosen = [
        item
        for item in figurants
        if status == "all"
        or listing.state(item) == status
        or (
            status == "no_rf"
            and words.get(item.key, ("", None, None, None, None, None))[0] == NO_RF_MATCH
        )
    ]
    return Listing(words, figurants, counts, chosen)


def people_search(db: Session, person_q: str) -> list[Group]:
    """The existing people a figurant may be, by a part of the name."""
    if not person_q.strip():
        return []
    return identity.groups_named(db, person_q.strip(), limit=20)


def _known(db: Session, figurant: str) -> None:
    if not analysis_read.figurant_exists(db, figurant):
        raise HTTPException(status_code=400, detail="Неизвестный безымянный")


def apply_keep(db: Session, fields: dict[str, str]) -> str:
    """Back to the cards, whom the search set aside; commits. The figurant's key."""
    figurant = fields.get("figurant", "")
    if not keep(db, figurant):
        raise HTTPException(status_code=400, detail="Неизвестный отсеянный")
    db.commit()
    return figurant


def apply_reject(db: Session, fields: dict[str, str]) -> str:
    """«Не он» of a candidate, or with `undo` its taking back; commits."""
    figurant = fields.get("figurant", "")
    _known(db, figurant)
    if not 0 < len(fields.get("candidate", "")) <= CANDIDATE_KEY_LENGTH:
        raise HTTPException(status_code=400, detail="Не выбран человек из перечня")
    if fields.get("undo") == "1":
        # «Не он» said by mistake: back to no word at all.
        forget(db, figurant, fields.get("candidate", ""))
    else:
        decide(db, figurant, fields.get("candidate", ""), DIFFERENT)
    db.commit()
    return figurant


def apply_resolve(db: Session, fields: dict[str, str]) -> str:
    """Who the figurant is, or that nobody fits, or that too little is known; commits."""
    figurant = fields.get("figurant", "")
    _known(db, figurant)
    resolution = fields.get("resolution", "")
    birth = None
    if fields.get("rf_birth_date"):
        try:
            birth = date.fromisoformat(fields["rf_birth_date"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Некорректная дата рождения") from exc
    if resolution == EXISTING_PERSON:
        key = fields.get("existing_person_key", "")
        person = identity.groups_by_key(db, [key]).get(key)
        if person is None:
            raise HTTPException(status_code=400, detail="Не выбран существующий человек")
        resolve_identity(
            db,
            figurant,
            resolution,
            normalized_name=person.name,
            existing_person_key=person.key,
        )
    elif resolution in (RF_ENTRY, SUPPLIED_NAME, NO_RF_MATCH, INSUFFICIENT):
        try:
            resolve_identity(
                db,
                figurant,
                resolution,
                normalized_name=fields.get("normalized_name") or fields.get("rf_name") or None,
                rf_name=fields.get("rf_name") or None,
                rf_birth_date=birth,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Неполное решение") from exc
    else:
        raise HTTPException(status_code=400, detail="Неполное решение")
    db.commit()
    return figurant


def apply_clear(db: Session, fields: dict[str, str]) -> str:
    """The figurant's word taken back; commits."""
    figurant = fields.get("figurant", "")
    _known(db, figurant)
    clear_resolution(db, figurant)
    db.commit()
    return figurant
