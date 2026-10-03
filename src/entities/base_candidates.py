"""Who in the operator's own base an unnamed figurant may be.

A news item of a sentence often names nobody — «осуждена 27-летняя жительница Крыма» —
while the operator's base already holds the person, entered when the case was opened,
with the birth date, the region and the articles. So an unnamed figurant is put next to
the people of the base of that age on the day of the news, of that sex, from that place;
those charged under the same article first. The operator confirms: this is a list of who
to look at, not an identification.

The place is where the two disagree most: the news says where the person lives («жительница
Якутии»), the base where the case was opened (Бурятия). So news of a sentence is also put
next to the people of the base sentenced shortly before it under the same article, wherever
from — what the operator does by hand, going through a court's sentences for someone of
that age. They stand below the people from the place, and say that the place differs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from entities.unnamed import DIFFERENT, SAME, SUPPLIED_NAME, place_pattern

SHOWN = 5
# News of a sentence comes after the sentence: the same day from the court, weeks later
# when it is an appeal or a round-up.
SENTENCE_WINDOW = timedelta(days=90)
# A sentence the base dates a day or two after the news is the same sentence.
SENTENCE_SLACK = timedelta(days=2)
SENTENCE = "sentence"
KEY_PREFIX = "base:"

# The people of the base of that age on the day (or a year older: the event may be
# earlier than the news), of that sex — or of a sex the base does not say.
_PEOPLE = text(
    """
    SELECT full_name, normalized_name, birth_date,
           coalesce(region, '') AS region, coalesce(city, '') AS city,
           coalesce(articles, '') AS articles, sentenced_on, coalesce(court, '') AS court,
           date_part('year', age(CAST(:on AS date), birth_date))::int AS age
    FROM airtable_known_persons
    WHERE birth_date IS NOT NULL
      AND date_part('year', age(CAST(:on AS date), birth_date)) BETWEEN :age AND :age + 1
      AND (:gender = '' OR gender IS NULL OR gender = :gender)
      AND (:initial = '' OR upper(full_name) LIKE :initial || '%')
    ORDER BY full_name, birth_date
    """
)
# «ст. 205.1 УК РФ ч. 1» → «205.1».
_ARTICLE = re.compile(r"ст\.\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


@dataclass
class BaseCandidate:
    """A person of the base who may be the unnamed figurant, and why."""

    key: str
    # The name as the base writes it, without a second spelling in brackets.
    name: str
    birth_date: date
    region: str
    city: str
    articles: str
    sentenced_on: date | None
    age: int
    reasons: list[str] = field(default_factory=list)
    article_match: bool = False
    # Found by the sentence and the article, not by the place: the place differs.
    by_sentence: bool = False
    decision: str | None = None


@dataclass
class BaseCandidates:
    shown: list[BaseCandidate]
    # People of that age and sex in the base in all; `shown` are the ones from that place.
    total: int


def base_candidate_key(normalized_name: str, birth_date: date) -> str:
    """A person of the base by who they are; apart from a list entry of the same name."""
    return f"{KEY_PREFIX}{normalized_name}|{birth_date.isoformat()}"


def article_numbers(articles: str) -> set[str]:
    return set(_ARTICLE.findall(articles))


def _name(full_name: str) -> str:
    return " ".join(full_name.split("(")[0].split())


def base_candidates(session: Session, figurant: Any, *, shown: int = SHOWN) -> BaseCandidates:
    """The people of the base that may be this unnamed person.

    The age and the sex alone fit hundreds, so something must narrow them. First the
    place: the people whose region or city is the one the text names. Then, for news of a
    sentence that names an article, the people sentenced under that article within
    `SENTENCE_WINDOW` before the news, wherever the base says they are from. A text that
    gives neither shows nobody — only how many there are."""
    if figurant.age is None or figurant.published_at is None:
        return BaseCandidates([], 0)
    rows = session.execute(
        _PEOPLE,
        {
            "on": figurant.published_at.date(),
            "age": figurant.age,
            "gender": figurant.gender or "",
            "initial": (figurant.initial or "").upper(),
        },
    ).all()
    in_place = place_pattern(figurant.place)
    wanted = {str(article) for article in figurant.articles or []}
    rejected = set(
        session.scalars(
            text(
                "SELECT candidate FROM unnamed_decisions "
                "WHERE figurant_key = :key AND decision = 'different'"
            ),
            {"key": figurant.key},
        )
    )
    # «Это он» keeps the name and the birth date: the name alone is two namesakes of the
    # base, or a name somebody typed by hand.
    identified = session.execute(
        text(
            "SELECT normalized_name, rf_birth_date FROM unnamed_identity_resolutions "
            "WHERE figurant_key = :key AND resolution = :resolution"
        ),
        {"key": figurant.key, "resolution": SUPPLIED_NAME},
    ).first()
    confirmed = tuple(identified) if identified else None
    found: list[BaseCandidate] = []
    day = figurant.published_at.date()
    sentence_news = getattr(figurant, "event_type", None) == SENTENCE
    for row in rows:
        where = f"{row.region} {row.city}".lower()
        shared = sorted(wanted & article_numbers(row.articles))
        place = ", ".join(part for part in (row.region, row.city) if part)
        placed = bool(in_place and in_place.search(where))
        by_sentence = (
            not placed
            and sentence_news
            and bool(shared)
            and row.sentenced_on is not None
            and day - SENTENCE_WINDOW <= row.sentenced_on <= day + SENTENCE_SLACK
        )
        if not placed and not by_sentence:
            continue
        reasons = [f"{row.age} лет на {figurant.published_at:%d.%m.%Y}"]
        if placed:
            reasons.append(f"место: {place}")
        if shared:
            reasons.append("та же статья: " + ", ".join(shared))
        if by_sentence:
            reasons.append(
                f"приговор в базе от {row.sentenced_on:%d.%m.%Y}"
                + (f", {row.court}" if row.court else "")
            )
            reasons.append(f"место в базе другое: {place or 'не указано'}")
        elif row.sentenced_on and row.sentenced_on < day:
            reasons.append(f"в базе уже есть приговор от {row.sentenced_on:%d.%m.%Y}")
        key = base_candidate_key(row.normalized_name, row.birth_date)
        name = _name(row.full_name)
        found.append(
            BaseCandidate(
                key=key,
                name=name,
                birth_date=row.birth_date,
                region=row.region,
                city=row.city,
                articles=row.articles,
                sentenced_on=row.sentenced_on,
                age=row.age,
                reasons=reasons,
                article_match=bool(shared),
                by_sentence=by_sentence,
                decision=SAME
                if confirmed == (name, row.birth_date)
                else DIFFERENT
                if key in rejected
                else None,
            )
        )
    # Confirmed first and the rejected last; then the people from the place, the same
    # article, the exact age.
    found.sort(
        key=lambda item: (
            item.decision != SAME,
            item.decision == DIFFERENT,
            item.by_sentence,
            not item.article_match,
            item.age != figurant.age,
            item.name,
        )
    )
    return BaseCandidates(found[:shown], len(rows))
