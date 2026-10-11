"""The identity comparison's own arithmetic, without a database."""

from __future__ import annotations

import pytest

from evaluation.identity_comparison import real, synthetic
from evaluation.identity_comparison.synthetic import Verdict
from evaluation.person_resolution.corpus import CorpusPerson
from persons.resolution.evaluation import ErCase
from persons.resolution.models import PersonResolutionAction

PERSONS = [
    CorpusPerson(key="lebedev", canonical_name="Константин Лебедев"),
    CorpusPerson(key="gusev", canonical_name="Алексей Гусев"),
]


def _case(incoming: str, true_person: str | None) -> ErCase:
    return ErCase(
        id=incoming,
        incoming=incoming,
        category="positive" if true_person else "negative",
        expected_action=PersonResolutionAction.AUTO_LINK
        if true_person
        else PersonResolutionAction.CREATE_NEW,
        true_person=true_person,
    )


@pytest.mark.parametrize(
    ("true_person", "verdict", "expected"),
    [
        ("a", Verdict(joined=frozenset({"a"})), synthetic.CORRECT_JOIN),
        ("a", Verdict(joined=frozenset({"b"})), synthetic.FALSE_MERGE),
        # Joined to the right person and to a wrong one: a false merge all the same.
        ("a", Verdict(joined=frozenset({"a", "b"})), synthetic.FALSE_MERGE),
        (None, Verdict(joined=frozenset({"b"})), synthetic.FALSE_MERGE),
        ("a", Verdict(asked_human=True, offered=frozenset({"a"})), synthetic.REVIEW),
        ("a", Verdict(asked_human=True), synthetic.REVIEW),
        ("a", Verdict(), synthetic.FALSE_SPLIT),
        (None, Verdict(), synthetic.CORRECT_SEPARATE),
        ("a", Verdict(decided=False), synthetic.UNDECIDED),
    ],
)
def test_a_verdict_becomes_one_outcome(
    true_person: str | None, verdict: Verdict, expected: str
) -> None:
    assert synthetic.outcome(_case("Имя Фамилия", true_person), verdict) == expected


def test_the_rules_join_a_declined_name_and_keep_a_stranger_apart() -> None:
    joined = synthetic.rules_verdict(_case("Константина Лебедева", "lebedev"), PERSONS)
    stranger = synthetic.rules_verdict(_case("Тарас Коваленко", None), PERSONS)

    assert joined.joined == frozenset({"lebedev"})
    assert stranger == Verdict()


def test_a_name_the_rules_make_no_entity_of_is_undecided() -> None:
    assert synthetic.rules_verdict(_case("А. Гусев", "gusev"), PERSONS) == Verdict(decided=False)


def test_a_three_word_name_is_placed_since_the_normalizer_stores_its_parts() -> None:
    # Normalizer 1.7.0 stores the parts in place, so the «repaired» variant of the
    # benchmark, which did that for it, now changes nothing.
    persons = [CorpusPerson(key="belova", canonical_name="Екатерина Андреевна Белова")]
    case = _case("Белова Екатерина Андреевна", "belova")

    assert synthetic.rules_verdict(case, persons).joined == frozenset({"belova"})
    assert synthetic.rules_verdict(case, persons, repaired=True).joined == frozenset({"belova"})


@pytest.mark.parametrize(
    ("name", "parts"),
    [
        ("Иван Петрович Сидоров", ("Иван", "Сидоров", "Петрович")),
        ("Сидорова Анна Ильинична", ("Анна", "Сидорова", "Ильинична")),
        ("Мамед Гусейн Алиев", None),
        ("Иван Сидоров", None),
    ],
)
def test_repaired_parts(name: str, parts: tuple[str, str, str] | None) -> None:
    assert synthetic.repaired_parts(name) == parts


def test_scores_count_joins_merges_and_reviews() -> None:
    cases = [_case("a", "p"), _case("b", "p"), _case("c", None), _case("d", "p")]
    verdicts = [
        Verdict(joined=frozenset({"p"})),
        Verdict(asked_human=True, offered=frozenset({"p"})),
        Verdict(joined=frozenset({"p"})),
        Verdict(),
    ]

    summary = synthetic.score(cases, verdicts).summary()

    assert summary["pairwise_precision"] == 0.5
    assert summary["pairwise_recall"] == round(1 / 3, 4)
    assert summary["false_merge_rate"] == 0.25
    assert summary["false_split_rate"] == round(1 / 3, 4)
    assert summary["reviews_offering_true_person"] == 1
    assert summary["coverage"] == 1.0


@pytest.mark.parametrize(
    ("parts", "shape"),
    [
        (("Иван", "Сидоров", "Петрович"), "given_patronymic_surname"),
        (("Иван", "Сидоров", None), "given_surname"),
        (("И.", "Сидоров", None), "surname_and_initial"),
        ((None, "Сидоров", None), "surname_only"),
        (("Иван", None, None), "given_only"),
        ((None, None, None), "unparsed"),
    ],
)
def test_name_shape(parts: tuple[str | None, str | None, str | None], shape: str) -> None:
    assert real.name_shape(*parts) == shape


@pytest.mark.parametrize(
    ("names", "kind"),
    [
        (["Иван Сидоров", "иван сидоров"], "same_name"),
        (["Иван Сидоров", "Сидоров Иван Петрович"], "a_name_with_a_third_word"),
        (["Иван Сидорова", "Иван Сидоров"], "one_given_name_surname_forms_differ"),
        (["Анна Сидорова", "Сидорова Город"], "other"),
    ],
)
def test_name_difference(names: list[str], kind: str) -> None:
    assert real.name_difference(names) == kind
