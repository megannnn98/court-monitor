"""What counts as a charge of *this* person: the local rule the extractor defers to.

Deterministic, offline and readable — a charge is an accusation about a name, and the
words that make it one are the words around it. Each case here is a text the open web
really produces.
"""

from __future__ import annotations

import pytest

from investigation.services.charges import normalised_charges
from investigation.services.external import _charge_lead, charges_of

NAME = "Мария Бонцлер"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A charge of her, in every way the news writes it.
        ("Марию Бонцлер обвиняют по ч. 3 ст. 207.3 УК РФ.", {"ч. 3 ст. 207.3 УК РФ"}),
        ("Мария Бонцлер: суд приговорил по ст. 280 УК РФ.", {"ст. 280 УК РФ"}),
        ("Задержана Мария Бонцлер. Ей вменяют ст. 210 УК РФ.", {"ст. 210 УК РФ"}),
        # An accusation of somebody else, in a text that names her as well.
        (
            "Марию Бонцлер обвинили по ст. 207.3 УК РФ. А Иванову вменяют ст. 280 УК РФ.",
            {"ст. 207.3 УК РФ"},
        ),
        # A namesake, a sportsman and a registry card.
        ("Обвинили Марию Бонцлер из другого региона по ст. 116 УК РФ.", {"ст. 116 УК РФ"}),
        # A law quoted in passing, and a comment about the law itself.
        ("Статья 228 УК РФ касается незаконного оборота наркотиков.", set()),
        ("Дело возбуждено по ст. 360 УК РФ в отношении Иванова.", set()),
        # An article far away from the name: fifty words of other news first.
        (
            "Мария Бонцлер была в суде. "
            + "Суд continued to hear the case. " * 50
            + "По ч. 3 ст. 207.3 УК РФ.",
            set(),
        ),
        # «Статья» and «статус» are not a charge.
        ("Изменился статус Марии Бонцлер.", set()),
        ("Мария Бонцлер: статья о жёнах военнослужащих.", set()),
        # Another country's code is nobody's charge here.
        ("Марию Бонцлер обвинили по ч. 3 ст. 342 УК Беларуси.", set()),
        # One case, two citations.
        (
            "Марию Бонцлер обвиняют по ч. 1 ст. 280 УК РФ и ч. 2 ст. 282 УК РФ.",
            {"ч. 1 ст. 280 УК РФ", "ч. 2 ст. 282 УК РФ"},
        ),
        (
            "Марию Бонцлер обвиняют по ст. 280 УК РФ, а также по ст. 282 УК РФ.",
            {"ст. 280 УК РФ", "ст. 282 УК РФ"},
        ),
        # And a Russian charge is not lost to a word that only begins like a country.
        ("Марию Бонцлер обвинили по ст. 280 УК кроме того.", {"ст. 280 УК РФ"}),
        ("Марию Бонцлер обвинили по ст. 280 УК. В Крыму то же.", {"ст. 280 УК РФ"}),
    ],
)
def test_only_a_charge_of_this_person_is_a_charge_of_this_person(
    text: str, expected: set[str]
) -> None:
    assert charges_of(text, NAME) == expected


def test_the_lead_is_the_same_question_with_an_empty_answer_asked_of_it() -> None:
    assert _charge_lead("Марию Бонцлер обвиняют по ч. 3 ст. 207.3 УК РФ.", NAME) is True
    assert _charge_lead("Дело возбуждено по ст. 360 УК РФ в отношении Иванова.", NAME) is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ст. 280 УК", {"ст. 280 УК РФ"}),
        ("ч. 2 ст. 20.3.3 КоАП РФ", set()),
        ("ст. 159 ГК", set()),
        # «кроме того» and «Крым» only begin like a country.
        ("ст. 280 УК кроме того", {"ст. 280 УК РФ"}),
        ("ст. 280 УК Крым", {"ст. 280 УК РФ"}),
        ("ст. 342 УК РБ", set()),
        ("ч. 3 ст. 286 УК Республики Беларусь", set()),
        # And the country a longer way off does not reach the citation.
        ("ст. 280 УК РФ. В Беларуси то же самое.", {"ст. 280 УК РФ"}),
    ],
)
def test_a_code_is_read_as_ours_only_where_it_is_ours(text: str, expected: set[str]) -> None:
    assert normalised_charges(text) == expected
