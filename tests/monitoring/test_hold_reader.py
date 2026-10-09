"""A model reading the articles the junk screen held, on PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import ExtractedEventRecord, JunkScreenHoldRecord, ParsedArticleRecord
from entities.llm import ModelError, Spend
from monitoring.hold_reader import (
    EXTRACTOR_NAME,
    RELEASED,
    HoldAnswer,
    HoldItem,
    hold_reader_from_env,
    read_holds,
    release,
)
from monitoring.junk_holds import mark_junk
from monitoring.junk_purge import JunkPurge
from monitoring.junk_screen import HELD

TEXTS = {
    "donation": "Наталью Голощукову признали виновной\nСуд назначил ей срок за донаты ФБК.",
    "bribe": "Бывший инспектор отправится в колонию за взятки.",
    "fine": "Суд оштрафовал жительницу Анапы по КоАП.",
}


class Reader:
    """A case where the text tells of a sentence; political unless it is a bribe."""

    name = "fake-reader"

    def __init__(self, *, fail: bool = False, silent: Sequence[str] = ()) -> None:
        self.fail = fail
        self.silent = silent
        self.asked: list[HoldItem] = []

    def read(self, items: Sequence[HoldItem]) -> dict[int, HoldAnswer]:
        self.asked += items
        if self.fail:
            raise ModelError("provider down")
        return {
            item.id: HoldAnswer(
                id=item.id,
                is_case="КоАП" not in item.text,
                motive="criminal" if "взятки" in item.text else "political",
                event="sentence",
                explanation=f"прочитано: {item.text[:20]}",
            )
            for item in items
            if not any(word in item.text for word in self.silent)
        }


def _seed(session_factory: sessionmaker[Session]) -> dict[str, int]:
    """Three held articles, each extracted with no criminal-case event."""
    ids = {}
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        for name, text_ in TEXTS.items():
            ids[name], _ = seed.article(source, external_id=name, title=name, text=text_)
            session.add(
                JunkScreenHoldRecord(
                    article_id=ids[name], status=HELD, score=0.9, cutoff=0.5, screen="s", reason="r"
                )
            )
        session.commit()
    return ids


def _said(name: str) -> str:
    """What `Reader` says of an article: it is given the title and the text as one line."""
    return f"прочитано: {' '.join(f'{name} {TEXTS[name]}'.split())[:20]}"


def _holds(session_factory: sessionmaker[Session]) -> dict[int, tuple[object, ...]]:
    with session_factory() as session:
        return {
            hold.article_id: (hold.status, hold.reader_verdict, hold.reader_event, hold.note)
            for hold in session.scalars(select(JunkScreenHoldRecord))
        }


def _events(session_factory: sessionmaker[Session], article_id: int) -> list[ExtractedEventRecord]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(ExtractedEventRecord)
                .where(
                    ExtractedEventRecord.extraction_run_id.in_(
                        text(
                            "SELECT id FROM article_extraction_runs WHERE article_id = :article"
                        ).bindparams(article=article_id)
                    )
                )
                .order_by(ExtractedEventRecord.id)
            )
        )


def test_a_case_goes_on_into_the_work_and_the_rest_waits_with_the_reason(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    reader = Reader()

    result = read_holds(session_factory, reader)
    again = Reader()
    second = read_holds(session_factory, again)

    assert _holds(session_factory) == {
        ids["donation"]: (RELEASED, "case", "sentence", _said("donation")),
        # A common crime is a case the later steps would only drop; no case is no case.
        ids["bribe"]: (HELD, "junk", None, _said("bribe")),
        ids["fine"]: (HELD, "junk", None, _said("fine")),
    }
    [event] = _events(session_factory, ids["donation"])
    # The event the rules missed, as the model's own, over the article's first line.
    assert (event.event_type, event.extractor_name) == ("sentence", EXTRACTOR_NAME)
    assert (event.start_offset, event.end_offset) == (
        0,
        len("Наталью Голощукову признали виновной"),
    )
    assert event.attributes["explanation"] == _said("donation")
    assert _events(session_factory, ids["bribe"]) == []
    assert (result.released, result.model_junk, result.failures) == (1, 2, 0)
    # Read once: nobody is asked about what was read.
    assert again.asked == [] and (second.released, second.model_junk) == (0, 0)


def test_what_the_model_did_not_answer_is_asked_again(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)

    failed = read_holds(session_factory, Reader(fail=True))
    partly = read_holds(session_factory, Reader(silent=["донаты"]))
    retry = Reader()
    read_holds(session_factory, retry)

    assert (failed.failures, failed.released, failed.model_junk) == (3, 0, 0)
    assert (partly.failures, partly.model_junk) == (1, 2)
    assert [item.id for item in retry.asked] == [ids["donation"]]
    assert _holds(session_factory)[ids["donation"]][0] == RELEASED


def test_a_purge_deletes_neither_the_released_nor_the_model_s_junk(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    read_holds(session_factory, Reader())

    JunkPurge(session_factory).run()

    # Nothing is deleted on a model's word: its junk waits for a person.
    with session_factory() as session:
        assert set(session.scalars(select(ParsedArticleRecord.id))) == set(ids.values())


def test_a_released_article_extracted_anew_gets_the_event_again_unasked(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    read_holds(session_factory, Reader())
    with session_factory.begin() as session:
        # The rules changed: a new extraction, and no event in it either.
        session.execute(
            text(
                "INSERT INTO article_extraction_runs (article_id, article_content_hash, "
                "extractor_name, extractor_version, normalizer_version, status, started_at) "
                "SELECT article_id, article_content_hash, extractor_name, "
                "extractor_version || '+', normalizer_version, status, now() "
                "FROM article_extraction_runs WHERE article_id = :article"
            ),
            {"article": ids["donation"]},
        )

    # Before the next reading the purge must leave it: its event is with the old run.
    JunkPurge(session_factory).run()
    reader = Reader()
    result = read_holds(session_factory, reader)
    read_holds(session_factory, reader)

    events = _events(session_factory, ids["donation"])
    assert reader.asked == [] and result.restored == 1
    assert [event.extractor_name for event in events] == [EXTRACTOR_NAME, EXTRACTOR_NAME]
    assert len({event.extraction_run_id for event in events}) == 2


def test_without_a_model_the_held_wait_for_a_person(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)

    result = read_holds(session_factory, hold_reader_from_env({}))

    assert (result.released, result.model_junk) == (0, 0)
    assert {status for status, *_ in _holds(session_factory).values()} == {HELD}
    assert len(ids) == 3


class MeanwhileJunk(Reader):
    """A person calls the donation junk while the model is reading."""

    def __init__(self, session_factory: sessionmaker[Session], article: int) -> None:
        super().__init__()
        self._session_factory, self._article = session_factory, article

    def read(self, items: Sequence[HoldItem]) -> dict[int, HoldAnswer]:
        with self._session_factory.begin() as session:
            assert mark_junk(session, self._article)
        return super().read(items)


def test_a_person_s_word_said_while_the_model_read_stands(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)

    result = read_holds(session_factory, MeanwhileJunk(session_factory, ids["donation"]))
    JunkPurge(session_factory).run()

    # No event after «Мусор»: it would keep the article from every purge, unseen.
    assert _events(session_factory, ids["donation"]) == []
    assert _holds(session_factory).get(ids["donation"]) is None
    with session_factory() as session:
        assert set(session.scalars(select(ParsedArticleRecord.id))) == {ids["bribe"], ids["fine"]}
    assert (result.released, result.model_junk, result.failures) == (0, 2, 0)


def test_a_release_with_no_extraction_to_write_to_is_left_unread(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE article_extraction_runs SET status = 'failed' WHERE article_id = :id"),
            {"id": ids["donation"]},
        )

    result = read_holds(session_factory, Reader())

    # Released without its event, an article would be in no list and no step.
    assert _holds(session_factory)[ids["donation"]] == (HELD, None, None, "")
    assert (result.released, result.failures) == (0, 1)


class Spending(Reader):
    def __init__(self) -> None:
        super().__init__()
        self.spend = Spend(budget_usd=0.0)


def test_what_the_budget_left_unasked_is_no_failure(
    session_factory: sessionmaker[Session],
) -> None:
    _seed(session_factory)
    reader = Spending()

    result = read_holds(session_factory, reader)

    assert reader.asked == []
    assert (result.unasked, result.failures, result.released, result.model_junk) == (3, 0, 0, 0)
    assert {status for status, *_ in _holds(session_factory).values()} == {HELD}


def test_a_person_s_release_needs_an_extraction_to_write_to(
    session_factory: sessionmaker[Session],
) -> None:
    ids = _seed(session_factory)
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE article_extraction_runs SET status = 'failed' WHERE article_id = :id"),
            {"id": ids["fine"]},
        )

    # Committed whatever it answers: a refusal must leave nothing behind.
    with session_factory.begin() as session:
        refused = release(session, ids["fine"])
    with session_factory.begin() as session:
        taken = release(session, ids["bribe"])

    holds = _holds(session_factory)
    assert (refused, taken) == (False, True)
    assert holds[ids["fine"]][0] == HELD and holds[ids["bribe"]][0] == RELEASED
    assert [event.event_type for event in _events(session_factory, ids["bribe"])] == ["case_opened"]
