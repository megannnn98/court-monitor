"""Scenario A: what is known of one named person.

«Что известно про Марию Бонцлер?», «За что преследуют X?», «Есть ли X в перечне?».
The person is an entity group (ADR 0024); the facts are what the pipeline decided of
it; a quote found for a missing fact is a lead, never a fact.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from investigation import intent as intent_rules
from investigation.models import (
    Conflict,
    CoverageAssessment,
    EvidenceItem,
    FactRequirement,
    FigurantCandidate,
    Finding,
    InvestigationIntent,
    InvestigationQuery,
    PersonSubject,
    ScenarioName,
    Status,
    Subject,
    SubjectCandidate,
    SubjectRef,
)
from investigation.scenarios.base import PlannedQuery, ScenarioView

# The facts a dossier of a person is expected to hold, in the order they are reported.
REQUIRED_FACTS = ("role", "charges", "politics", "rf_list")
# Asked only of a political case: the pipeline reads the latest news of those alone.
POLITICAL_ONLY_FACTS = ("latest_news",)
POLITICAL = "political"

FACT_LABELS = {
    "identity": "кто это",
    "role": "роль в деле",
    "charges": "статьи обвинения",
    "politics": "политическое ли преследование",
    "rf_list": "есть ли в перечне Росфинмониторинга",
    "latest_news": "последнее событие по делу",
}
FACTS: Mapping[str, FactRequirement] = {
    key: FactRequirement(key=key, description=FACT_LABELS[key])
    for key in (*REQUIRED_FACTS, *POLITICAL_ONLY_FACTS)
}
# What the entity group's own record says of the person, beside the verdicts: kept as
# evidence to tell a group that holds two people, never reported as a finding.
PROFILE_FACTS = ("patronymic", "region", "rf_entry")

# What to look for in the person's own publications when a fact is missing: the
# beginnings of words, one tuple per follow-up round, the narrower first. A fact
# without a row here is not something a publication's wording can supply.
FOLLOW_UP_TERMS: dict[str, tuple[tuple[str, ...], ...]] = {
    "charges": (("статье", "статьи", "ст. "), ("ук рф", "уголовного кодекса", "обвиня")),
    "role": (("обвиняем", "подозреваем", "фигурант"), ("задержа", "арестова", "осужд")),
    "latest_news": (("приговор", "осужд"), ("арест", "задержа", "суд")),
}
# The same, as whole words for a ranked text search (lexical index, a channel's search):
# the name and any one of these.
FOLLOW_UP_KEYWORDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "charges": (("статья", "статье", "статьи"), ("обвинение", "обвиняется")),
    "role": (("обвиняемый", "подозреваемый", "фигурант"), ("задержан", "арестован", "осужден")),
    "latest_news": (("приговор", "осужден"), ("арест", "суд")),
}

# A step that could not decide stores this word: it is the absence of a fact.
UNDECIDED = "unclear"

NOT_FOUND = "человек с таким именем не найден среди людей базы"
NAME_ONLY_MATCH = (
    "совпадение с перечнем Росфинмониторинга только по имени и фамилии, без отчества: "
    "возможен тёзка"
)
MIXED_IDENTITY = "possible_mixed_identity_group"
# The level of a list match that rests on given name and surname alone.
RF_NAME_LEVEL = "name"
RF_FULL_LEVEL = "full"

# Priorities: the person's own record first, ranked text next, outside sources last.
_FIRST, _TEXT, _OUTSIDE = 0, 1, 2


def decided(item: EvidenceItem) -> bool:
    """A fact the pipeline settled, not one it left as «unclear»."""
    return item.value is not None and item.value.split(" ", 1)[0] != UNDECIDED


def _values(evidence: Sequence[EvidenceItem]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = defaultdict(list)
    for item in evidence:
        if item.fact is None or item.value is None or not decided(item):
            continue
        if item.value not in values[item.fact]:
            values[item.fact].append(item.value)
    return dict(values)


def _refs(evidence: Sequence[EvidenceItem], fact: str) -> dict[str, list[str]]:
    refs: dict[str, list[str]] = defaultdict(list)
    for item in evidence:
        if item.fact == fact and item.value is not None and item.ref:
            refs[item.value].append(item.ref)
    return dict(refs)


def resolve_subject(name: str, evidence: Sequence[EvidenceItem]) -> Subject:
    """The person asked about. One entity group answering the name is that person;
    several are candidates a human tells apart; none leaves the key unset."""
    candidates: dict[str, SubjectCandidate] = {}
    for item in evidence:
        if item.fact == "identity" and item.entity_key is not None:
            candidates.setdefault(
                item.entity_key,
                SubjectCandidate(
                    entity_key=item.entity_key,
                    name=item.value or item.entity_key,
                    description=item.text,
                ),
            )
    found = list(candidates.values())
    return Subject(
        name=name,
        entity_key=found[0].entity_key if len(found) == 1 else None,
        candidates=found,
    )


def mixed_identity(evidence: Sequence[EvidenceItem]) -> list[Conflict]:
    """Signs that one entity group holds two people: a group is made by the rules from
    names alone (ADR 0024), so two namesakes may be one. Rules, not a resolver: the
    group is never split here, a person decides."""
    conflicts: list[Conflict] = []
    patronymics = _refs(evidence, "patronymic")
    if len(patronymics) > 1:
        conflicts.append(
            Conflict(
                fact="patronymic",
                description=(
                    f"{MIXED_IDENTITY}: в публикациях у человека разные отчества — "
                    + ", ".join(sorted(patronymics))
                ),
                evidence=[ref for refs in patronymics.values() for ref in refs],
            )
        )
    births = defaultdict(list)
    for item in evidence:
        if item.fact == "rf_entry" and item.value and item.ref:
            births[item.value.rsplit(" ", 1)[-1]].append(item.ref)
    if len(births) > 1:
        conflicts.append(
            Conflict(
                fact="birth_date",
                description=(
                    f"{MIXED_IDENTITY}: имя совпало с записями перечня с разными датами "
                    "рождения — " + ", ".join(sorted(births))
                ),
                evidence=[ref for refs in births.values() for ref in refs],
            )
        )
    regions = _refs(evidence, "region")
    if len(regions) > 1:
        conflicts.append(
            Conflict(
                fact="region",
                description=(
                    f"{MIXED_IDENTITY}: публикации о человеке называют разные регионы — "
                    + ", ".join(sorted(regions))
                ),
                evidence=[ref for refs in regions.values() for ref in refs],
                # A person moves, a case is tried elsewhere: a sign, not a contradiction.
                critical=False,
            )
        )
    return conflicts


class PersonDossierScenario:
    name: ScenarioName = "person_dossier"
    facts: Mapping[str, FactRequirement] = FACTS

    def intent_by_rules(self, question: str) -> InvestigationIntent | None:
        name = intent_rules.subject_by_rules(question)
        return InvestigationIntent(
            scenario=self.name,
            subject_name=name,
            requested_facts=intent_rules.requested_facts(question, self.facts),
        )

    def subject_of(self, intent: InvestigationIntent) -> SubjectRef | None:
        return None if not intent.subject_name else PersonSubject(name=intent.subject_name)

    def subject_name(self, intent: InvestigationIntent) -> str:
        return intent.subject_name or ""

    def initial_queries(
        self, intent: InvestigationIntent, subject: SubjectRef
    ) -> list[PlannedQuery]:
        assert isinstance(subject, PersonSubject)
        return [
            PlannedQuery(
                query=InvestigationQuery(kind="person", name=subject.name),
                purpose="identity",
                priority=_FIRST,
            ),
            PlannedQuery(
                query=InvestigationQuery(kind="keywords", name=subject.name),
                purpose="identity",
                priority=_TEXT,
            ),
        ]

    def settle(self, view: ScenarioView) -> Subject | None:
        name = self.subject_name(view.intent)
        # On everything found: a ceiling on what is shown must not turn several people
        # answering the name into one.
        return resolve_subject(name, view.found or view.evidence)

    def requirements(self, view: ScenarioView) -> list[FactRequirement]:
        values = _values(view.evidence)
        asked = set(view.intent.requested_facts)
        required = list(REQUIRED_FACTS)
        # The latest news is read of political cases alone, unless the question asks.
        if POLITICAL in values.get("politics", []) or asked & set(POLITICAL_ONLY_FACTS):
            required.extend(POLITICAL_ONLY_FACTS)
        return [
            FACTS[key].model_copy(update={"required": not asked or key in asked})
            for key in required
        ]

    def assess(self, view: ScenarioView) -> CoverageAssessment:
        subject = view.subject
        assert subject is not None
        if subject.entity_key is None:
            if len(subject.candidates) > 1:
                listed = "; ".join(candidate.description for candidate in subject.candidates)
                conflict = Conflict(
                    fact="identity",
                    description=f"имя подходит нескольким людям базы: {listed}",
                )
                return CoverageAssessment(
                    enough=True,
                    missing=[FACT_LABELS["identity"]],
                    conflicts=[conflict.description],
                    conflict_details=[conflict],
                )
            return CoverageAssessment(enough=True, missing=[NOT_FOUND])

        evidence = view.evidence
        values = _values(evidence)
        requirements = self.requirements(view)
        wanted = [requirement.key for requirement in requirements if requirement.required]
        known = ["identity", *(key for key in wanted if key in values)]
        missing = [key for key in wanted if key not in values]

        conflicts = [
            Conflict(
                fact=fact,
                description=f"{FACT_LABELS[fact]}: в базе несколько значений — {', '.join(found)}",
                evidence=[ref for refs in _refs(evidence, fact).values() for ref in refs],
            )
            for fact, found in values.items()
            if fact in FACT_LABELS and fact not in ("identity", "charges") and len(found) > 1
        ]
        levels = {
            level.strip() for value in values.get("rf_list", []) for level in value.split(",")
        }
        if RF_NAME_LEVEL in levels and RF_FULL_LEVEL not in levels:
            conflicts.append(
                Conflict(
                    fact="rf_list",
                    description=NAME_ONLY_MATCH,
                    evidence=[ref for refs in _refs(evidence, "rf_list").values() for ref in refs],
                )
            )
        conflicts.extend(mixed_identity(evidence))

        looked_up = {lead for item in evidence for lead in item.leads}
        follow_ups: list[InvestigationQuery] = []
        for fact in missing:
            if fact in looked_up or fact not in FOLLOW_UP_TERMS:
                continue
            rows, words = FOLLOW_UP_TERMS[fact], FOLLOW_UP_KEYWORDS[fact]
            if view.rounds < len(rows):
                follow_ups.append(
                    InvestigationQuery(
                        kind="publication_terms",
                        name=subject.name,
                        entity_key=subject.entity_key,
                        terms=rows[view.rounds],
                        fact=fact,
                    )
                )
            if view.rounds < len(words):
                follow_ups.append(
                    InvestigationQuery(
                        kind="keywords",
                        name=subject.name,
                        entity_key=subject.entity_key,
                        terms=words[view.rounds],
                        fact=fact,
                    )
                )
        return CoverageAssessment(
            enough=not follow_ups,
            known=[FACT_LABELS[fact] for fact in known],
            missing=[FACT_LABELS[fact] for fact in missing],
            open_gaps=[FACT_LABELS[fact] for fact in missing if fact not in looked_up],
            conflicts=[conflict.description for conflict in conflicts],
            conflict_details=conflicts,
            leads=[item.ref for item in evidence if item.leads],
            follow_up_queries=follow_ups,
        )

    def findings(self, view: ScenarioView) -> list[Finding]:
        """One finding per fact the pipeline decided, citing every piece that states it."""
        refs: dict[tuple[str, str], list[str]] = {}
        for item in view.evidence:
            if item.fact is None or item.fact not in FACTS or item.value is None:
                continue
            if decided(item):
                refs.setdefault((item.fact, item.value), []).append(item.ref)
        return [
            Finding(fact=fact, statement=f"{FACT_LABELS[fact]}: {value}", evidence=cited)
            for (fact, value), cited in refs.items()
        ]

    def candidates(self, view: ScenarioView) -> list[FigurantCandidate]:
        return []

    def status(self, view: ScenarioView, assessment: CoverageAssessment) -> Status:
        subject = view.subject
        if subject is None:
            return "clarification_required"
        if any(conflict.critical for conflict in assessment.conflict_details):
            return "review_required"
        if subject.entity_key is None or assessment.missing:
            return "partial"
        return "completed"
