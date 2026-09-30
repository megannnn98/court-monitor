"""The articles list: «статьи, которые нас интересуют».

A list of numbers, and the whole difficulty is that the same charge is written several
ways — «ст. 159», «159 УК РФ», «Статья 159. Уголовный кодекс» — while the pipeline
compares digits. So these tests are mostly about the fold, and about what must not be
written at all: a row with no number in it is a row nobody could ever match.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from airtable.articles import article_key, sync_articles
from airtable.client import AirtableRecord
from db.orm_models import CriminalArticleRecord


def _texts(session_factory: sessionmaker[Session]) -> list[str]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(CriminalArticleRecord.article_text).order_by(
                    CriminalArticleRecord.article_text
                )
            )
        )


@pytest.mark.parametrize(
    ("written", "key"),
    [
        ("159", "159"),
        ("ст. 159", "159"),
        ("159 УК РФ", "159"),
        ("Статья 159", "159"),
        ("Статья 159 Уголовного кодекса РФ", "159"),
        # A part is a different charge from the article it belongs to.
        ("ст. 159 ч. 3", "159.3"),
        ("159.3 УК РФ", "159.3"),
        # No digits: nothing to match with.
        ("статья", ""),
        ("", ""),
    ],
)
def test_the_key_is_the_digits_of_the_article(written: str, key: str) -> None:
    assert article_key(written) == key


def test_articles_are_written_once_each(
    session_factory: sessionmaker[Session],
) -> None:
    records = [
        AirtableRecord("recA1", {"Полная статья": "159 УК РФ"}),
        AirtableRecord("recA2", {"Полная статья": "ст. 105"}),
    ]

    with session_factory.begin() as session:
        result = sync_articles(session, records)

    assert (result.created, result.updated, result.unchanged, result.errors) == (2, 0, 0, 0)
    assert _texts(session_factory) == ["159 УК РФ", "ст. 105"]


def test_a_second_run_changes_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    """The button is pressed whenever the operator gets round to it; a list that is
    untouched must report itself as untouched, or every press looks like an edit."""
    records = [AirtableRecord("recA1", {"Полная статья": "159 УК РФ"})]
    with session_factory.begin() as session:
        sync_articles(session, records)

    with session_factory.begin() as session:
        again = sync_articles(session, records)

    assert (again.created, again.updated, again.unchanged) == (0, 0, 1)


def test_a_renamed_article_is_an_update_not_a_second_row(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        sync_articles(session, [AirtableRecord("recA1", {"Полная статья": "159"})])

    with session_factory.begin() as session:
        result = sync_articles(session, [AirtableRecord("recA1", {"Полная статья": "159 УК РФ"})])

    assert result.updated == 1 and result.created == 0
    assert _texts(session_factory) == ["159 УК РФ"]


def test_a_row_with_no_number_is_refused_and_nothing_is_written(
    session_factory: sessionmaker[Session],
) -> None:
    """It could never be matched against a charge, so writing it would be a row nobody
    could use — and the count of them is the operator's signal that the list is wrong."""
    records = [
        AirtableRecord("recA1", {"Полная статья": "159"}),
        AirtableRecord("recA2", {"Полная статья": "все статьи"}),
    ]

    with session_factory.begin() as session:
        result = sync_articles(session, records)

    assert result.errors == 1 and result.created == 1
    assert _texts(session_factory) == ["159"]


def test_a_row_with_no_article_at_all_is_refused(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        result = sync_articles(session, [AirtableRecord("recA1", {"Заметка": "проверить"})])

    assert result.errors == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CriminalArticleRecord)) == 0


def test_a_part_is_its_own_article(
    session_factory: sessionmaker[Session],
) -> None:
    """159 and 159.3 are different charges. Folding them together would report matches
    that do not exist — the worst kind of mistake for a list meant to say what to watch
    for. The key is dotted because that is how the pipeline writes a charge."""
    records = [
        AirtableRecord("recA1", {"Полная статья": "ст. 159"}),
        AirtableRecord("recA2", {"Полная статья": "ст. 159 ч. 3"}),
    ]

    with session_factory.begin() as session:
        result = sync_articles(session, records)

    assert result.created == 2
    with session_factory() as session:
        keys = set(session.scalars(select(CriminalArticleRecord.article_key)))
    assert keys == {"159", "159.3"}


def test_two_codes_with_the_same_number_share_a_key() -> None:
    """«ст. 30 УК РФ ч. 1» and «ст. 30 УК ЛНР ч. 1» are different charges, and the key
    does not tell them apart. That is a limit of the charges table, which carries no
    code either — recorded here so whoever matches on this list meets it knowingly."""
    assert article_key("ст. 30 УК РФ ч. 1") == article_key("ст. 30 УК ЛНР ч. 1") == "30.1"
