"""The matching over hand-labelled pairs of events: wrong merges and missed ties, counted
apart. The pairs are built by hand from the patterns of real reports (they are not a
sample of the corpus), so the numbers say what the rules do on the known cases, not how
often they are right on the news."""

from __future__ import annotations

from datetime import date

from test_case_matching import LONG_QUOTE, event

from cases.matching import score_pair

IVAN = ("иван петров",)
PETR = ("пётр сидоров",)
LAW = ("205.2/3",)
DAY = date(2024, 3, 1)

# (what it is, a, b, are they one case)
PAIRS = [
    # One case, and the rules can tell.
    ("one report copied", event(quote=LONG_QUOTE), event(quote=LONG_QUOTE), True),
    (
        "detention, arrest: part, body, place",
        event(charges=LAW, authorities=("фсб",), places=("москва",), day=DAY),
        event(
            "arrest", charges=LAW, authorities=("фсб",), places=("москва",), day=date(2024, 3, 3)
        ),
        True,
    ),
    (
        "arrest, sentence two years later: part and place",
        event("arrest", charges=LAW, places=("москва",), day=DAY),
        event("sentence", charges=LAW, places=("москва",), day=date(2026, 5, 1)),
        True,
    ),
    (
        "case opened, detention: part, body, place",
        event("case_opened", charges=LAW, authorities=("фсб",), places=("москва",)),
        event(charges=LAW, authorities=("фсб",), places=("москва",)),
        True,
    ),
    (
        "one detention reported twice, same day",
        event(charges=LAW, places=("москва",), day=DAY),
        event(charges=LAW, places=("москва",), day=DAY),
        True,
    ),
    (
        "one sentence reported twice, same court",
        event("sentence", charges=LAW, courts=("мосгорсуд",), day=DAY),
        event("sentence", charges=LAW, courts=("мосгорсуд",), day=DAY),
        True,
    ),
    (
        "charge, sentence: part and court",
        event("charge", charges=LAW, courts=("мосгорсуд",)),
        event("sentence", charges=LAW, courts=("мосгорсуд",), day=DAY),
        True,
    ),
    (
        "two accused, one of them later",
        event(targets=("иван петров", "пётр сидоров"), charges=LAW, places=("омск",)),
        event("sentence", targets=("иван петров",), charges=LAW, places=("омск",)),
        True,
    ),
    # One case, and the evidence is too thin: a miss is the price of never guessing.
    (
        "detention, arrest: a place only",
        event(places=("москва",)),
        event("arrest", places=("москва",)),
        True,
    ),
    (
        "detention with article, sentence without",
        event(charges=LAW, places=("москва",)),
        event("sentence", places=("москва",)),
        True,
    ),
    (
        "the charge changed between the steps",
        event(charges=("205.2/3",), places=("москва",)),
        event("sentence", charges=("205.2/2",), places=("москва",)),
        True,
    ),
    (
        "case opened bare, sentence full",
        event("case_opened"),
        event("sentence", charges=LAW, places=("москва",)),
        True,
    ),
    (
        "sentence and detention, article, no place",
        event(charges=LAW),
        event("sentence", charges=LAW, courts=("суд",)),
        True,
    ),
    # Two cases.
    (
        "two cities, one article",
        event(charges=LAW, places=("москва",)),
        event("sentence", charges=LAW, places=("омск",)),
        False,
    ),
    (
        "two charges, two cities",
        event(charges=("228/1",), places=("омск",)),
        event("arrest", charges=LAW, places=("москва",)),
        False,
    ),
    (
        "another article part, another place",
        event(charges=("205.2/2",), places=("москва",)),
        event("sentence", charges=("205.2/3",), places=("омск",)),
        False,
    ),
    (
        "the same words, two people",
        event(targets=IVAN, quote=LONG_QUOTE),
        event(targets=PETR, quote=LONG_QUOTE),
        False,
    ),
    (
        "two sentences, two dates",
        event("sentence", charges=LAW, courts=("суд",), day=DAY),
        event("sentence", charges=LAW, courts=("суд",), day=date(2025, 3, 1)),
        False,
    ),
    (
        "re-detention a year later, same place and article",
        event(charges=LAW, places=("москва",), day=DAY),
        event(charges=LAW, places=("москва",), day=date(2025, 3, 1)),
        False,
    ),
    (
        "verdict before the arrest",
        event("arrest", charges=LAW, places=("омск",), day=date(2025, 1, 1)),
        event("sentence", charges=LAW, places=("омск",), day=date(2024, 1, 1)),
        False,
    ),
    (
        "two case numbers",
        event(case_number="1-1", quote=LONG_QUOTE),
        event("sentence", case_number="1-2", quote=LONG_QUOTE),
        False,
    ),
    (
        "unnamed and named, the same words",
        event(targets=(), quote=LONG_QUOTE),
        event(quote=LONG_QUOTE),
        False,
    ),
    (
        "namesakes: one key, two towns, two articles",
        event(charges=("228/1",), places=("томск",)),
        event("arrest", charges=LAW, places=("казань",)),
        False,
    ),
    ("only the person and the article", event(charges=LAW), event("sentence", charges=LAW), False),
    ("only the person", event(), event("sentence"), False),
    (
        "same day, same step, two articles of law, no place",
        event(charges=("228/1",), day=DAY),
        event(charges=("205.2/3",), day=DAY),
        False,
    ),
    (
        "undated detentions in one town",
        event("arrest", places=("москва",)),
        event("arrest", places=("москва",)),
        False,
    ),
]


def test_no_pair_of_different_cases_is_joined_and_the_misses_are_counted() -> None:
    merged_wrongly = [name for name, a, b, same in PAIRS if not same and score_pair(a, b).automatic]
    missed = [name for name, a, b, same in PAIRS if same and not score_pair(a, b).automatic]
    positives = sum(1 for *_, same in PAIRS if same)

    assert merged_wrongly == []
    # What the rules leave apart on purpose: the evidence of these is too thin to tie them.
    assert sorted(missed) == sorted(
        [
            "detention, arrest: a place only",
            "detention with article, sentence without",
            "the charge changed between the steps",
            "case opened bare, sentence full",
            "sentence and detention, article, no place",
        ]
    )
    assert positives - len(missed) == 8
