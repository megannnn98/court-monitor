"""The articles a person is put on the Rosfinmonitoring list for, and what they say."""

from __future__ import annotations

import pytest

from entities.rf_articles import AWAITED, LISTED, SOMETIMES_KIND, listing


@pytest.mark.parametrize(
    "article",
    ["205", "205.1", "205.2", "205.6", "281", "281.3", "282", "282.2", "282.3", "208"],
)
def test_every_article_of_the_families_she_named_leads_to_the_list(article: str) -> None:
    found = listing([article], None)
    assert found is not None and (found.kind, found.articles) == (AWAITED, (article,))


@pytest.mark.parametrize(
    "article",
    # A neighbour by number is not of the family: 2050 is no 205, 208.1 was not named,
    # 280.3 is not the 280 she named, and common crime is nothing to the list.
    ["2050", "20", "208.1", "280.3", "280.1", "207", "207.1", "275", "105", "228.1", ""],
)
def test_an_article_she_did_not_name_says_nothing(article: str) -> None:
    assert listing([article], None) is None


def test_on_the_list_is_said_only_of_a_match_with_the_patronymic() -> None:
    assert listing(["205.2"], "full").kind == LISTED  # type: ignore[union-attr]
    # A name and a surname alone may be a namesake's entry: the person is still awaited.
    assert listing(["205.2"], "name").kind == AWAITED  # type: ignore[union-attr]
    assert listing(["205.2"], None).kind == AWAITED  # type: ignore[union-attr]


def test_an_article_of_now_and_then_is_nothing_to_wait_for() -> None:
    found = listing(["280", "105"], None)
    assert found is not None and (found.kind, found.articles) == (SOMETIMES_KIND, ("280",))
    assert found.text == "статья 280 — в перечень включают иногда"
    assert listing(["207.3"], "full").kind == SOMETIMES_KIND  # type: ignore[union-attr]


def test_the_surer_article_is_the_one_named_and_each_once() -> None:
    found = listing(["280", "282.2", "105", "205.2", "282.2"], None)
    assert found is not None and found.articles == ("282.2", "205.2")
    assert found.text == "статья перечня: 282.2, 205.2 — в перечне пока нет"
    assert listing(["205"], "full").text == "статья перечня: 205 — в перечне"  # type: ignore[union-attr]
