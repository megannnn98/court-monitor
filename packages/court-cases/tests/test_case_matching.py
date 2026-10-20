from __future__ import annotations

from datetime import date

from cases.matching import Evidence, Match, choose_case, score_case, score_pair

LONG_QUOTE = "x" * 80


def event(
    event_type: str = "detention",
    *,
    day: date | None = None,
    targets: tuple[str, ...] = ("иван иванов",),
    charges: tuple[str, ...] = (),
    courts: tuple[str, ...] = (),
    authorities: tuple[str, ...] = (),
    places: tuple[str, ...] = (),
    case_number: str | None = None,
    quote: str = "a",
) -> Evidence:
    return Evidence(
        event_type=event_type,
        event_date=day,
        targets=frozenset(targets),
        charges=frozenset(charges),
        courts=frozenset(courts),
        authorities=frozenset(authorities),
        places=frozenset(places),
        case_number=case_number,
        quote_hash=quote,
        quote_length=len(quote),
    )


def test_name_and_article_alone_never_join() -> None:
    match = score_pair(event(charges=("205.2/3",)), event("sentence", charges=("205.2/3",)))

    assert not match.automatic


def test_name_and_the_same_article_of_different_steps_need_a_place() -> None:
    first = event(charges=("205.2/3",), places=("москва",))
    second = event("sentence", charges=("205.2/3",), places=("москва",), day=date(2026, 5, 1))

    assert score_pair(first, second).automatic


def test_two_reports_of_one_detention_join_by_the_same_words() -> None:
    assert score_pair(event(quote=LONG_QUOTE), event(quote=LONG_QUOTE)).automatic


def test_undated_events_of_one_type_in_one_city_are_not_one_event() -> None:
    first = event("arrest", places=("москва",))
    second = event("arrest", places=("москва",))

    assert not score_pair(first, second).automatic


def test_a_missing_value_is_no_conflict() -> None:
    match = score_pair(event(day=date(2024, 3, 1), places=("москва",)), event(places=("москва",)))

    assert match.vetoes == ()


def test_different_dates_of_one_step_forbid_the_tie() -> None:
    first = event(day=date(2024, 3, 1), quote=LONG_QUOTE)
    second = event(day=date(2025, 3, 1), quote=LONG_QUOTE)

    match = score_pair(first, second)

    assert "different_dates_of_same_event_type" in match.vetoes
    assert not match.automatic


def test_a_verdict_before_the_arrest_forbids_the_tie() -> None:
    first = event("arrest", day=date(2025, 1, 1), charges=("228/1",), places=("омск",))
    second = event("sentence", day=date(2024, 1, 1), charges=("228/1",), places=("омск",))

    assert "stage_order_conflict" in score_pair(first, second).vetoes


def test_two_case_numbers_forbid_the_tie_even_with_the_same_words() -> None:
    first = event(case_number="1-1", quote=LONG_QUOTE)
    second = event(case_number="1-2", quote=LONG_QUOTE)

    assert score_pair(first, second).vetoes == ("different_case_numbers",)


def test_a_bare_case_number_does_not_join_but_one_with_a_court_does() -> None:
    bare = score_pair(event(case_number="1-1"), event("sentence", case_number="1-1"))
    scoped = score_pair(
        event(case_number="1-1", courts=("суд",)),
        event("sentence", case_number="1-1", courts=("суд",)),
    )

    assert not bare.automatic
    assert scoped.automatic


def test_a_case_number_cannot_be_the_same_in_two_courts() -> None:
    match = score_pair(
        event(case_number="1-1", courts=("суд а",)),
        event("sentence", case_number="1-1", courts=("суд б",)),
    )

    assert "case_number_scope_conflict" in match.vetoes


def test_another_person_is_no_match_whatever_else_agrees() -> None:
    other = event(targets=("петр петров",), quote=LONG_QUOTE)

    assert score_pair(event(quote=LONG_QUOTE), other).score == 0.0


def test_an_event_without_a_target_joins_nothing_by_the_words() -> None:
    nobody = event(targets=(), quote=LONG_QUOTE)

    assert not score_pair(nobody, event(quote=LONG_QUOTE)).automatic


def test_a_number_contradiction_in_any_member_is_carried_to_the_case() -> None:
    numbered = event("charge", case_number="1-2")
    members = [event(quote=LONG_QUOTE), numbered]

    match = score_case(event(case_number="1-1", quote=LONG_QUOTE), members)

    assert not match.automatic


def test_two_strong_cases_are_an_ambiguity_not_a_pick() -> None:
    strong = Match(0.9, ("exact_quote",))

    assert choose_case([("a", strong), ("b", strong)]) is None
    assert choose_case([("a", strong), ("b", Match(0.4, ()))]) == "a"


def test_evidence_survives_json() -> None:
    original = event(day=date(2024, 3, 1), charges=("205.2/3",), courts=("суд",))

    assert Evidence.from_json(original.to_json()) == original
