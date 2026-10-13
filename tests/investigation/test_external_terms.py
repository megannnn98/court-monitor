"""Which texts hold the word that was looked for: the rule every outside source is held
by, tested where it is decided.

The words of a query come from a scenario and are given to a site's own search, which
matches them loosely and receives only the first of them. So the text that comes back may
hold none of them at all, and a lead on it would end the search for the fact it was
asked for. What decides a lead is this one function.
"""

from __future__ import annotations

import pytest

from investigation.services.external import tells


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("Мария Бонцлер арестована по статье 280 УК", "статья"),
        ("вменили две статьи", "статья"),
        ("по части 3 статьёй", "статья"),
        ("вынесен приговор по статье", "статья"),
        ("статьи УК", "статья"),
    ],
)
def test_a_word_is_found_in_any_case_of_the_noun(text: str, term: str) -> None:
    assert tells(text, (term,))


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("предъявлено обвинение в госизмене", "обвинение"),
        ("он обвинён в госизмене", "обвинение"),
        ("обвиняется в госизмене", "обвинение"),
        ("против него обвиняемый", "обвиняемый"),
        ("арестован накануне суда", "арест"),
        ("в Тюменской области", "Тюмень"),
        ("в Тюмени задержали", "Тюмень"),
        ("задержан", "задержан"),
        ("задержали", "задержан"),
        ("осужден", "осужден"),
        ("осудили", "осужден"),
    ],
)
def test_words_of_one_root_are_one_word(text: str, term: str) -> None:
    """The dictionary's own forms of a word, and derivations of one root («обвин-»)."""
    assert tells(text, (term,))


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("изменился её статус", "статья"),
        ("по статистике судов", "статья"),
        ("статусы дел", "статья"),
        ("в арене", "арест"),
        ("Мария Бонцлер выступила в суде", "статья"),
        ("список фигурантов", "приговор"),
    ],
)
def test_a_word_that_only_begins_the_same_way_is_not_the_word_looked_for(
    text: str, term: str
) -> None:
    assert not tells(text, (term,))


@pytest.mark.parametrize(
    ("one", "other", "same"),
    [
        # The same word, another case of the noun: the dictionary alone.
        ("статья", "статьёй", True),
        ("приговор", "приговора", True),
        ("задержан", "задержали", True),
        # One root, two derivatives: this is what a bare lemma would miss.
        ("обвинение", "обвинён", True),
        ("обвинение", "обвиняется", True),
        ("обвиняемый", "обвинение", True),
        ("арест", "арестован", True),
        ("Тюмень", "Тюменской", True),
        # A longer word of one root, deliberately: a text about the detention is what
        # the follow-up looks for, whichever noun it uses.
        ("задержан", "задержка", True),
        ("задержан", "задержанность", True),
        # Two words that only begin alike: a shared prefix under five letters is a
        # coincidence, not a root.
        ("статья", "статус", False),
        ("статья", "статистика", False),
        ("арест", "арена", False),
        ("суд", "судья", False),
        ("дело", "делопроизводство", False),
    ],
)
def test_what_is_one_word_and_what_is_only_a_coincidence(one: str, other: str, same: bool) -> None:
    assert tells(other, (one,)) is same


def test_a_term_that_is_not_only_words_is_compared_as_written() -> None:
    assert tells("дело по части 2 статьи 205.2 УК", ("205.2",))
    assert not tells("дело по части 2 статьи 205 2 УК", ("205.2",))
    assert not tells("дело по 205.3 УК", ("205.2",))


def test_a_term_of_several_words_needs_all_of_them() -> None:
    assert tells("в Ростовской области", ("ростовской области",))
    assert not tells("в Ростовской области", ("ростовской области статья",))


def test_any_one_of_the_terms_is_enough_and_none_of_them_is_a_no() -> None:
    assert tells("Мария Бонцлер вменили статьи", ("статья", "приговор"))
    assert not tells("Мария Бонцлер вменили статьи", ("арест", "приговор"))


def test_a_text_with_no_words_holds_nothing() -> None:
    assert not tells("205.2 280.1 158.1", ("статья",))
    assert not tells("", ("статья",))
