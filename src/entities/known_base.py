"""Which of the people found here the operator's own base already holds.

The operator keeps her own table of the people she has entered («Найденные люди», synced
into `airtable_known_persons`). A person the pipeline finds who is already there is not
news for her; one who is not is. So the result says, for each person, whether the base has
them — by name, because the base has nothing else: no birth date, no region.

A name is weak evidence, and the answer says how weak:

- `in_base`: the base holds exactly one record with this very name (the same words,
  whatever the order and the case) — as full as the news gives it. Still a name.
- `probably`: exactly one record holds these words and more («Андрей Попов» → «Попов
  Андрей Иванович»), or fewer (the base lacks the patronymic). One record, but a different
  spelling of the name: probably the person, not certainly.
- `namesakes`: several records fit. Which one, if any, cannot be told from a name; the
  answer gives how many and does not pick.
- no match: the base has nobody by this name.

A record may name a person twice, in two spellings («Лагода Роман Александрович (Лагода
Роман Олександрович)»): both are read, and they count as one record. Records that are no
person («Житель Курской области 1», «Неизвестный … 2») are left out: they carry a number.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.orm_models import AirtableKnownPersonRecord

Level = Literal["in_base", "probably", "namesakes", "similar"]
LEVEL_LABELS = {
    "in_base": "есть в базе",
    "probably": "вероятно, есть в базе",
    "namesakes": "тёзки в базе",
    # An unnamed person against the records without a name (`entities.known_nameless`).
    "similar": "похожие записи в базе",
}
# The answers that name several records and count them.
COUNTED = frozenset({"namesakes", "similar"})
# A record shown beside the answer: how many names to list.
SHOWN = 3

# Letters of any script: the base writes Ukrainian («Дмітрієв Юрій») and Latin spellings too.
_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)?")


@dataclass(frozen=True)
class KnownMatch:
    level: Level
    # The matched records' names as the base writes them: all of them for one record, up
    # to `SHOWN` for several. `count` is how many records fit.
    names: tuple[str, ...]
    count: int

    @property
    def label(self) -> str:
        if self.level in COUNTED:
            return f"{LEVEL_LABELS[self.level]}: {self.count}"
        return LEVEL_LABELS[self.level]


def words(name: str) -> frozenset[str]:
    """The words of a name, folded: case and «ё» do not tell two people apart."""
    return frozenset(_WORD.findall(name.lower().replace("ё", "е")))


def _spellings(full_name: str) -> list[frozenset[str]]:
    """Each spelling a record gives (the one outside the brackets, then the ones inside),
    with at least two words; none for a record with a digit — no person."""
    if any(char.isdigit() for char in full_name):
        return []
    found = []
    for part in re.split(r"[()]", full_name):
        spelled = words(part)
        if len(spelled) >= 2:
            found.append(spelled)
    return found


class KnownBase:
    """The operator's base, indexed by the words of its names."""

    def __init__(self, names: Iterable[str]) -> None:
        self._names: list[str] = []
        self._spellings: list[list[frozenset[str]]] = []
        self._index: dict[str, set[int]] = defaultdict(set)
        for name in names:
            spellings = _spellings(name)
            if not spellings:
                continue
            record = len(self._names)
            self._names.append(" ".join(name.split()))
            self._spellings.append(spellings)
            for spelled in spellings:
                for word in spelled:
                    self._index[word].add(record)

    @classmethod
    def from_session(cls, session: Session) -> KnownBase:
        return cls(
            session.scalars(
                select(AirtableKnownPersonRecord.full_name).where(AirtableKnownPersonRecord.active)
            )
        )

    def __len__(self) -> int:
        return len(self._names)

    def match(self, name: str) -> KnownMatch | None:
        """What the base says of a person with this name; None for nobody by that name
        (or a name of one word, which names half the city)."""
        wanted = words(name)
        if len(wanted) < 2:
            return None
        candidates: set[int] = set()
        for word in wanted:
            candidates |= self._index.get(word, set())
        exact: list[int] = []
        close: list[int] = []
        for record in sorted(candidates):
            fits = [spelled for spelled in self._spellings[record] if self._fits(wanted, spelled)]
            if not fits:
                continue
            (exact if any(spelled == wanted for spelled in fits) else close).append(record)
        found = exact + close
        if not found:
            return None
        names = tuple(self._names[record] for record in found[:SHOWN])
        if len(exact) == 1 and (len(wanted) >= 3 or len(found) == 1):
            # The exact record first: `found` lists the exact ones before the close ones.
            return KnownMatch("in_base", names[:1], 1)
        if len(found) == 1:
            return KnownMatch("probably", names, 1)
        return KnownMatch("namesakes", names, len(found))

    @staticmethod
    def _fits(wanted: frozenset[str], spelled: frozenset[str]) -> bool:
        """The same words, or one name holds the other's («Андрей Попов» in «Попов Андрей
        Иванович»): two words at least on the shorter side, which the callers ensure."""
        return wanted <= spelled or spelled <= wanted


# Articles as the base writes them: «ст. 228.1 УК РФ ч. 4 п. г,ст. 280 УК РФ» → 228.1, 280.
_ARTICLE = re.compile(r"ст\.\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
# An attempt, complicity, a group: they say nothing of what the case is.
_NEUTRAL_ARTICLES = frozenset({"30", "33", "35"})


@dataclass(frozen=True)
class TrackedCase:
    """The base's one record of a person, charged under an article the news names too."""

    name: str
    articles: tuple[str, ...]


class TrackedCases:
    """Whose case the operator already tracks, as far as a name and an article tell.

    A name alone is a namesake nineteen times in twenty-two (measured 2026-10-06: the
    base's record is of terrorism, the news of a bribe). The same article beside the one
    record of that name is the same case — and a case in the operator's base is one she
    tracks as political, whatever the article: a drug charge against a political
    scientist reads as common crime to every rule and model."""

    def __init__(self, records: Iterable[tuple[str, str | None]]) -> None:
        records = list(records)
        self._base = KnownBase(name for name, _articles in records)
        self._articles: dict[str, set[str]] = defaultdict(set)
        for name, articles in records:
            self._articles[" ".join(name.split())] |= (
                set(_ARTICLE.findall(articles or "")) - _NEUTRAL_ARTICLES
            )

    @classmethod
    def from_session(cls, session: Session) -> TrackedCases:
        return cls(
            (name, articles)
            for name, articles in session.execute(
                select(
                    AirtableKnownPersonRecord.full_name, AirtableKnownPersonRecord.articles
                ).where(AirtableKnownPersonRecord.active)
            )
        )

    def match(self, name: str, articles: Iterable[str]) -> TrackedCase | None:
        found = self._base.match(name)
        if found is None or found.level not in ("in_base", "probably"):
            return None
        shared = sorted(self._articles[found.names[0]] & set(articles))
        return TrackedCase(found.names[0], tuple(shared)) if shared else None
