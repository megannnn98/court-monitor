"""The article number, as both sides write it.

The pipeline's column holds «291.1» and the annotation holds «ч. 4 ст. 291.1 УК»; the
number is the one thing they share, and it is the one thing that decides whether the web
may be credited with the fact.
"""

from evaluation.investigation.presence import _article_numbers


def test_the_number_is_taken_from_how_the_law_writes_the_article() -> None:
    assert _article_numbers("ч. 4 ст. 291.1 УК") == ["4", "291.1"]
    assert _article_numbers("ст. 276 УК") == ["276"]
    assert _article_numbers("275.1") == ["275.1"]


def test_a_phrase_without_a_number_yields_nothing_to_match() -> None:
    assert _article_numbers("сбыт сильнодействующего препарата") == []
    assert _article_numbers("") == []
