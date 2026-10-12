"""What is known of the person, what is missing, what contradicts itself, and what to
look for next. Rules only: the same evidence always gives the same assessment.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from investigation.models import CoverageAssessment, EvidenceItem, InvestigationQuery, Subject

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

# What to look for in the person's own publications when a fact is missing: the
# beginnings of words, one tuple per follow-up round, the narrower first. A fact
# without a row here is not something a publication's wording can supply.
FOLLOW_UP_TERMS: dict[str, tuple[tuple[str, ...], ...]] = {
    "charges": (("статье", "статьи", "ст. "), ("ук рф", "уголовного кодекса", "обвиня")),
    "role": (("обвиняем", "подозреваем", "фигурант"), ("задержа", "арестова", "осужд")),
    "latest_news": (("приговор", "осужд"), ("арест", "задержа", "суд")),
}

# A step that could not decide stores this word: it is the absence of a fact.
UNDECIDED = "unclear"

NOT_FOUND = "человек с таким именем не найден среди людей базы"
NAME_ONLY_MATCH = (
    "совпадение с перечнем Росфинмониторинга только по имени и фамилии, без отчества: "
    "возможен тёзка"
)
# The level of a list match that rests on given name and surname alone.
RF_NAME_LEVEL = "name"
RF_FULL_LEVEL = "full"


def _values(evidence: Sequence[EvidenceItem]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = defaultdict(list)
    for item in evidence:
        if item.fact is None or item.value is None or not decided(item):
            continue
        if item.value not in values[item.fact]:
            values[item.fact].append(item.value)
    return dict(values)


def decided(item: EvidenceItem) -> bool:
    """A fact the pipeline settled, not one it left as «unclear»."""
    return item.value is not None and item.value.split(" ", 1)[0] != UNDECIDED


def assess(
    subject: Subject, evidence: Sequence[EvidenceItem], *, followup_rounds: int
) -> CoverageAssessment:
    """`followup_rounds`: how many follow-up rounds already ran; the next one looks for
    the words of its own row of `FOLLOW_UP_TERMS`."""
    if subject.entity_key is None:
        if len(subject.candidates) > 1:
            listed = "; ".join(candidate.description for candidate in subject.candidates)
            return CoverageAssessment(
                enough=True,
                missing=[FACT_LABELS["identity"]],
                conflicts=[f"имя подходит нескольким людям базы: {listed}"],
            )
        return CoverageAssessment(enough=True, missing=[NOT_FOUND])

    values = _values(evidence)
    required = list(REQUIRED_FACTS)
    if POLITICAL in values.get("politics", []):
        required.extend(POLITICAL_ONLY_FACTS)
    known = ["identity", *(fact for fact in required if fact in values)]
    missing = [fact for fact in required if fact not in values]

    conflicts = [
        f"{FACT_LABELS[fact]}: в базе несколько значений — {', '.join(found)}"
        for fact, found in values.items()
        if fact not in ("identity", "charges") and len(found) > 1
    ]
    levels = {level.strip() for value in values.get("rf_list", []) for level in value.split(",")}
    if RF_NAME_LEVEL in levels and RF_FULL_LEVEL not in levels:
        conflicts.append(NAME_ONLY_MATCH)

    looked_up = {lead for item in evidence for lead in item.leads}
    follow_ups = [
        InvestigationQuery(
            kind="publication_terms",
            name=subject.name,
            entity_key=subject.entity_key,
            terms=FOLLOW_UP_TERMS[fact][followup_rounds],
            fact=fact,
        )
        for fact in missing
        if fact in FOLLOW_UP_TERMS
        and fact not in looked_up
        and followup_rounds < len(FOLLOW_UP_TERMS[fact])
    ]
    return CoverageAssessment(
        enough=not follow_ups,
        known=[FACT_LABELS[fact] for fact in known],
        missing=[FACT_LABELS[fact] for fact in missing],
        conflicts=conflicts,
        follow_up_queries=follow_ups,
    )
