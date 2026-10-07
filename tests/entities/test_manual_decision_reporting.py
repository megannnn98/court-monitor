"""What a person's own decision looks like back to them, and whether it is counted.

Two defects found by review rather than by a failing test, so they are written down here
as behaviour that must hold. Both fail on the current code — that is the point of the
file; it is not a passing suite.

- A manual verdict is shown as «модель: решено оператором вручную» on «Результат»: the
  person who decided it reads their own decision back as the model's answer.
- The step 4/5 counters do not account for a manual decision at all: a manually decided
  figurant appears in no category, so the run summary undercounts.

    TEST_DATABASE_URL=... uv run pytest tests/entities/test_manual_decision_reporting.py -q
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupChargeRecord,
    EntityGroupPoliticsRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityPoliticsDecisionRecord,
    EntityRoleDecisionRecord,
)
from entities.politics import POLITICAL, PoliticsFinder
from entities.roles import FIGURANT, FigurantFinder
from web.app import app
from web.dependencies import get_db

MANUAL_REASON = "решено оператором вручную"


@contextmanager
def _client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _seed(session: Session, name: str) -> EntityGroupRecord:
    """One entity whose role and political verdict were decided by a person, not a model."""
    entity = EntityGroupRecord(
        key=name,
        name=" ".join(part.capitalize() for part in name.split()),
        variants=[],
        mention_count=2,
        article_count=2,
        event_types={},
        regions=[],
    )
    session.add(entity)
    session.flush()
    session.add(EntityRoleDecisionRecord(key=name, role=FIGURANT))
    session.add(
        EntityGroupRoleRecord(
            group_id=entity.id,
            role=FIGURANT,
            kind=None,
            method="manual",
            reason=MANUAL_REASON,
            quote="",
        )
    )
    session.add(EntityPoliticsDecisionRecord(key=name, verdict=POLITICAL))
    session.add(
        EntityGroupPoliticsRecord(
            group_id=entity.id,
            verdict=POLITICAL,
            method="manual",
            reason=MANUAL_REASON,
            quote="",
        )
    )
    return entity


def test_a_manual_verdict_is_not_attributed_to_the_model(
    session_factory: sessionmaker[Session],
) -> None:
    """`web.ui.political._basis` labels every method but «article» as «модель»."""
    with session_factory.begin() as session:
        _seed(session, "мария резова")

    with _client(session_factory) as client:
        page = client.get("/ui/political").text

    assert f"модель: {MANUAL_REASON}" not in page, (
        "a decision no model made is attributed to the model on /ui/political"
    )
    assert "решено вручную" in page, "the label must say whose word it is"
    # The reason is the same words as the label: printing it after a colon reads as
    # «решено вручную: решено оператором вручную».
    assert f"решено вручную: {MANUAL_REASON}" not in page, (
        "the label and the reason say the same thing twice"
    )


def test_a_manual_figurant_is_counted_in_exactly_one_category(
    session_factory: sessionmaker[Session],
) -> None:
    """A separate field, not a fold into the model's count: a sum alone would pass if the
    manual case were simply added to `figurant_model` and the label kept saying «модель»."""
    with session_factory.begin() as session:
        _seed(session, "мария резова")

    result = FigurantFinder(session_factory, classifier=None).run()

    assert result.figurant_manual == 1, "the person's decision has its own counter"
    assert result.figurant_model == 0, "and does not borrow the model's"
    assert result.figurant_rules == 0
    accounted = (
        result.figurant_rules
        + result.figurant_model
        + result.figurant_manual
        + result.possible
        + result.mentioned
        + result.unclear
        + result.officials
    )
    assert accounted == result.entities, f"{result.entities} entities, {accounted} counted"


def test_a_manual_political_verdict_is_counted_in_exactly_one_category(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        _seed(session, "мария резова")

    result = PoliticsFinder(session_factory, classifier=None).run()

    assert result.political_manual == 1, "the person's decision has its own counter"
    assert result.political_model == 0
    accounted = (
        result.political_rules
        + result.political_memorial
        + result.political_model
        + result.political_manual
        + result.criminal
        + result.unclear
    )
    assert accounted == result.figurants, f"{result.figurants} figurants, {accounted} counted"


def _seed_political_charge(session: Session, entity: EntityGroupRecord, article: str) -> None:
    """A charge from the political list, with the event and publication it points at."""
    from db.orm_models import (
        ArticleExtractionRunRecord,
        ExtractedEventRecord,
        ParsedArticleRecord,
        Source,
        SourceDocument,
    )

    source = Source(name="Источник", base_url="https://example.test")
    session.add(source)
    session.flush()
    document = SourceDocument(
        source_id=source.id,
        external_id="1",
        canonical_url="https://example.test/1",
        fetched_at=datetime(2026, 9, 20, tzinfo=UTC),
        content_type="text/html",
        raw_content=b"<html></html>",
    )
    session.add(document)
    session.flush()
    article_row = ParsedArticleRecord(
        document_id=document.id, title="Публикация", published_at=None, text="Текст"
    )
    session.add(article_row)
    session.flush()
    run = ArticleExtractionRunRecord(
        article_id=article_row.id,
        article_content_hash="h",
        extractor_name="test",
        extractor_version="1",
        normalizer_version="1",
        status="succeeded",
        started_at=datetime(2026, 9, 20, tzinfo=UTC),
        finished_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    session.add(run)
    session.flush()
    event = ExtractedEventRecord(
        extraction_run_id=run.id,
        event_type="charge",
        event_date=None,
        start_offset=0,
        end_offset=10,
        confidence=0.9,
        attributes={},
        extractor_name="test",
        extractor_version="1",
    )
    session.add(event)
    session.flush()
    session.add(
        EntityGroupChargeRecord(
            group_id=entity.id,
            event_id=event.id,
            publication_id=article_row.id,
            article=article,
            part="1",
            clause=None,
            event_type="charge",
            other_targets=0,
            quote="«…»",
        )
    )


def test_a_manual_criminal_verdict_over_a_political_charge_is_counted_once(
    session_factory: sessionmaker[Session],
) -> None:
    """The double count the review named: `political_rules` was measured before the
    override, so an article with a political charge that a person calls an ordinary case
    landed in `political_rules` and `criminal` at once."""
    from entities.politics import CRIMINAL, decide_politics
    from persecution.classifier import POLITICAL_ARTICLES

    political_article = min(POLITICAL_ARTICLES)
    with session_factory.begin() as session:
        entity = _seed(session, "мария резова")
        _seed_political_charge(session, entity, political_article)
        decide_politics(session, entity, CRIMINAL)

    result = PoliticsFinder(session_factory, classifier=None).run()

    assert result.criminal_manual == 1
    assert result.political_rules == 0, (
        f"a person called it an ordinary case; the rules' political charge ({political_article})"
        " must not keep counting it as political"
    )
    assert result.political_manual == 0
    accounted = (
        result.political_rules
        + result.political_memorial
        + result.political_model
        + result.political_manual
        + result.criminal
        + result.unclear
    )
    assert accounted == result.figurants, f"{result.figurants} figurants, {accounted} counted"


@pytest.mark.parametrize("table", ["entity_role_decisions", "entity_politics_decisions"])
def test_a_decision_table_is_truncated_only_in_a_disposable_database(table: str) -> None:
    """The disposable list is the right home for these tables, and it is guarded.

    `truncate_disposable_tables` refuses any database not named `*_test` / `*_eval`, so
    the decision rows survive in production. Reviewed and kept deliberately: the risk is
    the opposite of what the table name suggests.
    """
    import sqlalchemy as sa

    from db.maintenance import DISPOSABLE_TABLES, require_disposable_database

    assert table in DISPOSABLE_TABLES, f"{table} would leak between tests otherwise"
    with pytest.raises(Exception):  # noqa: B017 - the refusal is the assertion
        require_disposable_database(sa.create_engine("postgresql://x/production"))


def test_the_exact_key_wins_over_a_surname_stem_match(
    session_factory: sessionmaker[Session],
) -> None:
    """Two records resolve to one entity through `KeyIndex`; the current key must win.

    Inserted in both orders, because the read is `select(...)` with no ORDER BY and a
    small PostgreSQL table comes back in insertion order — whichever record is read last
    is the one that wins, and today that is an accident of insert order, not a rule.
    """
    from entities.roles import role_decisions

    stale, current = "иван петров", "пётр иванов"
    for order in ((stale, current), (current, stale)):
        with session_factory.begin() as session:
            session.add(EntityRoleDecisionRecord(key=stale, role="mentioned"))
            session.add(EntityRoleDecisionRecord(key=current, role=FIGURANT))
        with session_factory() as session:
            found = role_decisions(session, {1: current})
        assert found.get(1) == FIGURANT, (
            f"inserted {order}: the record whose key is today's own must win over the "
            f"surname-stem match, got {found!r}"
        )
        # The fixture truncates per test, not per iteration: clear the table ourselves.
        with session_factory.begin() as session:
            session.execute(text("DELETE FROM entity_role_decisions"))


def test_a_decision_does_not_silently_revert_after_a_marked_official(
    session_factory: sessionmaker[Session],
) -> None:
    """The immediate write and the next rebuild must agree.

    The rebuild honours an official mark (`FigurantFinder.run`), so `decide_role` has to
    honour it too. It used to write «figurant» and the next run quietly changed it to
    «mentioned»: the operator saw a decision that the pipeline took back.
    """
    from entities.officials import OFFICIAL_KINDS, mark_official
    from entities.roles import MENTIONED, decide_role

    with session_factory.begin() as session:
        entity = _seed(session, "мария резова")
        mark_official(session, entity, True)
        decide_role(session, entity, FIGURANT)
        session.flush()
        now = session.get(EntityGroupRoleRecord, entity.id)

    assert now is not None
    at_once = (now.role, now.kind, now.method, now.reason)

    FigurantFinder(session_factory, classifier=None).run()

    with session_factory() as session:
        after = session.get(EntityGroupRoleRecord, entity.id)
        assert after is not None

    rebuilt = (after.role, after.kind, after.method, after.reason)
    assert at_once == rebuilt, (
        f"decided «{at_once}», the next run made it «{rebuilt}»: an official mark is the "
        "more specific word and wins at once too — an empty kind would drop the entity "
        "off «Должностные лица» until step 4 runs"
    )
    assert after.role == MENTIONED
    assert after.kind in OFFICIAL_KINDS, "an official with no kind vanishes from the officials page"


def test_dropping_a_figurant_to_mentioned_takes_it_off_the_political_list(
    session_factory: sessionmaker[Session],
) -> None:
    """`mark_official` deletes the verdict when an entity leaves the figurants; a manual
    role change must do the same, or the person stays on «Результат» until step 5."""
    from entities.roles import MENTIONED, decide_role

    with session_factory.begin() as session:
        entity = _seed(session, "мария резова")
        decide_role(session, entity, MENTIONED)

    with session_factory() as session:
        left = session.get(EntityGroupPoliticsRecord, entity.id)

    assert left is None, (
        "the verdict of a person who is no longer a figurant is still on «Результат»"
    )
