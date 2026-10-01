"""The unnamed figurants as cases for the result: «15-летний житель Канаша».

`unnamed_figurants` holds sentences, not people: one teenager of Канаш is three rows, one
per sentence that describes him. The operator wants them in the result beside the named
people — one row a person, named by what the text tells: the age, the sex, the place, the
surname's initial.

Three rules make a sentence a row of the result:

- the case is political by its article. An unnamed figurant has no verdict (step 5 reads
  the named), and half of them are common crime with no article told; so only a sentence
  that names a political article counts. One that names none stays on «Безымянные»;
- the figurant has not been identified. Once the operator says who it is, the person is in
  the result under the name;
- sentences about one person are one row. Two sentences are one person when the place,
  the sex, the age and the initial agree; a sentence that tells less (no age, no place)
  joins a case only when exactly one fits — otherwise it stays a row of its own.

Nothing here proves two sentences are one person: the rows only stand together, as a
reading aid. The sentences themselves are untouched.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache

from pymorphy3 import MorphAnalyzer
from sqlalchemy import text
from sqlalchemy.orm import Session

from entities.unnamed import IDENTIFIED, INSUFFICIENT
from persecution.classifier import POLITICAL_ARTICLES

# The operator's list of the перечень's articles: every 205.x beside the political ones.
# Kept here and not added to `POLITICAL_ARTICLES`: that set decides the verdict of the
# named people at step 5, and a bare 205 (a terrorist act) is not a verdict there.
UNNAMED_POLITICAL_ARTICLES = POLITICAL_ARTICLES | {"205", "205.1", "205.3", "205.4", "205.5"}

KEY_PREFIX = "unnamed:"


@dataclass(frozen=True)
class Sentence:
    """One row of `unnamed_figurants`, with the publication it stands in."""

    key: str
    article_id: int
    age: int | None
    gender: str
    place: str
    initial: str
    articles: tuple[str, ...]
    event_type: str
    quote: str
    explanation: str
    published_at: datetime | None
    title: str = ""
    url: str = ""
    source: str = ""


@dataclass
class Case:
    """One unnamed person of the result: the sentences that describe them."""

    sentences: list[Sentence] = field(default_factory=list)

    def _told(self, values: Iterable[str | int | None]) -> str | int | None:
        return next((value for value in values if value not in (None, "", "unknown")), None)

    @property
    def age(self) -> int | None:
        value = self._told(sentence.age for sentence in self.sentences)
        return value if isinstance(value, int) else None

    @property
    def gender(self) -> str:
        return str(self._told(sentence.gender for sentence in self.sentences) or "")

    @property
    def place(self) -> str:
        return str(self._told(sentence.place for sentence in self.sentences) or "")

    @property
    def initial(self) -> str:
        return str(self._told(sentence.initial for sentence in self.sentences) or "")

    @property
    def key(self) -> str:
        """Stable while the text tells the same: by what names the person, not by a row's
        id. A case that tells no place is its first sentence's."""
        if self.place:
            told = (self.place.lower().replace("ё", "е"), self.gender, self.age or "", self.initial)
            return KEY_PREFIX + "|".join(str(part) for part in told)
        return KEY_PREFIX + min(sentence.key for sentence in self.sentences)

    @property
    def name(self) -> str:
        return case_name(self.age, self.gender, self.place, self.initial)

    @property
    def articles(self) -> list[str]:
        return sorted({article for sentence in self.sentences for article in sentence.articles})

    @property
    def political(self) -> bool:
        return any(article in UNNAMED_POLITICAL_ARTICLES for article in self.articles)

    @property
    def latest(self) -> Sentence:
        oldest = datetime.min.replace(tzinfo=UTC)
        return max(
            self.sentences, key=lambda sentence: (sentence.published_at or oldest, sentence.key)
        )

    @property
    def last_published_at(self) -> datetime | None:
        return self.latest.published_at

    @property
    def first_published_at(self) -> datetime | None:
        dates = [sentence.published_at for sentence in self.sentences if sentence.published_at]
        return min(dates) if dates else None


@lru_cache(maxsize=1)
def _morph() -> MorphAnalyzer:
    return MorphAnalyzer()


def _word_genitive(word: str) -> str | None:
    if not word.isalpha() or (word.isupper() and len(word) > 1):
        return None
    nominative = [parse for parse in _morph().parse(word) if "nomn" in parse.tag]
    form = nominative[0].inflect({"gent"}) if nominative else None
    if form is None:
        return None
    return str(form.word).capitalize() if word[:1].isupper() else str(form.word)


@lru_cache(maxsize=4096)
def place_genitive(place: str) -> str | None:
    """«Канаш» → «Канаша», «Черноморский район» → «Черноморского района»; None when a
    word cannot be declined with confidence (an abbreviation, an unknown form) — the
    caller then gives the place as it is, in brackets."""
    words = place.split()
    if not words:
        return None
    # «Республика Крым» → «Республики Крым»: the name after the word stays as it is.
    head_only = words[0].lower() == "республика"
    declined: list[str] = []
    for position, word in enumerate(words):
        if head_only and position:
            declined.append(word)
            continue
        parts = word.split("-")
        # «Ростов-на-Дону» declines its head («Ростова-на-Дону»); «Санкт-Петербург», its
        # last part («Санкт-Петербурга»). The other parts stay as written.
        target = 0 if len(parts) > 2 and parts[1].lower() == "на" else len(parts) - 1
        form = _word_genitive(parts[target])
        if form is None:
            return None
        parts[target] = form
        declined.append("-".join(parts))
    return " ".join(declined)


def case_name(age: int | None, gender: str, place: str, initial: str) -> str:
    """«15-летний житель Канаша», «70-летняя жительница Токмака Л.», «житель
    Благовещенска»: what the text tells, and nothing it does not."""
    female = gender == "female"
    resident = "жительница" if female else "житель"
    if gender not in ("male", "female"):
        resident = "житель(ница)"
    parts = []
    if age is not None:
        parts.append(f"{age}-летняя" if female else f"{age}-летний")
    parts.append(resident)
    if place:
        declined = place_genitive(place)
        parts.append(declined if declined else f"({place})")
    else:
        parts.append("(место не названо)")
    if initial:
        parts.append(f"{initial.rstrip('.')}.")
    return " ".join(parts)


def _same(first: str | int | None, second: str | int | None) -> bool:
    """Two tellings agree: equal, or one of them tells nothing."""
    empty = (None, "", "unknown")
    return first in empty or second in empty or first == second


def _place(sentence: Sentence) -> str:
    return sentence.place.strip().lower().replace("ё", "е")


def group_cases(sentences: Sequence[Sentence]) -> list[Case]:
    """The sentences as cases, in the order their first sentences came.

    First the sentences that tell a place and an age: equal place, sex, age and a
    non-conflicting initial are one person. Then the rest, each joined to a case only
    when exactly one fits it — the same publication first, then the same place.
    """
    cases: list[Case] = []
    full = [sentence for sentence in sentences if _place(sentence) and sentence.age is not None]
    rest = [
        sentence for sentence in sentences if not (_place(sentence) and sentence.age is not None)
    ]
    for sentence in full:
        fits = [
            case
            for case in cases
            if _place(case.sentences[0]) == _place(sentence)
            and case.age == sentence.age
            and _same(case.gender, sentence.gender)
            and _same(case.initial, sentence.initial)
        ]
        if len(fits) == 1:
            fits[0].sentences.append(sentence)
        else:
            cases.append(Case([sentence]))
    for sentence in rest:
        compatible = [
            case
            for case in cases
            if _same(case.gender, sentence.gender)
            and _same(case.initial, sentence.initial)
            and _same(case.age, sentence.age)
        ]
        same_article = [
            case
            for case in compatible
            if any(other.article_id == sentence.article_id for other in case.sentences)
        ]
        same_place = [
            case
            for case in compatible
            if _place(sentence)
            and _same(_place(sentence), case.place.lower().replace("ё", "е"))
            and case.place
        ]
        fits = same_article if len(same_article) == 1 else same_place
        if len(fits) == 1:
            fits[0].sentences.append(sentence)
        else:
            cases.append(Case([sentence]))
    return cases


_SENTENCES = text(
    """
    SELECT u.key, u.article_id, u.age, coalesce(u.gender, '') AS gender, u.place,
           coalesce(u.initial, '') AS initial, u.articles, u.event_type, u.quote,
           u.explanation, coalesce(u.published_at, a.published_at) AS published_at,
           coalesce(a.title, '') AS title, d.canonical_url AS url, s.name AS source
    FROM unnamed_figurants u
    JOIN parsed_articles a ON a.id = u.article_id
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE NOT EXISTS (
        SELECT 1 FROM unnamed_identity_resolutions r
        WHERE r.figurant_key = u.key AND r.resolution = ANY(:closed)
    )
    ORDER BY coalesce(u.published_at, a.published_at) NULLS LAST, u.id
    """
)


def political_cases(session: Session) -> list[Case]:
    """The unnamed people for the result: not identified, political by an article."""
    sentences = [
        Sentence(
            key=row.key,
            article_id=row.article_id,
            age=row.age,
            gender=row.gender,
            place=row.place or "",
            initial=row.initial,
            articles=tuple(str(article).strip() for article in row.articles or ()),
            event_type=row.event_type,
            quote=row.quote,
            explanation=row.explanation,
            published_at=row.published_at,
            title=row.title,
            url=row.url or "",
            source=row.source,
        )
        for row in session.execute(_SENTENCES, {"closed": sorted(IDENTIFIED | {INSUFFICIENT})})
    ]
    return [case for case in group_cases(sentences) if case.political]
