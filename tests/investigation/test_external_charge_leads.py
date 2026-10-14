"""What counts as a charge of *this* person: the local rule a charge lead is held to.

Deterministic, offline and readable — a charge is an accusation about a name, and the
words that make it one are the words around it.
"""

from __future__ import annotations

import pytest

from investigation.models import InvestigationQuery
from investigation.services.charges import CHARGE, russian_charge
from investigation.services.external import SourceSearchRetriever, _charge_lead, charges_of

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
        # Even a bare comma between the two is one case.
        (
            "Марию Бонцлер обвиняют по ч. 1 ст. 280 УК РФ, ч. 2 ст. 282 УК РФ.",
            {"ч. 1 ст. 280 УК РФ", "ч. 2 ст. 282 УК РФ"},
        ),
        # Another man's case first, and his enumeration after it: not hers either.
        (
            "Петрова обвиняют по ст. 207.3 УК РФ. Иванову вменяют ст. 280 УК РФ и ст. 282 УК РФ.",
            set(),
        ),
        # And her own case after his: hers again.
        (
            "Иванову вменяют ст. 280 УК РФ. Марию Бонцлер обвиняют по ст. 207.3 УК РФ.",
            {"ст. 207.3 УК РФ"},
        ),
        # A foreign citation ends her enumeration: the country's name after the code is a
        # word between the two citations, so the article after it is not hers.
        (
            (
                "Марию Бонцлер обвиняют по ст. 280 УК РФ, по ст. 342 УК Беларуси, "
                "а ст. 282 УК РФ — Иванову."
            ),
            {"ст. 280 УК РФ"},
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


def test_an_enumeration_stops_where_the_next_person_starts() -> None:
    """The rule that carries a verdict from one citation to the next carries it only
    while the same case goes on. A citation of somebody else ends it — and his own
    enumeration after that is his, not the first person's."""
    both = "Петрова обвиняют по ст. 207.3 УК РФ. Иванову вменяют ст. 280 УК РФ и ст. 282 УК РФ."

    assert charges_of(both, "Петрова") == {"ст. 207.3 УК РФ"}
    assert charges_of(both, "Иванов") == {"ст. 280 УК РФ", "ст. 282 УК РФ"}


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
        ("осуждён по ст. 342 УК Беларуси", set()),
        ("по ст. 109 УК Украины", set()),
        ("по ст. 233 УК Казахстана", set()),
        ("по ст. 318 УК Армении", set()),
        ("по ст. 207.3 УК Российской Федерации", {"ст. 207.3 УК РФ"}),
        ("по статьёй 280 УК", {"ст. 280 УК РФ"}),
        ("ч. 3 ст. 286 УК Республики Беларусь", set()),
        # And the country a longer way off does not reach the citation.
        ("ст. 280 УК РФ. В Беларуси то же самое.", {"ст. 280 УК РФ"}),
    ],
)
def test_a_code_is_read_as_ours_only_where_it_is_ours(text: str, expected: set[str]) -> None:
    assert _spellings(text) == expected


def _spellings(text: str) -> set[str]:
    """Every Russian Criminal Code citation of a text, whoever it is about."""
    return {
        spelling
        for match in CHARGE.finditer(text)
        if (spelling := russian_charge(match, text)) is not None
    }


def test_a_name_written_after_the_citation_is_not_read() -> None:
    """A known limit, kept on purpose: only the words before a citation are read, so
    «По ст. 280 УК РФ обвинили Марию Бонцлер» leads to nothing and the articles are
    looked for again. Reading after it as well would hand her the article of the next
    person named."""
    assert charges_of("По ст. 280 УК РФ обвинили Марию Бонцлер.", NAME) == set()


@pytest.mark.parametrize(
    ("text", "leads"),
    [
        ("Марию Бонцлер обвиняют по ст. 280 УК РФ, статья о фейках.", ("charges",)),
        # The word looked for and no charge of hers.
        ("Мария Бонцлер: статья о жёнах военнослужащих.", ()),
        # A charge of hers, and none of the words looked for.
        ("Марию Бонцлер обвиняют по ч. 3 ст. 207.3 УК РФ.", ()),
        ("Мария Бонцлер выступила в суде.", ()),
    ],
)
def test_a_lead_needs_both_the_word_looked_for_and_a_charge_of_hers(
    text: str, leads: tuple[str, ...]
) -> None:
    query = InvestigationQuery(kind="keywords", name=NAME, terms=("статья",), fact="charges")

    assert SourceSearchRetriever._leads(query, text) == leads
