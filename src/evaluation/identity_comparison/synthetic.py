"""Both models on the ER v2 corpus, which says who an incoming name really is.

A case is one incoming name against the corpus persons. Each model's answer is put in
common terms (`Verdict`): the persons the name was joined to without a human, and the
persons offered to a human. ER v2 joins by AUTO_LINK and offers by REVIEW; the rules
join by putting two mentions in one entity and offer by a pair of «Спорные случаи».
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from entities.disputes import EntityRef, find_pairs
from entities.grouping import PersonMention
from evaluation.identity_comparison.rules import entity_of_mention, rules_only_entities
from evaluation.person_resolution.corpus import CorpusPerson
from evaluation.person_resolution.er_run import CaseOutcome, ErCase, ErCorpus
from persons.resolution.models import PersonResolutionAction
from processing.extraction.normalizers import RuleBasedMentionNormalizer

CORRECT_JOIN = "correct_join"
FALSE_MERGE = "false_merge"
REVIEW = "review"
FALSE_SPLIT = "false_split"
CORRECT_SEPARATE = "correct_separate"
UNDECIDED = "undecided"
OUTCOMES = (CORRECT_JOIN, FALSE_MERGE, REVIEW, FALSE_SPLIT, CORRECT_SEPARATE, UNDECIDED)

# Tags of the corpus whose cases put a namesake, or a near one, beside the incoming name.
NAMESAKE_TAGS = frozenset(
    {
        "namesakes",
        "same-first-last-different-patronymic",
        "same-full-name-different-person",
        "two-active-namesakes",
        "reviewer-created-namesake",
        "reordered-namesake-ambiguity",
        "same-surname",
        "same-initial",
    }
)

_normalizer = RuleBasedMentionNormalizer()


@dataclass(frozen=True)
class Verdict:
    joined: frozenset[str] = frozenset()
    # Left to a human, and the persons shown to them (none: «nobody plausible»).
    asked_human: bool = False
    offered: frozenset[str] = frozenset()
    # False when the model put the name nowhere: no entity was made of it.
    decided: bool = True


def outcome(case: ErCase, verdict: Verdict) -> str:
    truth = case.true_person
    if verdict.joined - {truth}:
        return FALSE_MERGE
    if truth is not None and truth in verdict.joined:
        return CORRECT_JOIN
    if not verdict.decided:
        return UNDECIDED
    if verdict.asked_human:
        return REVIEW
    return FALSE_SPLIT if truth is not None else CORRECT_SEPARATE


def er_verdict(result: CaseOutcome) -> Verdict:
    if result.action is PersonResolutionAction.AUTO_LINK and result.selected_person:
        return Verdict(joined=frozenset({result.selected_person}))
    if result.action is PersonResolutionAction.REVIEW:
        return Verdict(asked_human=True, offered=frozenset(result.candidates))
    return Verdict()


_PATRONYMIC = re.compile(r"(вич|вна|ична|ьич|оглы|кызы)$")


def repaired_parts(full_name: str) -> tuple[str | None, str | None, str | None] | None:
    """(given name, surname, patronymic) of a three-word name, told by where the
    patronymic stands; None when the name is not one. Only for the «repaired» variant:
    the pipeline stores a three-word name with its first word as both given name and
    surname and its third as patronymic (`RuleBasedMentionNormalizer._normalize_person`).
    """
    words = full_name.split()
    if len(words) != 3:
        return None
    if _PATRONYMIC.search(words[1].lower()):
        return words[0], words[2], words[1]
    if _PATRONYMIC.search(words[2].lower()):
        return words[1], words[0], words[2]
    return None


def _mention(mention_id: int, surface: str, *, repaired: bool = False) -> PersonMention:
    """The mention extraction would store for this surface, in an article of its own."""
    _, data = _normalizer.normalize_person(surface)
    first, last, patronymic = data.first_name, data.last_name, data.patronymic
    if repaired and (parts := repaired_parts(data.full_name)) is not None:
        first, last, patronymic = parts
    return PersonMention(
        mention_id=mention_id,
        article_id=mention_id,
        first_name=first,
        last_name=last,
        patronymic=patronymic,
        surface=surface,
    )


def _person_mentions(
    persons: Sequence[CorpusPerson], *, repaired: bool = False
) -> tuple[list[PersonMention], dict[int, str]]:
    mentions: list[PersonMention] = []
    person_of: dict[int, str] = {}
    for person in persons:
        for surface in (person.canonical_name, *person.aliases):
            mention = _mention(len(mentions) + 1, surface, repaired=repaired)
            mentions.append(mention)
            person_of[mention.mention_id] = person.key
    return mentions, person_of


def rules_verdict(
    case: ErCase, persons: Sequence[CorpusPerson], *, repaired: bool = False
) -> Verdict:
    mentions, person_of = _person_mentions(persons, repaired=repaired)
    incoming = _mention(len(mentions) + 1, case.incoming, repaired=repaired)
    everyone = [*mentions, incoming]
    entities = rules_only_entities(
        everyone, {mention.mention_id: mention.article_id for mention in everyone}
    )
    entity_of = entity_of_mention(entities)
    own = entity_of.get(incoming.mention_id)
    if own is None:
        return Verdict(decided=False)
    persons_of_entity: dict[str, set[str]] = {}
    for mention_id, key in person_of.items():
        if mention_id in entity_of:
            persons_of_entity.setdefault(entity_of[mention_id], set()).add(key)
    refs = [
        EntityRef(id=index, key=entity.key, name=entity.name, mention_count=len(entity.mention_ids))
        for index, entity in enumerate(entities)
    ]
    offered: set[str] = set()
    for pair in find_pairs(refs):
        if own in pair.keys:
            other = pair.left.key if pair.right.key == own else pair.right.key
            offered |= persons_of_entity.get(other, set())
    return Verdict(
        joined=frozenset(persons_of_entity.get(own, set())),
        asked_human=bool(offered),
        offered=frozenset(offered),
    )


@dataclass
class ModelScore:
    """Counted over the cases the data can tell apart; the indistinguishable are apart."""

    outcomes: Counter[str] = field(default_factory=Counter)
    cases: int = 0
    with_true_person: int = 0
    # A review that offered the true person to the human.
    reviews_offering_truth: int = 0
    namesake_cases: int = 0
    namesake_without_false_merge: int = 0
    indistinguishable: Counter[str] = field(default_factory=Counter)

    def summary(self) -> dict[str, object]:
        joins = self.outcomes[CORRECT_JOIN]
        merges = self.outcomes[FALSE_MERGE]
        precision = joins / (joins + merges) if joins + merges else None
        recall = joins / self.with_true_person if self.with_true_person else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else None
        )
        return {
            "cases": self.cases,
            "cases_with_true_person": self.with_true_person,
            "outcomes": {name: self.outcomes[name] for name in OUTCOMES},
            "pairwise_precision": _rounded(precision),
            "pairwise_recall": _rounded(recall),
            "pairwise_f1": _rounded(f1),
            "false_merge_rate": _rounded(merges / self.cases if self.cases else None),
            "false_split_rate": _rounded(
                self.outcomes[FALSE_SPLIT] / self.with_true_person
                if self.with_true_person
                else None
            ),
            "review_rate": _rounded(self.outcomes[REVIEW] / self.cases if self.cases else None),
            "reviews_offering_true_person": self.reviews_offering_truth,
            "coverage": _rounded(
                (self.cases - self.outcomes[UNDECIDED]) / self.cases if self.cases else None
            ),
            "namesake_cases": self.namesake_cases,
            "namesake_accuracy": _rounded(
                self.namesake_without_false_merge / self.namesake_cases
                if self.namesake_cases
                else None
            ),
            "indistinguishable": dict(self.indistinguishable),
        }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def score(cases: Sequence[ErCase], verdicts: Sequence[Verdict]) -> ModelScore:
    result = ModelScore()
    for case, verdict in zip(cases, verdicts, strict=True):
        label = outcome(case, verdict)
        if case.category == "indistinguishable":
            result.indistinguishable[label] += 1
            continue
        result.cases += 1
        result.outcomes[label] += 1
        if case.true_person is not None:
            result.with_true_person += 1
            if label == REVIEW and case.true_person in verdict.offered:
                result.reviews_offering_truth += 1
        if NAMESAKE_TAGS & set(case.tags):
            result.namesake_cases += 1
            result.namesake_without_false_merge += label != FALSE_MERGE
    return result


def by_tag(
    cases: Sequence[ErCase], verdicts: Mapping[str, Sequence[Verdict]]
) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, Counter[str]]] = {}
    for index, case in enumerate(cases):
        for tag in case.tags or ["untagged"]:
            row = rows.setdefault(tag, {model: Counter() for model in verdicts})
            for model, answers in verdicts.items():
                row[model][outcome(case, answers[index])] += 1
    return {
        tag: {
            "cases": sum(next(iter(row.values())).values()),
            **{model: dict(counter) for model, counter in row.items()},
        }
        for tag, row in sorted(rows.items())
    }


def case_table(
    cases: Sequence[ErCase], verdicts: Mapping[str, Sequence[Verdict]]
) -> list[dict[str, object]]:
    """Every case with each model's outcome; `agree` when all of them gave the same."""
    table: list[dict[str, object]] = []
    for index, case in enumerate(cases):
        labels = {model: outcome(case, answers[index]) for model, answers in verdicts.items()}
        table.append(
            {
                "case": case.id,
                "incoming": case.incoming,
                "category": case.category,
                "tags": case.tags,
                "true_person": case.true_person,
                **labels,
                "agree": len(set(labels.values())) == 1,
            }
        )
    return table


def corpus_self_grouping(corpus: ErCorpus, *, repaired: bool = False) -> dict[str, object]:
    """What the rules make of the corpus persons alone, before any incoming name: persons
    put in one entity, and persons whose own names fall into several."""
    mentions, person_of = _person_mentions(corpus.persons, repaired=repaired)
    entity_of = entity_of_mention(
        rules_only_entities(
            mentions, {mention.mention_id: mention.article_id for mention in mentions}
        )
    )
    persons_of_entity: dict[str, set[str]] = {}
    entities_of_person: dict[str, set[str]] = {}
    unplaced: set[str] = set()
    for mention_id, person in person_of.items():
        key = entity_of.get(mention_id)
        if key is None:
            unplaced.add(person)
            continue
        persons_of_entity.setdefault(key, set()).add(person)
        entities_of_person.setdefault(person, set()).add(key)
    return {
        "persons": len(corpus.persons),
        "mentions": len(mentions),
        "merged_groups": sorted(
            sorted(group) for group in persons_of_entity.values() if len(group) > 1
        ),
        "split_persons": sorted(
            person for person, keys in entities_of_person.items() if len(keys) > 1
        ),
        "persons_with_an_unplaced_name": sorted(unplaced),
    }
