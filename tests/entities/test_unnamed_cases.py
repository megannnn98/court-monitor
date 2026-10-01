"""The unnamed figurants as rows of the result: one person a row, named by what is told."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from entities.unnamed_cases import (
    Sentence,
    case_name,
    group_cases,
    place_genitive,
)


def _sentence(
    key: str,
    *,
    article_id: int = 1,
    age: int | None = None,
    gender: str = "male",
    place: str = "",
    initial: str = "",
    articles: tuple[str, ...] = (),
    day: int = 1,
) -> Sentence:
    return Sentence(
        key=key,
        article_id=article_id,
        age=age,
        gender=gender,
        place=place,
        initial=initial,
        articles=articles,
        event_type="detention",
        quote=f"цитата {key}",
        explanation=f"пояснение {key}",
        published_at=datetime(2026, 9, day, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("place", "expected"),
    [
        ("Канаш", "Канаша"),
        ("Токмак", "Токмака"),
        ("Благовещенск", "Благовещенска"),
        ("Черноморский район", "Черноморского района"),
        ("Вологодская область", "Вологодской области"),
        ("Республика Крым", "Республики Крым"),
        # Not declined with confidence: said as it is, in brackets, by the caller.
        ("Ростов-на-Дону", "Ростова-на-Дону"),
        ("Комсомольск-на-Амуре", "Комсомольска-на-Амуре"),
        ("Санкт-Петербург", "Санкт-Петербурга"),
        ("ХМАО", None),
        ("", None),
    ],
)
def test_a_place_is_put_in_the_genitive_or_left_alone(place: str, expected: str | None) -> None:
    assert place_genitive(place) == expected


def test_the_name_says_what_the_text_tells_and_nothing_more() -> None:
    """Ирина's own wording: «столько-то летний житель/ница такого-то места»."""
    assert case_name(15, "male", "Канаш", "") == "15-летний житель Канаша"
    assert case_name(70, "female", "Токмак", "К") == "70-летняя жительница Токмака К."
    assert case_name(None, "male", "Благовещенск", "") == "житель Благовещенска"
    assert case_name(30, "unknown", "ХМАО", "") == "30-летний житель(ница) (ХМАО)"
    assert case_name(17, "male", "", "") == "17-летний житель (место не названо)"


def test_sentences_about_one_person_are_one_case() -> None:
    """Three sentences about the teenager of Канаш, in two publications: one row."""
    cases = group_cases(
        [
            _sentence("a", article_id=1, age=15, place="Канаш", articles=("205.5",), day=1),
            _sentence("b", article_id=2, age=15, place="Канаш", day=2),
            # The same publication goes on about him without the age or the place.
            _sentence("c", article_id=2, day=2),
        ]
    )
    assert [[sentence.key for sentence in case.sentences] for case in cases] == [["a", "b", "c"]]
    assert cases[0].name == "15-летний житель Канаша"
    assert cases[0].articles == ["205.5"] and cases[0].political
    assert cases[0].latest.key == "c" and cases[0].first_published_at == datetime(
        2026, 9, 1, tzinfo=UTC
    )


def test_people_who_differ_in_what_is_told_stay_apart() -> None:
    cases = group_cases(
        [
            _sentence("a", age=15, place="Канаш"),
            _sentence("b", article_id=2, age=16, place="Канаш"),
            _sentence("c", article_id=3, age=15, place="Канаш", gender="female"),
            _sentence("d", article_id=4, age=15, place="Вологда"),
            _sentence("e", article_id=5, age=40, place="Токмак", initial="К"),
            _sentence("f", article_id=6, age=40, place="Токмак", initial="Л"),
        ]
    )
    assert [len(case.sentences) for case in cases] == [1, 1, 1, 1, 1, 1]


def test_a_sentence_that_tells_little_joins_only_where_exactly_one_case_fits() -> None:
    """Two teenagers of one town: a sentence with no age cannot be given to either."""
    cases = group_cases(
        [
            _sentence("a", article_id=1, age=15, place="Канаш"),
            _sentence("b", article_id=2, age=17, place="Канаш"),
            _sentence("c", article_id=3, place="Канаш"),
        ]
    )
    assert [[sentence.key for sentence in case.sentences] for case in cases] == [
        ["a"],
        ["b"],
        ["c"],
    ]


def test_the_key_holds_while_the_text_tells_the_same() -> None:
    """A mark is kept by the key: a new sentence about the person must not move it."""
    one = group_cases([_sentence("z", age=15, place="Канаш")])
    two = group_cases(
        [_sentence("z", age=15, place="Канаш"), _sentence("a", article_id=2, age=15, place="Канаш")]
    )
    assert one[0].key == two[0].key == "unnamed:канаш|male|15|"
    # No place told: the sentence's own key.
    assert group_cases([_sentence("q", age=20)])[0].key == "unnamed:q"


def test_two_cases_never_share_a_key() -> None:
    """A sentence that fits two people joins neither and stands alone; a second such
    sentence tells exactly what the first does. A mark on one must not fall on the other."""
    cases = group_cases(
        [
            _sentence("a", age=15, place="Канаш", initial="К"),
            _sentence("b", article_id=2, age=15, place="Канаш", initial="Л"),
            _sentence("c", article_id=3, age=15, place="Канаш"),
            _sentence("d", article_id=4, age=15, place="Канаш"),
        ]
    )
    keys = [case.key for case in cases]
    assert len(cases) == 4 and len(set(keys)) == 4
    # The first to tell it keeps the telling for its key, so its mark stays where it was.
    assert keys[2] == "unnamed:канаш|male|15|" and keys[3] == "unnamed:канаш|male|15|#d"


def test_a_case_is_political_only_by_an_article_it_names() -> None:
    """No verdict exists for the unnamed; an article the text does not name is no reason."""
    assert not group_cases([_sentence("a", age=30, place="Тотьма")])[0].political
    assert not group_cases([_sentence("a", age=30, place="Тотьма", articles=("228",))])[0].political
    # 205 is on the operator's list of the перечень's articles; 282.2 is «АУЕ».
    assert group_cases([_sentence("a", place="Тюмень", articles=("205",))])[0].political
    assert group_cases([_sentence("a", place="Благовещенск", articles=("282.2",))])[0].political
