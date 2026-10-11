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


def test_a_pair_is_one_entity_apart_offered_or_nobody() -> None:
    entity_of = {1: "a", 2: "a", 3: "b", 4: "c"}
    offered = frozenset({("b", "c")})

    assert production.pair_decision([1], [2], entity_of) == production.ONE_ENTITY
    assert production.pair_decision([1], [3], entity_of) == production.DIFFERENT_ENTITIES
    assert production.pair_decision([3], [4], entity_of, offered) == production.OFFERED
    assert production.pair_decision([4], [3], entity_of, offered) == production.OFFERED
    # One shared entity is enough, whatever else the sides hold.
    assert production.pair_decision([1, 3], [2, 4], entity_of, offered) == production.ONE_ENTITY
    assert production.pair_decision([1], [99], entity_of) == production.NO_ENTITY


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


def test_the_evaluator_reads_a_pairs_own_decision_and_counts_an_offer_as_left_to_a_human() -> None:
    sample = {
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
                    for name, decision in (
                        ("a-b", "one_entity"),
                        ("a-c", "offered_to_operator"),
                        ("b-c", "no_entity"),
                        ("c-d", "different_entities"),
                    )
                ],
            }
        ]
    }
    annotations = {
        "version": 2,
        "annotations": {
            pair.identifier: {"label": "same_person", "skipped": False}
            for pair in labelling.pairs_of(sample)
        },
    }

    report = evaluate_manual.evaluate(sample, annotations)

    assert set(report["models"]) == {"er_v2", "rules", "rules_deepseek"}
    replayed = report["models"]["rules_deepseek"]
    assert (replayed["correct"], replayed["abstains"], replayed["false_splits"]) == (1, 2, 1)
    assert replayed["automatic_decisions"] == 2
