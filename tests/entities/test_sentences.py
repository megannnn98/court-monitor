"""The sentences a model reads from the articles, on PostgreSQL."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import ArticleSentenceReadingRecord, ArticleSentenceRecord
from entities.sentences import (
    Publication,
    SentenceAnswer,
    SentenceFinder,
    SentenceReaderError,
    rows_of,
)

TEXT = (
    "Суд в Екатеринбурге приговорил Ивана Петрова к шести годам колонии за посты о войне. "
    "Его знакомому Олегу Сидорову прокурор запросил восемь лет."
)


def _answer(**fields: object) -> SentenceAnswer:
    base: dict[str, object] = {
        "person": "Иван Петров",
        "surname": "Петров",
        "first_name": "Иван",
        "stage": "sentenced",
        "region": "Свердловская область",
        "kind": "colony",
        "months": 72,
        "fine_rub": 0,
        "in_absentia": False,
        "date": "2026-09-24",
        "articles": ["207.3 ч. 2"],
        "reason": "antiwar_speech",
        "reason_text": "посты о войне",
        "quote": "приговорил Ивана Петрова к шести годам колонии",
    }
    return SentenceAnswer.model_validate(base | fields)


def _publication(article_id: int = 1, text: str = TEXT) -> Publication:
    return Publication(article_id, "Новости", "Приговор", "2026-09-24", text)


def test_a_sentence_becomes_a_row() -> None:
    (row,) = rows_of(_publication(), [_answer()])

    assert row["person_key"] == "петров иван"
    assert (row["region"], row["kind"], row["months"]) == ("Свердловская область", "colony", 72)
    assert row["sentenced_on"] == "2026-09-24"


def test_what_is_no_sentence_is_no_row() -> None:
    requested = _answer(
        person="Олег Сидоров",
        surname="Сидоров",
        first_name="Олег",
        stage="requested",
        months=96,
        quote="прокурор запросил восемь лет",
    )

    assert rows_of(_publication(), [requested]) == []
    assert rows_of(_publication(), [_answer(stage="foreign")]) == []


def test_a_quote_the_article_does_not_hold_is_no_row() -> None:
    assert rows_of(_publication(), [_answer(quote="приговорил к десяти годам")]) == []
    assert rows_of(_publication(), [_answer(quote="")]) == []
    # The article's own words, however its spaces run.
    assert rows_of(_publication(), [_answer(quote="приговорил  Ивана\nПетрова к шести годам")])


def test_what_the_model_wrote_loosely_is_made_strict() -> None:
    (row,) = rows_of(
        _publication(),
        [
            _answer(
                region="Екатеринбург",
                months=7200,
                fine_rub=-5,
                date="сентябрь 2026",
                surname="Петров",
                first_name="И.",
            )
        ],
    )

    # A city is no subject, a term of 600 years is a misreading, an initial is no name.
    assert (row["region"], row["months"], row["fine_rub"]) == ("", 0, 0)
    assert row["sentenced_on"] == ""
    assert row["person_key"] is None


class FakeReader:
    model = "fake-model"

    def __init__(self, answers: dict[int, list[SentenceAnswer]] | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[int] = []
        self.fail: set[int] = set()

    def read(self, publication: Publication) -> list[SentenceAnswer]:
        self.asked.append(publication.article_id)
        if publication.article_id in self.fail:
            raise SentenceReaderError("no answer")
        return self.answers.get(publication.article_id, [])


def _seed(session_factory: sessionmaker[Session]) -> dict[str, int]:
    """Three articles: a sentence, a fine, and an arrest that is neither."""
    ids: dict[str, int] = {}
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        for name, event in (("sentence", "sentence"), ("fine", "fine"), ("arrest", "arrest")):
            ids[name], run = seed.article(source, external_id=name, title=name, text=TEXT)
            seed.event(run, TEXT[:10], event_type=event, event_date=None, links=[])
        session.commit()
    return ids


def _rows(session_factory: sessionmaker[Session]) -> list[ArticleSentenceRecord]:
    with session_factory() as session:
        return list(
            session.scalars(select(ArticleSentenceRecord).order_by(ArticleSentenceRecord.id))
        )


def test_articles_with_a_sentence_are_read_once(session_factory: sessionmaker[Session]) -> None:
    ids = _seed(session_factory)
    reader = FakeReader({ids["sentence"]: [_answer()]})

    result = SentenceFinder(session_factory, reader=reader).run()

    # The arrest is not read: nothing says it holds a sentence.
    assert sorted(reader.asked) == sorted([ids["sentence"], ids["fine"]])
    assert (result.asked_now, result.sentences, result.cached) == (2, 1, 0)
    assert [(row.article_id, row.person) for row in _rows(session_factory)] == [
        (ids["sentence"], "Иван Петров")
    ]

    again = FakeReader()
    result = SentenceFinder(session_factory, reader=again).run()

    # Also the article with no sentence in it: read once is enough.
    assert again.asked == []
    assert (result.asked_now, result.cached) == (0, 2)


def test_a_failed_article_is_asked_by_the_next_run(session_factory: sessionmaker[Session]) -> None:
    ids = _seed(session_factory)
    reader = FakeReader({ids["sentence"]: [_answer()]})
    reader.fail = {ids["sentence"]}

    result = SentenceFinder(session_factory, reader=reader).run()

    assert (result.asked_now, result.failures, result.sentences) == (1, 1, 0)
    with session_factory() as session:
        read = set(session.scalars(select(ArticleSentenceReadingRecord.article_id)))
    assert read == {ids["fine"]}

    reader.fail = set()
    reader.asked = []
    SentenceFinder(session_factory, reader=reader).run()

    assert reader.asked == [ids["sentence"]]
    assert len(_rows(session_factory)) == 1


def test_without_a_model_nothing_is_read(session_factory: sessionmaker[Session]) -> None:
    _seed(session_factory)

    result = SentenceFinder(session_factory, reader=None).run()

    assert (result.asked_now, result.sentences) == (0, 0)
    assert _rows(session_factory) == []


def test_what_a_person_hid_stays_hidden_when_read_again(
    session_factory: sessionmaker[Session],
) -> None:
    from entities import sentences

    ids = _seed(session_factory)
    other = _answer(
        person="житель Тюмени", surname="", first_name="", quote="к шести годам колонии"
    )
    answers = {ids["sentence"]: [_answer(), other]}
    SentenceFinder(session_factory, reader=FakeReader(answers)).run()
    with session_factory.begin() as session:
        session.scalars(
            select(ArticleSentenceRecord).where(ArticleSentenceRecord.person == "Иван Петров")
        ).one().hidden = True

    # A new prompt reads everything again.
    old = sentences.PROMPT_VERSION
    sentences.PROMPT_VERSION = "sentences-next"
    try:
        reader = FakeReader(answers)
        SentenceFinder(session_factory, reader=reader).run()
    finally:
        sentences.PROMPT_VERSION = old

    assert ids["sentence"] in reader.asked
    assert {row.person: row.hidden for row in _rows(session_factory)} == {
        "Иван Петров": True,
        "житель Тюмени": False,
    }
