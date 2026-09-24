"""Person mentions into entities: declined surnames, patronymics, bare surnames."""

from __future__ import annotations

from itertools import count

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


def test_a_patronymic_does_not_split_a_person_and_shows_as_a_variant() -> None:
    entities = group_mentions(
        [mention("Иван", "Иванов", patronymic="Петрович"), mention("Иван", "Иванов", article=2)]
    )

    assert names(entities) == {"Иван Иванов": 2}
    assert set(entities[0].variants) == {"Иван Петрович Иванов", "Иван Иванов"}


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


def test_a_patronymic_from_the_model_does_not_keep_people_apart() -> None:
    from entities.grouping import GivenName, apply_names

    full = mention("Роман", "Попков", patronymic="Андреевич")
    short = mention("Роман", "Попков", article=2)
    other = mention("Роман", "Попкова", article=3)
    entities = group_mentions([full, short, other])
    names = {entity.key: GivenName("Роман Андреевич Попков", "male", True) for entity in entities}

    [entity] = apply_names(entities, names)

    assert (entity.key, entity.name, len(entity.mention_ids)) == (
        "роман попков",
        "Роман Андреевич Попков",
        3,
    )
