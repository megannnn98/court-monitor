"""Who among the people other publications name an unnamed figurant may be.

A court writes «осуждена 53-летняя жительница Якутии» and names nobody; the same day a
prosecutor's office, an agency or a newspaper tells the same sentence with the name. The
operator finds the second publication by searching the web. Often it is already here —
loaded from another source, its person collected and judged a figurant — and nothing put
the two side by side.

So an unnamed figurant is put next to the figurants named in the other publications of
the same days that tell the same kind of event in the same place. What narrows them is
what the operator reads for: the same age written beside the name, the same article, the
surname's initial. It is a list of who to look at; the operator confirms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from entities.unnamed import DIFFERENT, EXISTING_PERSON, SAME, place_stems

SHOWN = 5
# The same news comes out over a few days: a court's release, then the agencies.
WINDOW = timedelta(days=3)
# How far from the name the age may stand: «…Иванова, 53 года, осуждена…».
AGE_CONTEXT = 160
KEY_PREFIX = "person:"

# The figurants named in the other publications of those days that tell of the place,
# where they are a target of an event of the same kind; with the text around the name.
_NAMED = text(
    """
    SELECT g.key, g.name, g.gender,
           a.id AS article_id, a.title, a.published_at, s.name AS source,
           m.start_offset, m.end_offset,
           substr(a.text, greatest(m.start_offset - :context, 0) + 1,
                  m.end_offset - greatest(m.start_offset - :context, 0) + :context) AS around
    FROM parsed_articles a
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    JOIN article_extraction_runs r ON r.article_id = a.id
    JOIN entity_mentions m ON m.extraction_run_id = r.id
    JOIN entity_group_mentions gm ON gm.mention_id = m.id
    JOIN entity_groups g ON g.id = gm.group_id
    JOIN entity_group_roles ro ON ro.group_id = g.id AND ro.role = 'figurant'
    WHERE a.id <> :article
      AND a.published_at BETWEEN :since AND :until
      AND lower(a.text) ~ :place
      AND EXISTS (
          SELECT 1 FROM event_entity_mentions em
          JOIN extracted_events e ON e.id = em.event_id
          WHERE em.mention_id = m.id AND (:event = '' OR e.event_type = :event)
      )
    ORDER BY a.published_at, a.id, m.start_offset
    """
)
# The articles of the Code those people are charged under in those publications.
_CHARGES = text(
    """
    SELECT g.key, c.publication_id, c.article
    FROM entity_group_charges c JOIN entity_groups g ON g.id = c.group_id
    WHERE g.key = ANY(:keys) AND c.publication_id = ANY(:publications)
    """
)
# The events a publication is searched by; «other» says nothing of the kind.
_EVENTS = frozenset({"case_opened", "detention", "arrest", "search", "charge", "sentence"})


@dataclass
class NamedCandidate:
    """A named figurant of another publication who may be the unnamed one, and why."""

    key: str
    name: str
    article_id: int
    title: str
    source: str
    published_at: datetime | None
    # The person's name in that publication's text: where the link leads.
    start: int
    end: int
    reasons: list[str] = field(default_factory=list)
    age_match: bool = False
    article_match: bool = False
    decision: str | None = None


@dataclass
class NamedCandidates:
    shown: list[NamedCandidate]
    total: int


def person_candidate_key(person_key: str) -> str:
    """A named person in the words kept on candidates; apart from a list entry's key."""
    return f"{KEY_PREFIX}{person_key}"


def _age_pattern(age: int) -> re.Pattern[str]:
    """«53 года», «53-летняя», «53 лет» — not «153» and not «53 тысячи»."""
    return re.compile(rf"(?<!\d){age}(?:\s*-?\s*лет|\s+год)", re.IGNORECASE)


def named_candidates(session: Session, figurant: Any, *, shown: int = SHOWN) -> NamedCandidates:
    """The named figurants of other publications that may be this unnamed person.

    The place, the days and the kind of event choose the publications; a person of them
    is shown when something of the person fits as well — the age written beside the
    name, an article of the Code, or the surname's initial — and the sex does not
    contradict. A text that names no place has no other publication to be found by."""
    stems = place_stems(figurant.place)
    if not stems or figurant.published_at is None:
        return NamedCandidates([], 0)
    rows = session.execute(
        _NAMED,
        {
            "article": figurant.article_id,
            "since": figurant.published_at - WINDOW,
            "until": figurant.published_at + WINDOW,
            # Where a word begins, as `place_pattern` reads it.
            "place": "(^|[^а-яё])(" + "|".join(re.escape(stem) for stem in stems) + ")",
            "event": figurant.event_type if figurant.event_type in _EVENTS else "",
            "context": AGE_CONTEXT,
        },
    ).all()
    wanted = {str(article) for article in figurant.articles or []}
    charges: dict[tuple[str, int], set[str]] = {}
    if rows and wanted:
        for key, publication, article in session.execute(
            _CHARGES,
            {
                "keys": sorted({row.key for row in rows}),
                "publications": sorted({row.article_id for row in rows}),
            },
        ):
            charges.setdefault((key, publication), set()).add(article)
    words: dict[str, str] = {
        candidate: decision
        for candidate, decision in session.execute(
            text(
                "SELECT candidate, decision FROM unnamed_decisions WHERE figurant_key = :key "
                "AND candidate LIKE :prefix"
            ),
            {"key": figurant.key, "prefix": f"{KEY_PREFIX}%"},
        )
    }
    confirmed = session.scalar(
        text(
            "SELECT existing_person_key FROM unnamed_identity_resolutions "
            "WHERE figurant_key = :key AND resolution = :resolution"
        ),
        {"key": figurant.key, "resolution": EXISTING_PERSON},
    )
    age = _age_pattern(figurant.age) if figurant.age is not None else None
    initial = (figurant.initial or "").strip(". ").casefold()
    found: dict[str, NamedCandidate] = {}
    people: set[str] = set()
    for row in rows:
        people.add(row.key)
        if figurant.gender and row.gender and row.gender != figurant.gender:
            continue
        age_match = bool(age and age.search(row.around or ""))
        shared = sorted(wanted & charges.get((row.key, row.article_id), set()))
        by_initial = bool(initial) and any(
            word.casefold().startswith(initial) for word in row.name.split()
        )
        if not (age_match or shared or by_initial):
            continue
        reasons = [f"{row.source}, {row.published_at:%d.%m.%Y}: тот же вид события в том же месте"]
        if age_match:
            reasons.append(f"рядом с именем назван возраст {figurant.age}")
        if shared:
            reasons.append("та же статья: " + ", ".join(shared))
        if by_initial:
            reasons.append(f"имя на «{initial.upper()}»")
        candidate = NamedCandidate(
            key=row.key,
            name=row.name,
            article_id=row.article_id,
            title=row.title,
            source=row.source,
            published_at=row.published_at,
            start=row.start_offset,
            end=row.end_offset,
            reasons=reasons,
            age_match=age_match,
            article_match=bool(shared),
            decision=SAME if row.key == confirmed else words.get(person_candidate_key(row.key)),
        )
        # One person, one row: the mention that tells the most.
        held = found.get(row.key)
        if held is None or _strength(candidate) > _strength(held):
            found[row.key] = candidate
    ranked = sorted(
        found.values(),
        key=lambda item: (
            item.decision != SAME,
            item.decision == DIFFERENT,
            -_strength(item),
            item.name,
        ),
    )
    return NamedCandidates(ranked[:shown], len(people))


def _strength(candidate: NamedCandidate) -> int:
    """The age beside the name tells more than an article half the region is charged
    under; both tell more than either."""
    return 2 * candidate.age_match + candidate.article_match
