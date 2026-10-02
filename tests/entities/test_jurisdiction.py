"""Which court tries an article from a place, as the base has seen it."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import AirtableKnownPersonRecord
from entities.jurisdiction import CourtHint, CourtHints, Jurisdiction
from web.ui.court_hints import courts_html

VOVS = "2-й Восточный окружной военный суд"
ROWS = [
    (
        "Алтайский край",
        "Рубцовск",
        "ст. 205.1 УК РФ ч. 1.1",
        VOVS,
        "https://2vovs.sudrf.test/a?x=1",
    ),
    (
        "Алтайский край",
        "Барнаул",
        "ст. 205.1 УК РФ,ст. 280 УК РФ",
        VOVS,
        "https://2vovs.sudrf.test/b",
    ),
    ("Алтайский край", "", "ст. 205.1 УК РФ", VOVS, "http://old.sudrf.test/c"),
    ("Алтайский край", "Барнаул", "ст. 205.1 УК РФ", "Барнаульский суд", "не ссылка"),
    ("Алтайский край", "Барнаул", "ст. 205.1 УК РФ", "Алтайский краевой суд", None),
    ("Алтайский край", "Бийск", "ст. 205.1 УК РФ", "Бийский суд", None),
    # Another article, another region, no article at all.
    ("Алтайский край", "Барнаул", "ст. 280 УК РФ ч. 2", "Центральный районный суд", None),
    ("Омская область", "Омск", "ст. 205.1 УК РФ", "Омский суд", None),
    ("Алтайский край", "Барнаул", "Нет информации", "Никакой суд", None),
]


def test_the_courts_of_a_place_and_an_article_the_most_frequent_first() -> None:
    found = Jurisdiction(ROWS).courts("Алтайский край", ["205.1"])

    assert found == CourtHints(
        [
            # The site the base links to most often.
            CourtHint(VOVS, 3, "https://2vovs.sudrf.test"),
            # Equal counts stand by name; a cell that is no link gives no site.
            CourtHint("Алтайский краевой суд", 1, None),
            CourtHint("Барнаульский суд", 1, None),
        ],
        6,
    )


def test_the_place_may_be_a_city_and_the_article_any_of_several() -> None:
    courts = Jurisdiction(ROWS)

    assert [hint.court for hint in courts.courts("Рубцовск", ["205.1"]).shown] == [VOVS]
    both = courts.courts("Барнаул", [280, "205.1"], shown=10)
    assert [(hint.court, hint.cases) for hint in both.shown] == [
        # One person under both articles is one sentence.
        (VOVS, 1),
        ("Алтайский краевой суд", 1),
        ("Барнаульский суд", 1),
        ("Центральный районный суд", 1),
    ]
    assert both.total == 4


def test_a_cell_of_two_courts_is_two_courts() -> None:
    rows = [
        ("Тула", "", "ст. 280 УК РФ", "Суд Б , Суд  А", "https://b.test/card"),
        ("Тула", "", "ст. 280 УК РФ", "Суд А", "https://a.test/card"),
        ("Томск", "", "ст. 280 УК РФ", "Томский суд", None),
    ]

    found = Jurisdiction(rows).courts("Тула", ["280"])

    # The link beside two courts does not say whose site it is.
    assert found == CourtHints(
        [CourtHint("Суд А", 2, "https://a.test"), CourtHint("Суд Б", 1, None)], 3
    )
    assert Jurisdiction(rows).courts("Омск", ["280"]) == CourtHints([], 0)


def test_the_count_orders_before_the_name() -> None:
    rows = [("Тула", "", "ст. 280 УК РФ", court, None) for court in ("А суд", "Я суд", "Я суд")]

    found = Jurisdiction(rows).courts("Тула", ["280"])

    assert [(hint.court, hint.cases) for hint in found.shown] == [("Я суд", 2), ("А суд", 1)]


def test_nothing_without_a_place_an_article_or_a_sentence() -> None:
    courts = Jurisdiction(ROWS)

    assert courts.courts("", ["205.1"]) == CourtHints([], 0)
    assert courts.courts("Алтайский край", []) == CourtHints([], 0)
    assert courts.courts("Алтайский край", ["275"]) == CourtHints([], 0)
    assert courts.courts("Тула", ["205.1"]) == CourtHints([], 0)


def test_the_sentences_are_read_from_the_base(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        for index, court in enumerate([VOVS, None]):
            session.add(
                AirtableKnownPersonRecord(
                    external_id=f"rec{index}",
                    full_name=f"Человек Номер {index}",
                    normalized_name="x",
                    matching_key="x",
                    region="Алтайский край",
                    articles="ст. 205.1 УК РФ",
                    court=court,
                    court_card_url="https://2vovs.sudrf.test/a",
                )
            )

    with session_factory() as session:
        found = Jurisdiction.from_session(session).courts("Алтайский край", ["205.1"])

    assert found == CourtHints([CourtHint(VOVS, 1, "https://2vovs.sudrf.test")], 1)


def test_the_line_of_a_card() -> None:
    assert courts_html(CourtHints([], 0)) == ""
    line = courts_html(
        CourtHints(
            [CourtHint("Суд <1>", 5, "https://a.test"), CourtHint("Суд 2", 2, "javascript:x")], 9
        )
    )
    assert line == (
        '<p class="muted">Где судят по этой статье из этого места (приговоры в базе Airtable, '
        'всего 9): Суд &lt;1&gt; — 5 (<a href="https://a.test">сайт суда</a>); Суд 2 — 2; '
        "другие суды — 2.</p>"
    )
