"""Who on the Rosfinmonitoring list an unnamed figurant may be.

The operator's way, made a tool: a press release hides the name but tells the age, the
town, sometimes the surname's initial and the article; the list tells each person's
birth date and birthplace. Who is on the list, of that age on the day of the news, of
that sex, with that initial and born there, is a probable figurant; a person confirms.

The list has no date of inclusion; an entry's first snapshot is the earliest we know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from analysis.unnamed.figurants import DIFFERENT, SAME

CANDIDATES_SHOWN = 10


@dataclass
class Candidate:
    """An entry of the list that may be the unnamed person, and why."""

    key: str
    full_name: str
    birth_date: date
    birth_place: str
    age: int
    first_seen: datetime | None
    reasons: list[str] = field(default_factory=list)
    place_match: bool = False
    decision: str | None = None
    # Not on the list now: the operator's table says the row was removed, and when it
    # had been added.
    removed: bool = False
    added_on: date | None = None


@dataclass
class Candidates:
    shown: list[Candidate]
    # Entries of that age and sex (and initial) in all; `shown` are the best of them.
    total: int
    snapshot_date: datetime | None
    # The ones worth looking at: born there, or narrowed by an initial, or few in all;
    # 0 when only the age and sex tell, hundreds of them.
    likely: int = 0


def candidate_key(normalized_name: str, birth_date: date) -> str:
    """An entry by who it is, not by its id: the ids change with every snapshot."""
    return f"{normalized_name}|{birth_date.isoformat()}"


def place_stems(place: str) -> list[str]:
    """«Тюмень» → «тюме»: enough of each word of the place to find it declined in the
    list's «Г. ТЮМЕНЬ ТЮМЕНСКОЙ ОБЛАСТИ»; the kind of place is no evidence.

    A word of three letters is a place only when it is written as a name («Уфа»): «под»
    and «при» are not."""
    generic = {"область", "край", "республика", "город", "округ", "район", "автономный"}
    stems = []
    for word in re.findall(r"[А-Яа-яЁё]+", place):
        folded = word.lower()
        if folded in generic or len(folded) < 3 or (len(folded) == 3 and not word[0].isupper()):
            continue
        stems.append(folded[:-2] if len(folded) > 5 else folded[:-1])
    return stems


def place_pattern(place: str) -> re.Pattern[str] | None:
    """What finds the place in a lower-case text, or None for a place that names nothing.

    A stem counts only where a word begins: «омс» is «омск», «омская», not «томск»."""
    stems = place_stems(place)
    if not stems:
        return None
    return re.compile("(?<![а-яё])(?:" + "|".join(re.escape(stem) for stem in stems) + ")")


# The entries of the latest snapshot of that age on the day, of that sex and initial —
# and the rows the operator's own table marks as removed from the list since: a person the
# news left unnamed years ago may have been on the list then and not be now. A removed
# row still in the snapshot (added again) is the snapshot's entry, not a second candidate.
_CANDIDATES = text(
    """
    WITH latest AS (SELECT max(id) AS id FROM rosfinmonitoring_snapshots),
    listed AS (
        SELECT e.full_name, e.normalized_name, e.birth_date::date AS birth_date,
               coalesce(e.birth_place, '') AS birth_place,
               false AS removed, CAST(NULL AS date) AS added_on
        FROM rosfinmonitoring_entries e, latest
        WHERE e.snapshot_id = latest.id AND e.birth_date IS NOT NULL
    ),
    removed AS (
        SELECT DISTINCT ON (o.normalized_name, o.birth_date)
               o.full_name, o.normalized_name, o.birth_date, o.birth_place,
               true AS removed, o.added_on
        FROM rfm_operator_entries o
        WHERE o.removed AND o.birth_date IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM listed l
              WHERE l.normalized_name = o.normalized_name AND l.birth_date = o.birth_date)
        ORDER BY o.normalized_name, o.birth_date, o.added_on DESC NULLS LAST
    )
    SELECT c.full_name, c.normalized_name, c.birth_date, c.birth_place,
           date_part('year', age(CAST(:on AS date), c.birth_date))::int AS age,
           c.removed, c.added_on
    FROM (SELECT * FROM listed UNION ALL SELECT * FROM removed) c
    WHERE date_part('year', age(CAST(:on AS date), c.birth_date)) BETWEEN :age AND :age + 1
      AND (:gender = ''
           OR (:gender = 'male' AND c.full_name ~ '(ВИЧ|ОГЛЫ|УГЛИ|ИЧ)\\*?\\s*$')
           OR (:gender = 'female' AND c.full_name ~ '(ВНА|КЫЗЫ|ГЫЗЫ|ИЧНА)\\*?\\s*$'))
      AND (:initial = '' OR c.full_name LIKE :initial || '%')
    """
)
# When each entry first appeared in our snapshots.
_FIRST_SEEN = text(
    """
    SELECT e.normalized_name, e.birth_date::date, min(s.snapshot_date)
    FROM rosfinmonitoring_entries e JOIN rosfinmonitoring_snapshots s ON s.id = e.snapshot_id
    WHERE e.normalized_name = ANY(:names)
    GROUP BY e.normalized_name, e.birth_date::date
    """
)


def candidates(session: Session, figurant: Any, *, shown: int = CANDIDATES_SHOWN) -> Candidates:
    """The entries of the list that may be this unnamed person: of the age the text says on
    the day of the news (or a year older: the event may be earlier than the news), of
    that sex and initial; those born where the text says first."""
    snapshot_date = session.scalar(
        text("SELECT max(snapshot_date) FROM rosfinmonitoring_snapshots")
    )
    if figurant.age is None or figurant.published_at is None:
        return Candidates([], 0, snapshot_date)
    rows = session.execute(
        _CANDIDATES,
        {
            "on": figurant.published_at.date(),
            "age": figurant.age,
            "gender": figurant.gender or "",
            "initial": (figurant.initial or "").upper(),
        },
    ).all()
    in_place = place_pattern(figurant.place)
    rejected = {
        candidate: decision
        for candidate, decision in session.execute(
            text(
                "SELECT candidate, decision FROM unnamed_decisions "
                "WHERE figurant_key = :key AND decision = 'different'"
            ),
            {"key": figurant.key},
        ).all()
    }
    identified = session.execute(
        text(
            """
            SELECT rf_name, rf_birth_date FROM unnamed_identity_resolutions
            WHERE figurant_key = :key AND resolution = 'rf_entry'
            """
        ),
        {"key": figurant.key},
    ).first()
    same_key = (
        candidate_key(identified.rf_name, identified.rf_birth_date)
        if identified and identified.rf_name and identified.rf_birth_date
        else None
    )
    found: list[Candidate] = []
    for row in rows:
        place = row.birth_place.lower()
        place_match = bool(in_place and in_place.search(place))
        reasons = [f"{row.age} лет на {figurant.published_at:%d.%m.%Y}"]
        if figurant.gender:
            reasons.append("мужчина" if figurant.gender == "male" else "женщина")
        if figurant.initial:
            reasons.append(f"фамилия на «{figurant.initial}»")
        if place_match:
            reasons.append(f"родился: {row.birth_place}")
        if row.removed:
            reasons.append("исключён из перечня")
        key = candidate_key(row.normalized_name, row.birth_date)
        found.append(
            Candidate(
                key=key,
                full_name=row.full_name,
                birth_date=row.birth_date,
                birth_place=row.birth_place,
                age=row.age,
                first_seen=None,
                reasons=reasons,
                place_match=place_match,
                decision=SAME if key == same_key else rejected.get(key),
                removed=row.removed,
                added_on=row.added_on,
            )
        )
    # Confirmed first, then born there, then the exact age; the rejected last.
    found.sort(
        key=lambda item: (
            item.decision != SAME,
            item.decision == DIFFERENT,
            not item.place_match,
            item.age != figurant.age,
            item.full_name,
        )
    )
    # Born there, or confirmed: the ones to look at. Else an initial narrows enough, or
    # there are few; without either, a list of hundreds tells nothing — only its size.
    best = [item for item in found if item.place_match or item.decision == SAME]
    if best:
        likely = len(best)
    elif figurant.initial or len(found) <= shown:
        likely = len(found)
    else:
        likely = 0
    top = (best or found)[:shown] if likely else []
    if top:
        first_seen = {
            (name, birth): seen
            for name, birth, seen in session.execute(
                _FIRST_SEEN, {"names": [item.key.split("|")[0] for item in top]}
            ).all()
        }
        for item in top:
            item.first_seen = first_seen.get((item.key.split("|")[0], item.birth_date))
    return Candidates(top, len(found), snapshot_date, likely)
