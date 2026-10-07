"""Rows of articles folded into cases, and the counts over the cases."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from entities.sentence_cases import EXCLUDE, ONLY, POLITICAL, Filters, fold, listing, select, stats

_ids = iter(range(1, 10_000))


def _row(person: str = "", /, **fields: Any) -> Any:
    """A row as the query gives it; a person named by two words has a key."""
    row_id = next(_ids)
    base: dict[str, Any] = {
        "id": row_id,
        "article_id": row_id,
        "person": person or "житель города",
        "person_key": person.lower() if " " in person else None,
        "region": "Свердловская область",
        "kind": "colony",
        "months": 72,
        "fine_rub": 0,
        "in_absentia": False,
        "sentenced_on": "2026-09-24",
        "articles": ["207.3"],
        "reason": "antiwar_speech",
        "reason_text": "посты о войне",
        "quote": "приговорил",
        "title": "Приговор",
        "published_at": datetime(2026, 9, 25, tzinfo=UTC),
        "source": "Новости",
    }
    return SimpleNamespace(**(base | fields))


def test_one_sentence_told_by_three_articles_is_one_case() -> None:
    (case,) = fold([_row("Петров Иван"), _row("Петров Иван"), _row("Петров Иван")])

    assert len(case.publications) == 3
    assert (case.person, case.months) == ("Петров Иван", 72)


def test_two_people_with_the_same_sentence_are_two_cases() -> None:
    found = fold([_row("Ронжес Оксана", months=156), _row("Сахаров Артём", months=156)])

    assert sorted(case.person for case in found) == ["Ронжес Оксана", "Сахаров Артём"]


def test_a_person_with_two_cases_has_two() -> None:
    found = fold(
        [
            _row("Пономаренко Мария", months=72),
            _row("Пономаренко Мария", months=22, reason="other_political"),
        ]
    )

    assert sorted(case.months for case in found) == [22, 72]


def test_a_reason_two_articles_call_differently_does_not_split_the_case() -> None:
    (case,) = fold(
        [
            _row("Антонович Михаил", months=96, reason="organization"),
            _row("Антонович Михаил", months=96, reason="other_political"),
        ]
    )

    assert case.months == 96


def test_a_term_an_appeal_changed_is_one_case_with_the_latest_term() -> None:
    (case,) = fold(
        [
            _row("Беркович Евгения", months=72, sentenced_on="2024-07"),
            _row("Беркович Евгения", months=67, sentenced_on="2024-12"),
        ]
    )

    assert case.months == 67


def test_an_unnamed_row_joins_the_one_named_case_it_fits() -> None:
    (case,) = fold([_row("Антонович Михаил", months=96), _row(months=96), _row(months=96)])

    assert (case.person, case.named, len(case.publications)) == ("Антонович Михаил", True, 3)


def test_an_unnamed_row_that_fits_two_named_cases_joins_neither() -> None:
    found = fold([_row("Ронжес Оксана"), _row("Сахаров Артём"), _row()])

    assert len(found) == 3


def test_unnamed_rows_of_one_sentence_are_one_case() -> None:
    found = fold([_row(), _row(), _row(region="Тюменская область")])

    assert sorted(len(case.publications) for case in found) == [1, 2]


def test_a_day_and_an_added_fine_do_not_split_an_unnamed_persecution() -> None:
    # Four articles on one sentence: a day or a month apart, the fine told or not.
    (case,) = fold(
        [
            _row(months=228, fine_rub=200_000, sentenced_on="2026-09-30"),
            _row(months=228, sentenced_on="2026-09-29"),
            _row(months=228, fine_rub=200_000, sentenced_on="2026"),
            # The latest tells no fine: the fine is still the case's.
            _row(months=228, sentenced_on="2026-10-01"),
        ]
    )

    assert (len(case.publications), case.fine_rub) == (4, 200_000)


def test_another_year_or_another_term_is_another_persecution() -> None:
    found = fold(
        [
            _row(months=228, sentenced_on="2026-09-23"),
            _row(months=228, sentenced_on="2025-09-23"),
            _row(months=216, sentenced_on="2026-09-23"),
            _row(months=228, sentenced_on="2026-09-23", kind="suspended"),
        ]
    )

    assert len(found) == 4


def test_common_crimes_are_one_case_only_within_a_month() -> None:
    # «Два года условно» is given every week: the year alone says nothing.
    crime = {"reason": "not_political", "kind": "suspended", "months": 24}
    found = fold(
        [
            _row(**crime, sentenced_on="2026-09-23"),
            _row(**crime, sentenced_on="2026-09-25"),
            _row(**crime, sentenced_on="2026-08-25"),
            _row(**crime, sentenced_on="2026"),
            _row(**crime, sentenced_on="2026"),
        ]
    )

    assert sorted(len(case.publications) for case in found) == [1, 1, 1, 2]


def test_a_fine_as_the_punishment_tells_cases_apart() -> None:
    fine = {"kind": "fine", "months": 0}
    found = fold(
        [
            _row(**fine, fine_rub=35_000),
            _row(**fine, fine_rub=35_000),
            _row(**fine, fine_rub=50_000),
        ]
    )

    assert sorted(len(case.publications) for case in found) == [1, 2]


def test_a_row_without_a_date_joins_the_one_case_of_its_region_and_term() -> None:
    (case,) = fold([_row("Антонович Михаил", months=96), _row(months=96, sentenced_on="")])

    assert len(case.publications) == 2


def test_a_row_without_a_date_that_fits_two_cases_or_none_stays_apart() -> None:
    two = fold([_row("Ронжес Оксана"), _row("Сахаров Артём"), _row(sentenced_on="")])
    none = fold([_row(sentenced_on=""), _row(sentenced_on="")])

    assert len(two) == 3
    assert len(none) == 2


def test_an_unnamed_row_that_tells_too_little_is_its_own_case() -> None:
    # No region or no punishment: nothing says two such rows are one sentence.
    found = fold([_row(region=""), _row(region=""), _row(months=0), _row(months=0)])

    assert len(found) == 4


def test_a_case_takes_from_other_rows_what_its_latest_lacks() -> None:
    # The latest article tells the day and nothing else; the earlier one told the rest.
    (case,) = fold(
        [
            _row("Петров Иван", sentenced_on="2026-09"),
            _row("Петров Иван", sentenced_on="2026-09-24", region="", months=0, kind="unknown"),
        ]
    )

    assert case.sentenced_on == "2026-09-24"
    assert (case.region, case.kind, case.months) == ("Свердловская область", "colony", 72)


def _cases() -> list[Any]:
    return fold(
        [
            _row("А Б", months=120),
            _row("В Г", months=60),
            _row("Д Е", months=36, region="Москва"),
            _row("Ж З", months=24, region="Москва", in_absentia=True),
            _row("И К", months=12, region="Москва", kind="suspended"),
            _row("Л М", months=240, region="Москва", reason="not_political"),
            _row("Н О", months=48, region="", sentenced_on=""),
        ]
    )


def test_a_count_by_region_gives_terms_of_the_imprisoned_only() -> None:
    counted = stats(_cases(), Filters(reasons=(POLITICAL,)), group_by="region", sort="mean_years")

    assert [(group.name, group.cases, group.imprisoned) for group in counted.groups] == [
        ("Свердловская область", 2, 2),
        ("Москва", 3, 2),
    ]
    sverdlovsk, moscow = counted.groups
    assert (sverdlovsk.mean_years, sverdlovsk.median_years, sverdlovsk.max_years) == (7.5, 7.5, 10)
    # The suspended year is no term in prison; the common crime is not political.
    assert (moscow.mean_years, moscow.suspended, moscow.in_absentia) == (2.5, 1, 1)
    assert counted.unknown == 1
    assert counted.total.cases == 6


def test_a_group_with_too_few_terms_is_left_out_and_counted() -> None:
    counted = stats(_cases(), Filters(), group_by="region", sort="mean_years", min_imprisoned=3)

    assert [group.name for group in counted.groups] == ["Москва"]
    # Not ranked, yet named: the answer may say what the one sentence there was.
    assert counted.small == 1
    assert [(group.name, group.imprisoned) for group in counted.small_groups] == [
        ("Свердловская область", 2)
    ]


def test_a_count_of_cases_leaves_no_group_out() -> None:
    # The model may ask for «three terms at least» where nothing is ranked by a term.
    counted = stats(_cases(), Filters(), group_by="region", sort="cases", min_imprisoned=3)

    assert [group.name for group in counted.groups] == ["Москва", "Свердловская область"]
    assert (counted.small, counted.small_groups) == (0, ())


def test_filters_narrow_the_selection() -> None:
    all_cases = _cases()

    assert select(all_cases, Filters(region="Москва")).total == 4
    assert select(all_cases, Filters(absentia=ONLY)).total == 1
    assert select(all_cases, Filters(absentia=EXCLUDE)).total == 6
    assert select(all_cases, Filters(kind="suspended")).total == 1
    assert select(all_cases, Filters(reasons=("not_political",))).total == 1
    assert select(all_cases, Filters(article="207.3")).total == 7
    assert select(all_cases, Filters(article="207")).total == 0


def test_a_year_filter_names_the_cases_whose_year_is_unknown() -> None:
    selection = select(_cases(), Filters(year_from=2026, year_to=2026))

    assert (selection.total, selection.year_unknown) == (6, 1)
    assert select(_cases(), Filters(year_from=2027)).total == 0
    assert select(_cases(), Filters(year_to=2025)).total == 0


def test_an_article_with_a_part_counts_under_its_number() -> None:
    found = fold([_row("А Б", articles=["207.3 ч. 2", "280"]), _row("В Г", articles=[])])

    counted = stats(found, Filters(), group_by="article")

    assert {group.name: group.cases for group in counted.groups} == {"207.3": 1, "280": 1}
    assert select(found, Filters(article="207.3")).total == 1
    assert counted.unknown == 1


def test_a_list_gives_the_longest_or_the_latest_first() -> None:
    found = fold(
        [
            _row("А Б", months=120, sentenced_on="2024-01-01"),
            _row("В Г", months=60, sentenced_on="2026-01-01"),
            _row("Д Е", months=36, sentenced_on="2025-01-01"),
        ]
    )

    longest = listing(found, Filters(), sort="months", limit=2)
    latest = listing(found, Filters(), sort="date", limit=2)

    assert [case.person for case in longest.cases] == ["А Б", "В Г"]
    assert [case.person for case in latest.cases] == ["В Г", "Д Е"]
    assert longest.total == 3
