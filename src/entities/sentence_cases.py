"""The sentences as cases, and the counts over them.

`article_sentences` holds a row per article that tells of a sentence; five articles on
one sentence are five rows. A count needs each sentence once, so the rows are folded:

- A named person's rows are one case when they share the reason or the punishment. The
  reason alone would split a case two articles call by different reasons; the
  punishment alone would split a sentence an appeal changed.
- An unnamed person's rows are one case when the region, the term and the time agree;
  with one named case of the same there, they join it («житель Тюмени» and the article
  that names him). The time is the year for a persecution — nineteen years for treason
  are not given twice a year in one region — and the month for a common crime, where
  «два года условно» is given every week. A row without a date joins the one case of
  its region and term, if there is one.
- An added fine is no part of what must agree: one article tells of it, the next does
  not. Nor is the day: articles differ by a day or give the year alone.
- A row that tells neither the region nor the punishment is a case of its own: nothing
  says whose it is.

A case shows what its latest row tells, and takes from the others what that one lacks.

The counts are made here, in code: a model asked «где наказывают суровее» chooses a
count (`entities.ask`) and reads its numbers; it never counts itself.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from statistics import mean, median
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from entities.sentences import COLONY, NOT_POLITICAL, REASONS, UNKNOWN

# What stands for every reason that is a persecution.
POLITICAL = "political"
POLITICAL_REASONS = frozenset(REASONS) - {NOT_POLITICAL, UNKNOWN}

_ROWS = text(
    """
    SELECT t.id, t.article_id, t.person, t.person_key, t.region, t.kind, t.months, t.fine_rub,
           t.in_absentia, t.sentenced_on, t.articles, t.reason, t.reason_text, t.quote,
           a.title, a.published_at, s.name AS source
    FROM article_sentences t
    JOIN parsed_articles a ON a.id = t.article_id
    JOIN source_documents d ON d.id = a.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE NOT t.hidden
    ORDER BY t.id
    """
)


@dataclass(frozen=True)
class Case:
    person: str
    named: bool
    region: str
    kind: str
    months: int
    fine_rub: int
    in_absentia: bool
    sentenced_on: str
    articles: tuple[str, ...]
    reason: str
    reason_text: str
    quote: str
    # The article the case is shown by, and every article that tells of it.
    article_id: int
    title: str
    source: str
    publications: tuple[int, ...]
    row_ids: tuple[int, ...]

    @property
    def year(self) -> int | None:
        return int(self.sentenced_on[:4]) if self.sentenced_on else None

    @property
    def imprisoned(self) -> bool:
        return self.kind == COLONY and self.months > 0


def _term(row: Any) -> tuple[str, int, int] | None:
    """The main punishment: the term, or the fine when there is no term."""
    if row.months:
        return (row.kind, row.months, 0)
    return (row.kind, 0, row.fine_rub) if row.fine_rub else None


def _named_cases(rows: Sequence[Any]) -> list[list[Any]]:
    """One person's rows, joined while they share a reason or a punishment."""
    cases: list[list[Any]] = []
    for row in rows:
        joined = [
            case
            for case in cases
            if any(
                other.reason == row.reason
                or (_term(row) is not None and _term(other) == _term(row))
                for other in case
            )
        ]
        merged = [row, *(other for case in joined for other in case)]
        cases = [case for case in cases if all(case is not one for one in joined)]
        cases.append(merged)
    return cases


def _place(row: Any) -> tuple[object, ...] | None:
    """Where and what, whenever: None for a row that does not tell both."""
    term = _term(row)
    return (row.region, *term) if row.region and term is not None else None


def _signature(row: Any) -> tuple[object, ...] | None:
    """What tells an unnamed sentence from another: where, what, and the year of a
    persecution or the month of a common crime."""
    place = _place(row)
    exact = 4 if row.reason in POLITICAL_REASONS else 7
    if place is None or len(row.sentenced_on) < exact:
        return None
    return (*place, row.sentenced_on[:exact])


def _latest(rows: Sequence[Any]) -> Any:
    return max(
        rows,
        key=lambda row: (
            row.sentenced_on,
            row.published_at.timestamp() if row.published_at else 0.0,
            row.id,
        ),
    )


def _case(rows: Sequence[Any]) -> Case:
    shown = _latest(rows)

    def told[Value](value: Callable[[Any], Value]) -> Value:
        """What the latest row tells, else the first of the others that tells it."""
        return next((value(row) for row in (shown, *rows) if value(row)), value(shown))

    named = next((row for row in rows if row.person_key), None)
    term = next((row for row in (shown, *rows) if _term(row)), shown)
    return Case(
        person=(named or shown).person,
        named=named is not None,
        region=told(lambda row: row.region),
        kind=term.kind,
        months=term.months,
        # An added fine is told by some of the articles only.
        fine_rub=max(row.fine_rub for row in rows if _term(row) == _term(term)),
        in_absentia=any(row.in_absentia for row in rows),
        sentenced_on=told(lambda row: row.sentenced_on),
        articles=tuple(told(lambda row: row.articles)),
        reason=Counter(row.reason for row in rows).most_common(1)[0][0],
        reason_text=told(lambda row: row.reason_text),
        quote=shown.quote,
        article_id=shown.article_id,
        title=shown.title,
        source=shown.source,
        publications=tuple(sorted({row.article_id for row in rows})),
        row_ids=tuple(sorted(row.id for row in rows)),
    )


def fold(rows: Iterable[Any]) -> list[Case]:
    by_person: dict[str, list[Any]] = defaultdict(list)
    unnamed: list[Any] = []
    for row in rows:
        if row.person_key:
            by_person[row.person_key].append(row)
        else:
            unnamed.append(row)
    groups = [case for person_rows in by_person.values() for case in _named_cases(person_rows)]
    # The named cases an unnamed row may be one of: by signature, when it points at one.
    by_signature: dict[tuple[object, ...], list[list[Any]]] = defaultdict(list)
    for group in groups:
        for signature in {_signature(row) for row in group} - {None}:
            by_signature[signature].append(group)  # type: ignore[index]
    nameless: dict[tuple[object, ...], list[Any]] = {}
    undated: list[Any] = []
    for row in unnamed:
        signature = _signature(row)
        if signature is None:
            undated.append(row)
        elif len(by_signature[signature]) == 1:
            by_signature[signature][0].append(row)
        elif signature in nameless:
            nameless[signature].append(row)
        else:
            nameless[signature] = [row]
            groups.append(nameless[signature])
    # A row without a date: the one case of its region and term, if there is but one.
    by_place: dict[tuple[object, ...], list[list[Any]]] = defaultdict(list)
    for group in groups:
        for place in {_place(row) for row in group} - {None}:
            by_place[place].append(group)  # type: ignore[index]
    for row in undated:
        place = _place(row)
        if place is not None and len(by_place[place]) == 1:
            by_place[place][0].append(row)
        else:
            groups.append([row])
    return sorted((_case(group) for group in groups), key=lambda case: case.row_ids)


def cases(session: Session) -> list[Case]:
    return fold(session.execute(_ROWS).all())


ANY, ONLY, EXCLUDE = "any", "only", "exclude"


@dataclass(frozen=True)
class Filters:
    """Which cases a count is over; an empty field does not narrow."""

    # Reasons of `entities.sentences.REASONS`, or `POLITICAL` for every persecution.
    reasons: tuple[str, ...] = ()
    region: str = ""
    kind: str = ""
    year_from: int = 0
    year_to: int = 0
    absentia: str = ANY
    # An article of the Criminal Code by its number: «207.3» also takes «207.3 ч. 2».
    article: str = ""

    def reason_set(self) -> frozenset[str]:
        wanted: set[str] = set()
        for reason in self.reasons:
            wanted |= POLITICAL_REASONS if reason == POLITICAL else {reason}
        return frozenset(wanted)


def _has_article(case: Case, number: str) -> bool:
    return any(item == number or item.startswith(f"{number} ") for item in case.articles)


@dataclass(frozen=True)
class Selection:
    cases: list[Case]
    # Left out because the year is asked for and the case's is not known.
    year_unknown: int = 0
    # How many the selection holds when `cases` is only its head.
    total: int = 0


def select(all_cases: Sequence[Case], filters: Filters) -> Selection:
    reasons = filters.reason_set()
    by_year = bool(filters.year_from or filters.year_to)
    kept: list[Case] = []
    year_unknown = 0
    for case in all_cases:
        if reasons and case.reason not in reasons:
            continue
        if filters.region and case.region != filters.region:
            continue
        if filters.kind and case.kind != filters.kind:
            continue
        if filters.absentia == ONLY and not case.in_absentia:
            continue
        if filters.absentia == EXCLUDE and case.in_absentia:
            continue
        if filters.article and not _has_article(case, filters.article):
            continue
        if by_year:
            if case.year is None:
                year_unknown += 1
                continue
            if filters.year_from and case.year < filters.year_from:
                continue
            if filters.year_to and case.year > filters.year_to:
                continue
        kept.append(case)
    return Selection(kept, year_unknown, len(kept))


GROUPS: dict[str, Callable[[Case], Sequence[str]]] = {
    "region": lambda case: (case.region,),
    "reason": lambda case: (case.reason,),
    "kind": lambda case: (case.kind,),
    "year": lambda case: (str(case.year) if case.year else "",),
    # A case under two articles counts under each.
    "article": lambda case: (
        tuple(dict.fromkeys(item.split()[0] for item in case.articles)) or ("",)
    ),
}
SORTS = ("cases", "mean_years", "median_years", "max_years")
# How many of the groups too small to rank are given: the head of them, as examples.
SMALL_SHOWN = 5


def _years(months: float) -> float:
    return round(months / 12, 1)


@dataclass(frozen=True)
class Group:
    name: str
    cases: int
    # Sent to prison for a known term; the three numbers below are over these.
    imprisoned: int
    mean_years: float
    median_years: float
    max_years: float
    suspended: int
    fined: int
    in_absentia: int


def _group(name: str, members: Sequence[Case]) -> Group:
    terms = [case.months for case in members if case.imprisoned]
    return Group(
        name=name,
        cases=len(members),
        imprisoned=len(terms),
        mean_years=_years(mean(terms)) if terms else 0.0,
        median_years=_years(median(terms)) if terms else 0.0,
        max_years=_years(max(terms)) if terms else 0.0,
        suspended=sum(case.kind == "suspended" for case in members),
        fined=sum(case.kind == "fine" for case in members),
        in_absentia=sum(case.in_absentia for case in members),
    )


@dataclass(frozen=True)
class Stats:
    total: Group
    groups: list[Group]
    # Cases of the selection whose group is not known (no region, no year, no article).
    unknown: int
    year_unknown: int
    # Groups with fewer prison terms than `min_imprisoned`: counted, and the largest
    # of them given apart — too few to rank, enough to be named.
    small: int = 0
    small_groups: tuple[Group, ...] = ()


def stats(
    all_cases: Sequence[Case],
    filters: Filters,
    *,
    group_by: str,
    sort: str = "cases",
    min_imprisoned: int = 0,
    limit: int = 15,
) -> Stats:
    """The selection counted by `group_by`. A sort by a term takes only groups with at
    least `min_imprisoned` prison terms: the mean of one sentence ranks nothing."""
    selection = select(all_cases, filters)
    members: dict[str, list[Case]] = defaultdict(list)
    for case in selection.cases:
        for name in GROUPS[group_by](case):
            members[name].append(case)
    unknown = len(members.pop("", []))
    groups = [_group(name, found) for name, found in members.items()]
    groups.sort(key=lambda group: (-getattr(group, sort), group.name))
    kept = [group for group in groups if group.imprisoned >= min_imprisoned]
    small = [group for group in groups if group.imprisoned < min_imprisoned]
    return Stats(
        total=_group("", selection.cases),
        groups=kept[:limit],
        unknown=unknown,
        year_unknown=selection.year_unknown,
        small=len(small),
        small_groups=tuple(small[:SMALL_SHOWN]),
    )


def listing(
    all_cases: Sequence[Case], filters: Filters, *, sort: str = "months", limit: int = 10
) -> Selection:
    """The selection's cases, the longest terms or the latest sentences first."""
    selection = select(all_cases, filters)
    if sort == "date":
        ordered = sorted(selection.cases, key=lambda case: case.sentenced_on, reverse=True)
    else:
        ordered = sorted(selection.cases, key=lambda case: case.months, reverse=True)
    return Selection(ordered[:limit], selection.year_unknown, selection.total)
