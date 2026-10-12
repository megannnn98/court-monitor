"""The replay of the operator cycle's grouping (`evaluation.identity_comparison.production`)
and the identity defects the labelled sample exposed, each at the level that owns it.
"""

from __future__ import annotations

import pytest

from entities.grouping import GivenName, PersonMention
from evaluation.identity_comparison import evaluate_manual, labelling, production
from evaluation.identity_comparison.rules import entity_of_mention
from processing.extraction.normalizers import RuleBasedMentionNormalizer

NORMALIZER = RuleBasedMentionNormalizer()


def _mention(mention_id: int, article_id: int, surface: str) -> PersonMention:
    """A mention as extraction stores it today."""
    _, data = NORMALIZER.normalize_person(surface)
    return PersonMention(
        mention_id, article_id, data.first_name, data.last_name, data.patronymic, None, surface
    )


def _entities(mentions: list[PersonMention]) -> dict[int, str]:
    return entity_of_mention(production.build(mentions))


def _decision(left: list[int], right: list[int], offered: frozenset[tuple[str, str]]) -> str:
    return production.pair_decision(left, right, {1: "a", 2: "a", 3: "b", 4: "c"}, offered)[0]


def test_a_pair_is_one_entity_in_part_apart_offered_or_nobody() -> None:
    offered = frozenset({("b", "c")})
    nothing: frozenset[tuple[str, str]] = frozenset()

    assert _decision([1], [2], nothing) == production.ONE_ENTITY
    assert _decision([1], [3], nothing) == production.DIFFERENT_ENTITIES
    assert _decision([3], [4], offered) == production.OFFERED
    assert _decision([4], [3], offered) == production.OFFERED
    # A shared entity beside others is a join in part, not one entity.
    assert _decision([1, 3], [2, 4], offered) == production.PARTLY
    assert _decision([1, 3], [2], nothing) == production.PARTLY
    assert _decision([1], [99], nothing) == production.NO_ENTITY


def test_mentions_without_an_entity_are_counted_and_do_not_decide() -> None:
    decision, detail = production.pair_decision([1, 98], [2, 99], {1: "a", 2: "a"})

    assert decision == production.ONE_ENTITY
    assert detail == {
        "left_entities": 1,
        "right_entities": 1,
        "shared_entities": 1,
        "left_mentions_without_entity": 1,
        "right_mentions_without_entity": 1,
    }


def test_an_entity_a_person_took_out_is_gone_with_its_mentions() -> None:
    mentions = [_mention(1, 1, "Иван Сидоров"), _mention(2, 2, "Анна Орлова")]

    found = entity_of_mention(production.build(mentions, removed=["иван сидоров"]))

    assert set(found) == {2}


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("Джон Пол Смит", "Джеймс Пол Смит"),
        ("Джон Давид Смит", "Джеймс Давид Смит"),
        ("Анна Мария Смит", "Анна Елена Смит"),
        ("Герман Иван Петрович", "Герман Олег Петрович"),
    ],
)
def test_two_names_of_several_parts_stay_two_people(one: str, other: str) -> None:
    # Each pair got one set of parts under an earlier `name_parts` and merged.
    mentions = [_mention(1, 1, one), _mention(2, 2, other)]

    found = _entities(mentions)

    assert found[1] != found[2]
    # A model's answer for one of them does not bring them together either.
    apart = production.build(mentions)
    names = {apart[0].key: GivenName(apart[0].name, "male", True)}
    renamed = entity_of_mention(production.build(mentions, names=names))
    assert renamed[1] != renamed[2]


def test_a_name_is_taken_from_a_stored_answer_or_the_rule_and_the_rest_is_left_to_ask() -> None:
    entities = production.build(
        [
            _mention(1, 1, "Ивана Сидорова"),
            _mention(2, 2, "Петру Кузнецову"),
            _mention(3, 3, "Анна Орлова"),
            _mention(4, 4, "Олегу Громову"),
        ]
    )
    by_key = {entity.key: entity for entity in entities}
    by_forms, by_rule_key, ruled, asked = (
        by_key["иван сидоров"],
        by_key["петр кузнецов"],
        by_key["анна орлов"],
        by_key["олег громов"],
    )
    answers = {
        production.forms_key(by_forms): GivenName("Иван Сидоров", "male", True),
        by_rule_key.key: GivenName("Пётр Кузнецов", "male", True),
    }

    names, missing = production.names_for(entities, answers)

    assert names[by_forms.key].nominative == "Иван Сидоров"
    assert names[by_rule_key.key].nominative == "Пётр Кузнецов"
    # One form already in the nominative needs no model.
    assert names[ruled.key].nominative == "Анна Орлова"
    assert missing == [asked]


def test_entities_a_model_names_alike_become_one() -> None:
    mentions = [_mention(1, 1, "Саша Громов"), _mention(2, 2, "Александр Громов")]
    apart = production.build(mentions)
    assert len(apart) == 2
    names = {entity.key: GivenName("Александр Громов", "male", True) for entity in apart}

    together = entity_of_mention(production.build(mentions, names=names))

    assert together[1] == together[2]


@pytest.mark.parametrize(
    ("declined", "nominative"),
    [("Сергея Вихоря", "Сергей Вихорь"), ("Андрея Курылева", "Андрей Курылев")],
)
def test_a_surname_the_dictionary_left_declined_is_still_the_same_person(
    declined: str, nominative: str
) -> None:
    # The normalizer stores «Сергей Вихоря»: the dictionary does not know the surname.
    # Grouping joins the forms by the surname's root, without a model.
    found = _entities([_mention(1, 1, declined), _mention(2, 2, nominative)])

    assert found[1] == found[2]


def test_a_span_with_a_neighbours_surname_no_longer_joins_that_neighbour() -> None:
    # «адвоката Ткачевой Романа Морозова»: the span is the lawyer, with his client's
    # surname in front. Stored with the client's surname as its name, it joined her.
    client = _mention(1, 1, "Екатерина Ткачева")
    span = _mention(2, 1, "Ткачевой Романа Морозова")

    found = _entities([client, span])

    assert found[1] != found.get(2)

    stored_before = PersonMention(2, 1, "Ткачева", "Ткачева", "Морозова", None, span.surface)
    assert _entities([client, stored_before])[1] == _entities([client, stored_before])[2]


@pytest.mark.parametrize(
    ("named", "glued"), [("Макса Грейси", "А. Грейси"), ("Никиты Бородина", "Ш. Бородин")]
)
def test_an_initial_glued_to_another_persons_surname_makes_no_person_of_its_own(
    named: str, glued: str
) -> None:
    # «у знакомого А. Грейси похитил»: the initial is the victim's, the surname the
    # offender's. Extraction owns this span; grouping at least makes nobody new of it:
    # the surname goes to the one full name of its article.
    found = _entities([_mention(1, 1, named), _mention(2, 1, glued)])

    assert len(set(found.values())) == 1
    assert found[2] == found[1]


@pytest.mark.xfail(
    strict=True,
    reason="An entity's key is its name: two people of one name cannot be two entities. "
    "Pairs #68 and #100 of the labelled sample; docs/adr/0024.",
)
@pytest.mark.parametrize("name", ["Андрей Луговой", "Александр Шевчук"])
def test_two_people_of_one_name_are_kept_apart(name: str) -> None:
    # Different articles, different cases, nothing in common but the name.
    found = _entities([_mention(1, 1, name), _mention(2, 2, name)])

    assert found[1] != found[2]


def _sample(decisions: list[tuple[str, str]]) -> dict[str, object]:
    return {
        "cases": [
            {
                "case_id": "case-1",
                "stratum": "rules_join_er_splits/other",
                "decisions": {"er_v2": "different_persons", "rules": "one_entity"},
                "not_ready": None,
                "sides": [{"side": "a"}, {"side": "b"}],
                "pairs": [
                    {
                        "pair_id": f"case-1:{name}",
                        "sides": ["a", "b"],
                        "annotation": {"label": None, "comment": None},
                        "decisions": {"rules_deepseek": decision},
                    }
                    for name, decision in decisions
                ],
            }
        ]
    }


def _labelled(sample: dict[str, object], label: str) -> dict[str, object]:
    return {
        "version": 2,
        "annotations": {
            pair.identifier: {"label": label, "skipped": False}
            for pair in labelling.pairs_of(sample)
        },
    }


def test_the_evaluator_tells_a_review_a_missing_entity_and_a_partial_join_apart() -> None:
    sample = _sample(
        [
            ("a-b", "one_entity"),
            ("a-c", "offered_to_operator"),
            ("b-c", "no_entity"),
            ("c-d", "different_entities"),
            ("d-e", "partly_one_entity"),
        ]
    )

    report = evaluate_manual.evaluate(sample, _labelled(sample, "same_person"))

    assert set(report["models"]) == {"er_v2", "rules", "rules_deepseek"}
    replayed = report["models"]["rules_deepseek"]
    assert replayed["correct"] == 1
    assert replayed["abstains"] == 1
    assert replayed["missing_entity"] == 1
    assert replayed["partial_merges"] == 1
    assert replayed["false_splits"] == 1
    # Neither the review nor the missing entity is a decision.
    assert replayed["automatic_decisions"] == 3
    assert [row["pair_id"] for row in report["errors"]["rules_deepseek_partial_merges"]] == [
        "case-1:d-e"
    ]
    assert [row["pair_id"] for row in report["errors"]["rules_deepseek_missing_entities"]] == [
        "case-1:b-c"
    ]


def test_every_pair_is_decided_left_to_a_human_or_missing() -> None:
    sample = _sample(
        [
            ("a-b", "one_entity"),
            ("a-c", "offered_to_operator"),
            ("b-c", "no_entity"),
            ("c-d", "different_entities"),
        ]
    )
    annotations = _labelled(sample, "same_person")
    # A special label is outside the identity metric and still one of every pair.
    annotations["annotations"]["case-1:b-c"]["label"] = "not_a_person"  # type: ignore[index]

    replayed = evaluate_manual.evaluate(sample, annotations)["models"]["rules_deepseek"]

    assert (
        replayed["all_pairs_automatic"],
        replayed["all_pairs_abstains"],
        replayed["all_pairs_missing"],
    ) == (2, 1, 1)
    assert replayed["evaluable_pairs"] == 3 and replayed["missing_entity"] == 0


def test_a_correct_join_with_a_mention_left_out_is_told_from_a_whole_one() -> None:
    sample = _sample([("a-b", "one_entity"), ("a-c", "one_entity")])
    pairs = sample["cases"][0]["pairs"]  # type: ignore[index]
    whole = {"left_mentions_without_entity": 0, "right_mentions_without_entity": 0}
    pairs[0]["replay"] = {"rules_deepseek": whole}
    pairs[1]["replay"] = {"rules_deepseek": {**whole, "right_mentions_without_entity": 2}}

    report = evaluate_manual.evaluate(sample, _labelled(sample, "same_person"))

    replayed = report["models"]["rules_deepseek"]
    assert (replayed["correct"], replayed["correct_with_every_mention_placed"]) == (2, 1)
    # ER v2 and the stored rules have no counts in the sample.
    assert report["models"]["er_v2"]["correct_with_every_mention_placed"] is None


def test_a_partial_join_of_two_different_people_is_a_false_merge() -> None:
    sample = _sample([("a-b", "partly_one_entity"), ("a-c", "offered_to_operator")])

    replayed = evaluate_manual.evaluate(sample, _labelled(sample, "different_person"))["models"][
        "rules_deepseek"
    ]

    assert (replayed["false_merges"], replayed["partial_merges"]) == (1, 0)
    # Offered to the operator, the two stay apart in what is stored: right for now.
    assert (replayed["abstains"], replayed["stored_correct"]) == (1, 1)
