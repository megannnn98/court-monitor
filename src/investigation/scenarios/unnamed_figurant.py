"""Scenario B: who is the person a publication describes without a name.

«В Тюмени задержан 17-летний житель города…»: the pipeline read the sentence into clues
(age, sex, place, the surname's initial, the articles) and the list holds each
person's birth date and birthplace. This scenario gathers who fits — list entries and
people of the database named in publications alike — weighs each by explicit signals
and says how sure the ranking is. It never decides an identity on its own: a strong,
clear lead is reported as the likeliest candidate, anything less goes to a person.

Rounds:
    0  the figurant's clues and the list entries they fit (structured);
    1  publications and posts naming a candidate's surname with the place, and
       publications of the place, the article and the days around the news (text);
    2  the people of the database those publications name (structured).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from investigation import intent as intent_rules
from investigation.models import (
    ArticleSubject,
    CandidateSignal,
    Conflict,
    CoverageAssessment,
    EvidenceItem,
    FactRequirement,
    FigurantCandidate,
    Finding,
    InvestigationIntent,
    InvestigationQuery,
    ScenarioName,
    Status,
    Subject,
    SubjectRef,
    canonical,
)
from investigation.scenarios.base import PlannedQuery, ScenarioView

FACT_LABELS = {
    "clues": "что публикация говорит о фигуранте",
    "candidates": "кто может быть фигурантом",
    "corroboration": "другие публикации о том же деле",
}
FACTS: Mapping[str, FactRequirement] = {
    "clues": FactRequirement(key="clues", description=FACT_LABELS["clues"]),
    "candidates": FactRequirement(key="candidates", description=FACT_LABELS["candidates"]),
    "corroboration": FactRequirement(
        key="corroboration", description=FACT_LABELS["corroboration"], required=False
    ),
}

# The weights of the signals: a list entry's age and sex make it one of hundreds; the
# initial and the birthplace narrow it; a text naming the surname with the place ties
# it to the case. A person's confirmation outweighs everything.
WEIGHTS: Mapping[str, float] = {
    "age_exact": 2.0,
    "age_near": 1.0,
    "gender": 1.0,
    "initial": 2.0,
    "birthplace": 3.0,
    "confirmed": 10.0,
    "removed_from_list": -0.5,
    "named_with_place": 3.0,
    "shared_article": 2.0,
    "same_place_publication": 2.0,
    "same_days": 1.0,
}
# A ranking a result may report as its likeliest candidate: this strong, and this far
# ahead of the next. Below either, a person decides from the list.
STRONG = 7.0
MARGIN = 3.0
# How many of the best list entries are looked up in the text, and how many shown.
CORROBORATED = 3
SHOWN = 10
# Days around the news within which another publication is about the same events.
WINDOW_DAYS = 45

NO_FIGURANT = "в публикации нет неназванного фигуранта, или публикация не найдена"
NO_CANDIDATE = "ни одна запись перечня и ни один человек базы не подходят по признакам"
SEVERAL_FIGURANTS = "в публикации несколько неназванных фигурантов: кандидаты у каждого свои"
TIE = "несколько кандидатов подходят одинаково сильно"
PEOPLE_UNKNOWN = (
    "кого публикация называет по имени, узнать не удалось: среди кандидатов может быть "
    "человек, уже названный в ней, — наиболее вероятный кандидат не объявлен"
)
NOT_CONFIRMED = (
    "кандидат — вероятностная оценка по признакам, не установленная личность: подтверждает человек"
)

_TEXT, _STRUCTURED = 1, 0
_WORD = re.compile(r"[А-Яа-яЁё]+")


@dataclass
class _Clue:
    key: str
    ref: str
    article_id: int | None
    told: dict[str, str]
    published_at: datetime | None

    @property
    def age(self) -> int | None:
        value = self.told.get("age")
        return int(value) if value and value.isdigit() else None

    @property
    def place(self) -> str:
        return self.told.get("place", "")

    @property
    def articles(self) -> list[str]:
        return [item.strip() for item in self.told.get("articles", "").split(",") if item.strip()]


@dataclass
class _Candidate:
    key: str
    name: str
    kind: str
    signals: list[CandidateSignal] = field(default_factory=list)

    def add(self, signal: str, ref: str | None) -> None:
        for held in self.signals:
            if held.signal == signal:
                if ref and ref not in held.evidence:
                    held.evidence.append(ref)
                return
        self.signals.append(
            CandidateSignal(signal=signal, weight=WEIGHTS[signal], evidence=[ref] if ref else [])
        )

    @property
    def score(self) -> float:
        return round(sum(signal.weight for signal in self.signals), 2)


def _fold(text: str) -> str:
    return text.lower().replace("ё", "е")


def _stem(word: str) -> str:
    folded = _fold(word)
    return folded[:-2] if len(folded) > 5 else folded[:-1] if len(folded) > 3 else folded


def place_stems(place: str) -> list[str]:
    generic = {"область", "край", "республика", "город", "округ", "район", "автономный"}
    return [
        _stem(word)
        for word in _WORD.findall(place)
        if _fold(word) not in generic and len(word) >= 3
    ]


def _mentions_place(text: str, place: str) -> bool:
    stems = place_stems(place)
    folded = _fold(text)
    return bool(stems) and all(re.search(rf"(?<![а-я]){re.escape(stem)}", folded) for stem in stems)


def surname_stem(surname: str) -> str:
    """What finds a surname declined, and not a given name inside it: «Петров» is found
    as «петров…» (Петрова, Петровым), never as «петр…» (Пётр); «Толстой» as «толст…»."""
    folded = _fold(surname)
    if folded.endswith(("ов", "ев", "ин", "ын")):
        return folded
    if folded.endswith(("ий", "ый", "ой", "ая", "яя")):
        return folded[:-2]
    return folded[:-1] if len(folded) > 4 else folded


def surname_of(full_name: str) -> str:
    """«ИВАНОВ ИВАН ИВАНОВИЧ» → «Иванов»: the list writes the surname first."""
    first = full_name.split()[0] if full_name.split() else ""
    return first.capitalize()


def _clues(evidence: Sequence[EvidenceItem]) -> list[_Clue]:
    found = []
    for item in evidence:
        if item.fact != "clues" or not item.value or not item.entity_key:
            continue
        told = dict(part.split("=", 1) for part in item.value.split("; ") if "=" in part)
        found.append(
            _Clue(
                key=item.entity_key.removeprefix("figurant:"),
                ref=item.ref,
                article_id=item.article_id,
                told=told,
                published_at=item.published_at,
            )
        )
    return found


def _list_candidates(evidence: Sequence[EvidenceItem], clue: _Clue) -> dict[str, _Candidate]:
    candidates: dict[str, _Candidate] = {}
    for item in evidence:
        if item.fact != "candidates" or not item.value:
            continue
        figurant, *signals = item.value.split(";")
        if figurant != f"figurant:{clue.key}" or not item.entity_key:
            continue
        key = item.entity_key.removeprefix("rf:")
        name = item.text.split(",", 1)[0]
        candidate = candidates.setdefault(key, _Candidate(key=key, name=name, kind="rf_entry"))
        for signal in signals:
            if signal.startswith("age="):
                age = int(signal.removeprefix("age="))
                candidate.add(
                    "age_exact" if clue.age is not None and age == clue.age else "age_near",
                    item.ref,
                )
            elif signal == "removed":
                candidate.add("removed_from_list", item.ref)
            elif signal in ("gender", "initial", "birthplace", "confirmed"):
                candidate.add(signal, item.ref)
    return candidates


def _named_in(evidence: Sequence[EvidenceItem], article_id: int | None) -> dict[str, str]:
    """The people (key → name) the pipeline grouped from this publication's mentions."""
    if article_id is None:
        return {}
    return {
        item.entity_key: item.title or item.entity_key
        for item in evidence
        if item.fact == "named_in_publication"
        and item.entity_key is not None
        and item.article_id == article_id
    }


def _is_one_of(name: str, people: Iterable[str]) -> bool:
    """Whether a list entry's name is one of these people's: every word of a person's
    name (two at least) stands in the entry's."""
    words = set(_fold(name).split())
    for person in people:
        theirs = set(_fold(person).split())
        if len(theirs) >= 2 and theirs <= words:
            return True
    return False


def _source_pages(evidence: Sequence[EvidenceItem], article_id: int | None) -> set[str]:
    """Every address the very publication of the clue is known by here: the one the run
    stored and the one a source outside gave again as a link. The publication cannot
    corroborate itself, however many retrievers found it."""
    return {
        canonical(item.url)
        for item in evidence
        if item.url and (item.article_id == article_id or item.fact == "clues")
    }


def _corroborates(item: EvidenceItem, clue: _Clue, source_pages: set[str]) -> bool:
    """A text corroborates this publication's own clue only if it is a different
    publication: not its own article by number, and not its own address — which is how a
    source outside returns the page the run already had."""
    if "corroboration" not in item.leads or item.article_id == clue.article_id:
        return False
    return not (item.url and canonical(item.url) in source_pages)


def _about_the_case(text: str, clue: _Clue) -> bool:
    """A name and a place are what any list of companies prints; a text about *this*
    case also speaks of one of its articles. Without one it is a listing, not a
    publication about the case, and it corroborates nobody."""
    if not clue.articles:
        return True
    folded = _fold(text)
    return any(_mentions_place_articles(folded, article) for article in clue.articles)


def _mentions_place_articles(folded: str, article: str) -> bool:
    """Every number of the article stands in the text as that number: «158.1» is not «158»
    and «1», a post about 158.2 is not about the case of 158.1, and «ч. 2 ст. 158.1» is
    read by its numbers whatever the spacing a post writes it with."""
    numbers = re.findall(r"\d+(?:\.\d+)*", article)
    return bool(numbers) and all(
        re.search(rf"(?<!\d)(?<!\d\.){re.escape(number)}(?!\.?\d)", folded) for number in numbers
    )


def _people_known(view: ScenarioView, article_id: int | None) -> bool:
    """Whether the people the publication names were asked for and the question was
    answered. An empty answer is an answer; a search that was cut by the ceiling, failed
    or timed out is not, and then nobody can be told from the people already named."""
    return article_id is not None and any(
        query.kind == "publication_people" and article_id in query.article_ids
        for query in view.answered
    )


def _figurants(view: ScenarioView) -> set[str]:
    """The unnamed figurants of the publication, by everything found: the ceiling on
    what is shown must not hide one of two."""
    return {clue.key for clue in _clues(view.found or view.evidence)}


def _preliminary(candidates: Mapping[str, _Candidate]) -> list[_Candidate]:
    return sorted(candidates.values(), key=lambda candidate: (-candidate.score, candidate.name))


class UnnamedFigurantScenario:
    name: ScenarioName = "identify_unnamed"
    facts: Mapping[str, FactRequirement] = FACTS

    def intent_by_rules(self, question: str) -> InvestigationIntent | None:
        article_id = intent_rules.article_by_rules(question)
        if article_id is None:
            return None
        return InvestigationIntent(scenario=self.name, article_id=article_id)

    def subject_of(self, intent: InvestigationIntent) -> SubjectRef | None:
        return None if intent.article_id is None else ArticleSubject(article_id=intent.article_id)

    def subject_name(self, intent: InvestigationIntent) -> str:
        return f"неназванный фигурант публикации {intent.article_id}"

    def initial_queries(
        self, intent: InvestigationIntent, subject: SubjectRef
    ) -> list[PlannedQuery]:
        assert isinstance(subject, ArticleSubject)
        return [
            PlannedQuery(
                query=InvestigationQuery(kind="unnamed", article_id=subject.article_id),
                purpose="candidates",
                priority=_STRUCTURED,
            ),
            # Who the publication does name: they are someone else of the case, wherever
            # else they are named, and must be known before anyone is ranked.
            PlannedQuery(
                query=InvestigationQuery(
                    kind="publication_people",
                    article_ids=(subject.article_id,),
                    fact="candidates",
                ),
                purpose="candidates",
                priority=_STRUCTURED,
            ),
        ]

    def settle(self, view: ScenarioView) -> Subject | None:
        return Subject(name=self.subject_name(view.intent))

    def requirements(self, view: ScenarioView) -> list[FactRequirement]:
        return list(FACTS.values())

    def _follow_ups(self, view: ScenarioView, clue: _Clue) -> list[InvestigationQuery]:
        evidence = view.evidence
        if view.rounds == 0:
            queries: list[InvestigationQuery] = []
            best = _preliminary(_list_candidates(evidence, clue))[:CORROBORATED]
            for candidate in best:
                surname = surname_of(candidate.name)
                if surname and clue.place:
                    queries.append(
                        InvestigationQuery(
                            kind="keywords", name=surname, terms=(clue.place,), fact="corroboration"
                        )
                    )
            if clue.place and clue.published_at is not None:
                around = timedelta(days=WINDOW_DAYS)
                terms = tuple(clue.articles[:2]) or ("уголовное дело",)
                queries.append(
                    InvestigationQuery(
                        kind="keywords",
                        name=clue.place,
                        terms=terms,
                        fact="corroboration",
                        published_from=clue.published_at - around,
                        published_to=clue.published_at + around,
                    )
                )
            return queries
        if view.rounds == 1:
            other = sorted(
                {
                    item.article_id
                    for item in evidence
                    if item.article_id is not None
                    and item.article_id != clue.article_id
                    and "corroboration" in item.leads
                    and item.source_type == "publication"
                }
            )
            if other:
                return [
                    InvestigationQuery(
                        kind="publication_people", article_ids=tuple(other[:20]), fact="candidates"
                    )
                ]
        return []

    def _scored(self, view: ScenarioView, clue: _Clue) -> list[_Candidate]:
        evidence = view.evidence
        # Read from everything found, not from what the ceiling left to show: a person
        # the publication names must be left out even when their line was cut.
        named_here = _named_in(view.found or evidence, clue.article_id)
        source_pages = _source_pages(evidence, clue.article_id)
        candidates = {
            key: candidate
            for key, candidate in _list_candidates(evidence, clue).items()
            if not _is_one_of(candidate.name, named_here.values())
        }
        for candidate in candidates.values():
            surname = surname_stem(surname_of(candidate.name))
            for item in evidence:
                if not _corroborates(item, clue, source_pages):
                    continue
                if (
                    surname
                    and re.search(rf"(?<![а-я]){re.escape(surname)}", _fold(item.text))
                    and _mentions_place(item.text, clue.place)
                    and _about_the_case(item.text, clue)
                ):
                    candidate.add("named_with_place", item.ref)
        place_articles = {
            item.article_id: item
            for item in evidence
            if "corroboration" in item.leads
            and item.article_id is not None
            and _mentions_place(item.text, clue.place)
        }
        for item in evidence:
            if item.fact != "named_in_publication" or item.entity_key is None:
                continue
            if item.article_id == clue.article_id or item.entity_key in named_here:
                # Named in the very publication that leaves the figurant unnamed: someone
                # else of the same case, not them — in whichever publication the name
                # was met again.
                continue
            candidate = candidates.setdefault(
                item.entity_key,
                _Candidate(
                    key=item.entity_key, name=item.title or item.entity_key, kind="entity_group"
                ),
            )
            shared = set((item.value or "").split(",")) & set(clue.articles)
            if shared - {""}:
                candidate.add("shared_article", item.ref)
            if item.article_id in place_articles:
                candidate.add("same_place_publication", place_articles[item.article_id].ref)
            if (
                item.published_at
                and clue.published_at
                and abs(item.published_at - clue.published_at) <= timedelta(days=WINDOW_DAYS)
            ):
                candidate.add("same_days", item.ref)
        # A person named with the place but nothing else is noise of a busy town.
        return [
            candidate
            for candidate in _preliminary(candidates)
            if candidate.kind == "rf_entry" or len(candidate.signals) >= 2
        ]

    def assess(self, view: ScenarioView) -> CoverageAssessment:
        clues = _clues(view.evidence)
        if not clues:
            return CoverageAssessment(enough=True, missing=[NO_FIGURANT])
        conflicts: list[Conflict] = []
        if len(_figurants(view)) > 1:
            # The refs are of the clues shown; one the ceiling cut still counts.
            conflicts.append(
                Conflict(
                    fact="clues",
                    description=SEVERAL_FIGURANTS,
                    evidence=[clue.ref for clue in clues],
                )
            )
        follow_ups = [query for clue in clues for query in self._follow_ups(view, clue)]
        ranked = [candidate for clue in clues for candidate in self._scored(view, clue)]
        missing: list[str] = []
        if not ranked:
            missing.append(FACT_LABELS["candidates"])
        else:
            top = ranked[0]
            second = ranked[1].score if len(ranked) > 1 else 0.0
            if top.score >= STRONG and second >= STRONG and top.score - second < MARGIN:
                conflicts.append(
                    Conflict(
                        fact="candidates",
                        description=f"{TIE}: {top.name}, {ranked[1].name}",
                        evidence=sorted(
                            {
                                ref
                                for signal in [*top.signals, *ranked[1].signals]
                                for ref in signal.evidence
                            }
                        ),
                    )
                )
        known = [FACT_LABELS["clues"], *([FACT_LABELS["candidates"]] if ranked else [])]
        warnings = [NOT_CONFIRMED] if ranked else []
        if ranked and not all(_people_known(view, clue.article_id) for clue in clues):
            warnings.append(PEOPLE_UNKNOWN)
        return CoverageAssessment(
            warnings=warnings,
            enough=not follow_ups,
            known=known,
            missing=missing,
            conflicts=[conflict.description for conflict in conflicts],
            conflict_details=conflicts,
            leads=[item.ref for item in view.evidence if item.leads],
            follow_up_queries=follow_ups,
        )

    def candidates(self, view: ScenarioView) -> list[FigurantCandidate]:
        ranked = [
            candidate for clue in _clues(view.evidence) for candidate in self._scored(view, clue)
        ]
        ranked.sort(key=lambda candidate: (-candidate.score, candidate.name))
        return [
            FigurantCandidate(
                key=candidate.key,
                name=candidate.name,
                kind="rf_entry" if candidate.kind == "rf_entry" else "entity_group",
                score=candidate.score,
                signals=candidate.signals,
            )
            for candidate in ranked[:SHOWN]
        ]

    def leading(self, view: ScenarioView) -> FigurantCandidate | None:
        """The candidate the evidence clearly points to, or None: strong and ahead."""
        ranked = self.candidates(view)
        if not ranked or len(_figurants(view)) != 1:
            return None
        if not all(_people_known(view, clue.article_id) for clue in _clues(view.evidence)):
            # Who the publication names is not known: the best candidate may be one of
            # them, that is someone else of the case.
            return None
        top = ranked[0]
        second = ranked[1].score if len(ranked) > 1 else 0.0
        if top.score >= STRONG and top.score - second >= MARGIN:
            return top
        return None

    def findings(self, view: ScenarioView) -> list[Finding]:
        found: list[Finding] = []
        refs: dict[str, list[str]] = defaultdict(list)
        for clue in _clues(view.evidence):
            refs[", ".join(f"{key}: {value}" for key, value in clue.told.items())].append(clue.ref)
        for told, cited in refs.items():
            found.append(
                Finding(fact="clues", statement=f"{FACT_LABELS['clues']}: {told}", evidence=cited)
            )
        top = self.leading(view)
        if top is not None:
            cited = sorted({ref for signal in top.signals for ref in signal.evidence})
            if cited:
                found.append(
                    Finding(
                        fact="probable_candidate",
                        statement=(
                            f"наиболее вероятный кандидат: {top.name} (оценка {top.score}; "
                            + ", ".join(signal.signal for signal in top.signals)
                            + ")"
                        ),
                        evidence=cited,
                    )
                )
        return found

    def status(self, view: ScenarioView, assessment: CoverageAssessment) -> Status:
        if not _clues(view.evidence):
            return "partial"
        if assessment.conflict_details:
            return "review_required"
        if not self.candidates(view):
            return "partial"
        return "completed" if self.leading(view) is not None else "review_required"
