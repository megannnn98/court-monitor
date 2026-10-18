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

from analysis.unnamed.figurants import DIFFERENT, EXISTING_PERSON, SAME
from analysis.unnamed.matching import place_stems
from ingestion import read
from processing import read as identity

SHOWN = 5
# The same news comes out over a few days: a court's release, then the agencies. Whole
# days of the calendar, either way.
WINDOW = timedelta(days=3)
# How far from the name the age may stand: «…Иванова, 53 года, осуждена…».
AGE_CONTEXT = 160
KEY_PREFIX = "person:"

# One case is told at one stage by different words: what one source calls a detention
# another calls an arrest or a charge. A sentence is a stage of its own. «other» — the
# model could not say what happened — says nothing of the stage, and nothing is compared.
_BEFORE_TRIAL = ("case_opened", "detention", "arrest", "search", "charge")
_STAGES: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(_BEFORE_TRIAL, _BEFORE_TRIAL),
    "sentence": ("sentence",),
}


@dataclass(frozen=True)
class _Named:
    """A figurant's mention in another publication, with that publication's card."""

    mention: identity.FigurantMention
    card: read.ArticleCard
    around: str | None

    @property
    def key(self) -> str:
        return self.mention.key

    @property
    def name(self) -> str:
        return self.mention.name

    @property
    def gender(self) -> str | None:
        return self.mention.gender

    @property
    def article_id(self) -> int:
        return self.mention.article_id

    @property
    def start_offset(self) -> int:
        return self.mention.start_offset

    @property
    def end_offset(self) -> int:
        return self.mention.end_offset

    @property
    def title(self) -> str:
        return self.card.title

    @property
    def published_at(self) -> datetime | None:
        return self.card.published_at

    @property
    def source(self) -> str:
        return self.card.source_name


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


def person_candidate_key(person_key: str) -> str:
    """A named person in the words kept on candidates; apart from a list entry's key."""
    return f"{KEY_PREFIX}{person_key}"


def _age_pattern(age: int) -> re.Pattern[str]:
    """«53 года», «53-летняя», «53 лет» — not «153» and not «53 тысячи»."""
    return re.compile(rf"(?<!\d){age}(?:\s*-?\s*лет|\s+год)", re.IGNORECASE)


def named_candidates(
    session: Session, figurant: Any, *, shown: int = SHOWN
) -> list[NamedCandidate]:
    """The people other publications name that may be this unnamed person.

    The place, the days and the stage of the case choose the publications. A person named
    there at such an event — someone the steps have judged a figurant, of this case or of
    another — is shown when something of the person fits as well: the age written near
    the name, an article of the Code, or an initial; and the sex does not contradict. A
    text that names no place has no other publication to be found by.

    None of it says the two are one person: the age near a name may be a neighbour's, the
    article is charged to many. It says where to look."""
    stems = place_stems(figurant.place)
    if not stems or figurant.published_at is None:
        return []
    day = figurant.published_at.replace(hour=0, minute=0, second=0, microsecond=0)
    stage = _STAGES.get(figurant.event_type)
    # The figurants named in the other publications of those days that tell of the
    # place, where they are a target of an event of the same kind; with the text
    # around the name.
    articles = read.published_matching(
        session,
        day - WINDOW,
        day + WINDOW + timedelta(days=1),
        # Where a word begins, as `place_pattern` reads it.
        "(^|[^а-яё])(" + "|".join(re.escape(stem) for stem in stems) + ")",
        other_than=figurant.article_id,
    )
    mentions = identity.figurant_mentions(session, articles, stage)
    cards = read.article_cards(session, {mention.article_id for mention in mentions})
    mentions = sorted(
        (mention for mention in mentions if mention.article_id in cards),
        key=lambda mention: (
            cards[mention.article_id].published_at or day,
            mention.article_id,
            mention.start_offset,
        ),
    )
    around = read.text_windows(
        session,
        [
            read.TextWindow(
                mention.article_id,
                max(mention.start_offset - AGE_CONTEXT, 0),
                mention.end_offset - max(mention.start_offset - AGE_CONTEXT, 0) + AGE_CONTEXT,
            )
            for mention in mentions
        ],
    )
    rows = [
        _Named(mention, cards[mention.article_id], text_around)
        for mention, text_around in zip(mentions, around, strict=True)
    ]
    wanted = {str(article) for article in figurant.articles or []}
    charges: dict[tuple[str, int], set[str]] = (
        identity.publication_charges(
            session, {row.key for row in rows}, {row.article_id for row in rows}
        )
        if rows and wanted
        else {}
    )
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
    for row in rows:
        if figurant.gender and row.gender and row.gender != figurant.gender:
            continue
        age_match = bool(age and age.search(row.around or ""))
        shared = sorted(wanted & charges.get((row.key, row.article_id), set()))
        by_initial = bool(initial) and any(
            word.casefold().startswith(initial) for word in row.name.split()
        )
        if not (age_match or shared or by_initial):
            continue
        # What was compared, said as it was: the stage only when there was one to compare.
        reasons = [
            f"{row.source}, {row.published_at:%d.%m.%Y}: то же место, "
            + ("та же стадия дела" if stage else "вид события не сравнивался — он не определён")
        ]
        if age_match:
            reasons.append(f"возраст {figurant.age} назван в тексте около имени")
        if shared:
            reasons.append("та же статья: " + ", ".join(shared))
        if by_initial:
            reasons.append(f"в имени есть слово на «{initial.upper()}»")
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
    return ranked[:shown]


def _strength(candidate: NamedCandidate) -> int:
    """The age beside the name tells more than an article half the region is charged
    under; both tell more than either."""
    return 2 * candidate.age_match + candidate.article_match
