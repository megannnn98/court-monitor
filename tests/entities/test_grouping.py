"""Person mentions into entities: declined surnames, patronymics, bare surnames."""

from __future__ import annotations

from itertools import count

import pytest

from entities.grouping import Entity, PersonMention, group_mentions

_ids = count(1)


def mention(
    first: str | None, last: str, *, article: int = 1, patronymic: str | None = None
) -> PersonMention:
    return PersonMention(
        mention_id=next(_ids),
        article_id=article,
        first_name=first,
        last_name=last,
        patronymic=patronymic,
    )


def names(entities: list[Entity]) -> dict[str, int]:
    return {entity.name: len(entity.mention_ids) for entity in entities}


def test_a_surname_the_dictionary_left_declined_is_one_entity() -> None:
    entities = group_mentions(
        [
            mention("Александр", "Моор"),
            mention("Александр", "Моора", article=2),
            mention("Александр", "Моору", article=3),
            mention("Александр", "Моором", article=4),
        ]
    )

    assert names(entities) == {"Александр Моор": 4}


def test_declined_forms_join_even_without_the_nominative_in_the_news() -> None:
    entities = group_mentions(
        [mention("Сергей", "Давидиса"), mention("Сергей", "Давидису", article=2)]
    )

    assert len(entities) == 1
    assert entities[0].key == "сергей давидис"
    # Only declined forms in the news: the name shows the nominative they share.
    assert entities[0].name == "Сергей Давидис"


def test_different_given_names_or_surnames_stay_apart() -> None:
    entities = group_mentions(
        [
            mention("Александр", "Моор"),
            mention("Александра", "Моора"),
            mention("Сергей", "Стариков"),
            mention("Сергей", "Старков"),
        ]
    )

    assert names(entities) == {
        "Александр Моор": 1,
        "Александра Моора": 1,
        "Сергей Стариков": 1,
        "Сергей Старков": 1,
    }


def test_a_patronymic_keeps_namesakes_of_other_articles_apart() -> None:
    """The registry's «Николай Викторович Бондаренко» is not the deputy of the news."""
    entities = group_mentions(
        [
            mention("Николай", "Бондаренко", patronymic="Викторович", article=1),
            mention("Николай", "Бондаренко", article=2),
            mention("Николай", "Бондаренко", article=3),
        ]
    )

    assert names(entities) == {"Николай Бондаренко": 2, "Николай Викторович Бондаренко": 1}
    assert {entity.key: entity.patronymic for entity in entities} == {
        "николай бондаренко": None,
        "николай викторович бондаренко": "викторович",
    }


def test_a_name_without_patronymic_joins_the_full_name_of_its_article() -> None:
    entities = group_mentions(
        [
            mention("Иван", "Иванов", patronymic="Петрович", article=1),
            mention("Иван", "Иванов", article=1),
            mention(None, "Иванова", article=1),
            # Declined, the same patronymic is the same person in another article too.
            mention("Иван", "Иванова", patronymic="Петровича", article=2),
        ]
    )

    assert names(entities) == {"Иван Петрович Иванов": 4}
    assert set(entities[0].variants) == {
        "Иван Петрович Иванов",
        "Иван Иванов",
        "Иванова",
        "Иван Петровича Иванова",
    }


def test_two_patronymics_in_one_article_leave_the_short_name_to_nobody_of_them() -> None:
    """Father and son: «Иван Иванов» alone could be either."""
    entities = group_mentions(
        [
            mention("Иван", "Иванов", patronymic="Петрович"),
            mention("Иван", "Иванов", patronymic="Иванович"),
            mention("Иван", "Иванов"),
        ]
    )

    assert names(entities) == {
        "Иван Петрович Иванов": 1,
        "Иван Иванович Иванов": 1,
        "Иван Иванов": 1,
    }


def test_the_regions_of_the_registry_cards_count_publications() -> None:
    def card(article: int, region: str | None) -> PersonMention:
        return PersonMention(next(_ids), article, "Анна", "Смирнова", "Олеговна", region)

    # Twice in one card, once in another, once in the news (no region: it joins them).
    [entity] = group_mentions(
        [card(1, "Москва"), card(1, "Москва"), card(2, "Москва"), card(3, None)]
    )

    assert entity.regions == {"Москва": 2}
    assert len(entity.mention_ids) == 4


def test_a_bare_surname_joins_the_full_name_of_its_article_only() -> None:
    full = mention("Александр", "Моор", article=1)
    same_article = mention(None, "Моору", article=1)
    other_article = mention(None, "Моор", article=2)

    entities = group_mentions([full, same_article, other_article])

    assert entities[0].mention_ids == [full.mention_id, same_article.mention_id]


def test_a_bare_surname_of_two_people_in_one_article_joins_nobody() -> None:
    father = mention("Иван", "Моор")
    son = mention("Пётр", "Моор")
    bare = mention(None, "Моору")

    entities = group_mentions([father, son, bare])

    assert all(bare.mention_id not in entity.mention_ids for entity in entities)


def test_initials_pick_the_person_of_that_given_name() -> None:
    alexander = mention("Александр", "Моор")
    boris = mention("Борис", "Моор")
    initials = mention("А.", "Моор")

    entities = {entity.name: entity for entity in group_mentions([alexander, boris, initials])}

    assert entities["Александр Моор"].mention_ids == [alexander.mention_id, initials.mention_id]
    assert entities["Борис Моор"].mention_ids == [boris.mention_id]


def test_a_two_letter_stem_is_no_surname() -> None:
    entities = group_mentions([mention("Ян", "Лиа"), mention("Ян", "Лию")])

    assert len(entities) == 2


def test_the_most_mentioned_entity_comes_first() -> None:
    entities = group_mentions(
        [mention("Иван", "Иванов"), mention("Олег", "Орлов"), mention("Олег", "Орлова")]
    )

    assert [entity.name for entity in entities] == ["Олег Орлов", "Иван Иванов"]


def test_a_feminine_surname_keeps_its_nominative_ending() -> None:
    entities = group_mentions([mention("Мария", "Иванова"), mention("Мария", "Ивановой")])

    assert names(entities) == {"Мария Иванова": 2}


def test_a_surname_written_one_way_is_not_cut() -> None:
    entities = group_mentions([mention("Иван", "Бабуа"), mention("Иван", "Бабуа", article=2)])

    assert names(entities) == {"Иван Бабуа": 2}


def test_a_given_name_that_repeats_the_surname_is_a_bare_surname() -> None:
    full = mention("Алексей", "Горинов")
    doubled = mention("Горинов", "Горинов", patronymic="Александрович")
    declined = mention("Бонцлера", "Бонцлера", article=2)

    entities = group_mentions([full, doubled, declined])

    assert names(entities) == {"Алексей Горинов": 2}
    assert all(declined.mention_id not in entity.mention_ids for entity in entities)


def test_model_names_rename_merge_and_drop_what_is_no_person() -> None:
    from entities.grouping import GivenName, apply_names

    moor = mention("Александр", "Моор")
    declined = mention("Александра", "Моора", article=2)
    slogan = mention("Слава", "Украине", article=3)
    unanswered = mention("Иван", "Иванов", article=4)
    entities = group_mentions([moor, declined, slogan, unanswered])

    key = {entity.name: entity.key for entity in entities}
    named = apply_names(
        entities,
        {
            key["Александр Моор"]: GivenName("Александр Моор", "male", True),
            key["Александра Моора"]: GivenName("Александр Моор", "male", True),
            key["Слава Украине"]: GivenName("Слава Украине", "unknown", False),
        },
    )

    assert {entity.name: len(entity.mention_ids) for entity in named} == {
        "Александр Моор": 2,
        "Иван Иванов": 1,
    }
    merged = next(entity for entity in named if entity.name == "Александр Моор")
    assert (merged.key, merged.gender, merged.name_source) == ("александр моор", "male", "model")
    assert set(merged.variants) == {"Александр Моор", "Александра Моора"}
    kept = next(entity for entity in named if entity.name == "Иван Иванов")
    assert (kept.gender, kept.name_source) == (None, "rules")


def test_a_model_neither_adds_nor_drops_the_patronymic_that_parts_namesakes() -> None:
    from entities.grouping import GivenName, apply_names

    full = mention("Роман", "Попков", patronymic="Андреевич")
    short = mention("Роман", "Попков", article=2)
    other = mention("Роман", "Попкова", article=3)
    entities = group_mentions([full, short, other])
    by_key = {entity.key: entity for entity in entities}
    names = {
        # Asked about the forms without a patronymic, a model may still add one.
        next(key for key, entity in by_key.items() if entity.patronymic is None): GivenName(
            "Роман Андреевич Попков", "male", True
        ),
        # And may leave it out where the forms had it.
        next(key for key, entity in by_key.items() if entity.patronymic): GivenName(
            "Роман Попков", "male", True
        ),
    }

    named = {
        entity.key: (entity.name, len(entity.mention_ids))
        for entity in apply_names(entities, names)
    }

    assert named == {
        "роман попков": ("Роман Попков", 2),
        "роман андреевич попков": ("Роман Андреевич Попков", 1),
    }


@pytest.mark.parametrize(
    ("answer", "stored"),
    [
        ("Турбин Арсений", "Арсений Турбин"),
        ("Иванов Иван Петрович", "Иван Петрович Иванов"),
        ("Арсений Турбин", "Арсений Турбин"),
        ("Лида Мониава", "Лида Мониава"),  # no word in the dictionary: left as written
        ("Соломатин П.", "Соломатин П."),
    ],
)
def test_a_surname_first_answer_is_turned_given_name_first(answer: str, stored: str) -> None:
    from entities.grouping import given_name_first

    assert given_name_first(answer) == stored


def test_a_surname_first_answer_merges_with_the_same_person() -> None:
    from entities.grouping import GivenName, apply_names

    entities = group_mentions(
        [mention("Арсений", "Турбин"), mention("Арсения", "Турбина", article=2)]
    )
    names = {entity.key: GivenName("Турбин Арсений", "male", True) for entity in entities}

    [entity] = apply_names(entities, names)

    assert (entity.key, entity.name, len(entity.mention_ids)) == (
        "арсений турбин",
        "Арсений Турбин",
        2,
    )


def test_a_rule_name_written_surname_first_joins_the_same_person() -> None:
    from entities.grouping import GivenName, apply_names

    reversed_ = mention("Турбин", "Арсений")  # the text wrote the surname first
    right = mention("Арсений", "Турбин", article=2)
    entities = group_mentions([reversed_, right])
    right_key = next(entity.key for entity in entities if entity.name == "Арсений Турбин")

    named = apply_names(entities, {right_key: GivenName("Арсений Турбин", "male", True)})

    assert [(entity.name, len(entity.mention_ids)) for entity in named] == [("Арсений Турбин", 2)]


def _card(
    first: str, patronymic: str, last: str, article: int, region: str | None
) -> PersonMention:
    return PersonMention(next(_ids), article, first, last, patronymic, region)


def test_cards_of_one_full_name_in_two_regions_are_two_people() -> None:
    """Two «Денис Владимирович Попов» of the registry: Курская and Краснодарский край."""
    entities = group_mentions(
        [
            _card("Денис", "Владимирович", "Попов", 1, "Курская область"),
            _card("Денис", "Владимирович", "Попов", 2, "Краснодарский край"),
            _card("Денис", "Владимирович", "Попов", 3, "Курская область"),
            # The news name him without a region: either card, so neither.
            _card("Денис", "Владимирович", "Попов", 4, None),
        ]
    )

    assert {entity.key: len(entity.mention_ids) for entity in entities} == {
        "денис владимирович попов · курская область": 2,
        "денис владимирович попов · краснодарский край": 1,
        "денис владимирович попов": 1,
    }
    assert {entity.key: entity.region_tag for entity in entities}[
        "денис владимирович попов · курская область"
    ] == "Курская область"


def test_a_full_name_without_a_region_joins_the_cards_of_one_region() -> None:
    [entity] = group_mentions(
        [
            _card("Игорь", "Александрович", "Ранав", 1, "Чукотский автономный округ"),
            _card("Игорь", "Александрович", "Ранав", 2, None),
        ]
    )

    assert entity.key == "игорь александрович ранав · чукотский автономный округ"
    assert len(entity.mention_ids) == 2


def test_a_model_name_keeps_the_region_that_parts_namesakes() -> None:
    from entities.grouping import GivenName, apply_names

    entities = group_mentions(
        [
            _card("Денис", "Владимировича", "Попова", 1, "Курская область"),
            _card("Денис", "Владимировича", "Попова", 2, "Краснодарский край"),
        ]
    )
    names = {entity.key: GivenName("Денис Владимирович Попов", "male", True) for entity in entities}

    named = apply_names(entities, names)

    assert sorted(entity.key for entity in named) == [
        "денис владимирович попов · краснодарский край",
        "денис владимирович попов · курская область",
    ]


def test_a_surname_in_a_soft_sign_declines_on_its_stem() -> None:
    """«Юрий Дудь» of one piece, «Юрия Дудя» and «Дудю» of others: one person."""
    entities = group_mentions(
        [
            mention("Юрий", "Дудя", article=1),
            mention("Юрий", "Дудя", article=2),
            mention(None, "Дудю", article=2),
            mention("Юрий", "Дудь", article=3),
        ]
    )

    assert names(entities) == {"Юрий Дудь": 4}


def test_a_surname_that_is_an_adjective_declines_on_its_stem() -> None:
    entities = group_mentions(
        [
            mention("Олег", "Заболотнего", article=1),
            mention("Олег", "Заболотнему", article=2),
            mention("Олег", "Заболотний", article=3),
            # A hard adjective is another surname.
            mention("Олег", "Заболотного", article=4),
            mention("Олег", "Заболотный", article=5),
        ]
    )

    assert names(entities) == {"Олег Заболотний": 3, "Олег Заболотный": 2}
