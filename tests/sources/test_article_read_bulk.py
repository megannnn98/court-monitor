"""The PostgreSQL array-based bulk contract performs no query per article."""

from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from ingestion.read import stored_articles


def test_bulk_articles_use_one_query_and_omit_unknown_ids(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example")
        first, _ = seed.article(source, external_id="one", title="One", text="first text")
        second, _ = seed.article(source, external_id="two", title="Two", text="second text")

    with session_factory() as session:
        queries: list[str] = []

        def count(*args: object) -> None:
            queries.append("query")

        bind = session.get_bind()
        event.listen(bind, "before_cursor_execute", count)
        try:
            found = stored_articles(session, [first, second, 999_999])
            assert len(queries) == 1
            assert set(found) == {first, second}
            assert (found[first].text, found[second].title) == ("first text", "Two")
            queries.clear()
            assert stored_articles(session, []) == {}
            assert queries == []
        finally:
            event.remove(bind, "before_cursor_execute", count)
