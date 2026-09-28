"""Which articles substantively describe a person's own case, cached by article id, on
PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from support.db_fixtures import DatabaseSeeder

from db.orm_models import (
    ArticleDigestAnswerRecord,
    EntityGroupRecord,
    EntityGroupRoleRecord,
    EntityMentionRecord,
)
from entities.collector import EntityCollector
from entities.digest import DigestAnswer, DigestFinder, figurant_titles


def _person(session: Session, seed: DatabaseSeeder, run: int, surface: str, name: str) -> int:
    first, last = name.split()
    mention_id = seed.mention(run, surface, person_id=None)
    session.get_one(EntityMentionRecord, mention_id).normalized_data = {
        "first_name": first,
        "last_name": last,
        "patronymic": None,
    }
    return mention_id


def _seed(session_factory: sessionmaker[Session]) -> dict[str, int]:
    """Петров: a figurant, in a roundup post. Сирош: mentioned only, in his own article."""
    article_ids: dict[str, int] = {}
    with session_factory() as session:
        seed = DatabaseSeeder(session)
        source = seed.source("news", "https://news.example.test")
        digest_text = "Главное за день: Ивана Петрова задержали по делу о фейках."
        article_id, run = seed.article(
            source, external_id="digest", title="Главное за день", text=digest_text
        )
        article_ids["digest"] = article_id
        _person(session, seed, run, "Ивана Петрова", "Иван Петров")
        seed.event(run, digest_text[:10], event_type="arrest", event_date=None, links=[])

        sirosh_text = "Адвокат Фёдор Сирош обжалует приговор."
        article_id, run = seed.article(
            source, external_id="sirosh", title="Обжалование", text=sirosh_text
        )
        article_ids["sirosh"] = article_id
        _person(session, seed, run, "Фёдор Сирош", "Фёдор Сирош")
        seed.event(run, sirosh_text[:10], event_type="sentence", event_date=None, links=[])
        session.commit()
    EntityCollector(session_factory).run()
    with session_factory.begin() as session:
        for group_id, name in session.execute(select(EntityGroupRecord.id, EntityGroupRecord.name)):
            role = "mentioned" if name == "Фёдор Сирош" else "figurant"
            session.add(
                EntityGroupRoleRecord(
                    group_id=group_id, role=role, method="model", reason="", quote=""
                )
            )
    return article_ids


class FakeDigestClassifier:
    model = "fake-model"

    def __init__(self, relevance: dict[str, bool]) -> None:
        self.relevance = relevance
        self.asked: list[str] = []

    def classify(self, titles: Mapping[int, str]) -> dict[int, DigestAnswer]:
        self.asked += titles.values()
        return {
            article_id: DigestAnswer(id=article_id, relevant=self.relevance[title], explanation="—")
            for article_id, title in titles.items()
        }


def test_only_figurants_sources_are_offered(session_factory: sessionmaker[Session]) -> None:
    article_ids = _seed(session_factory)

    with session_factory() as session:
        titles = figurant_titles(session)

    # Сирош is «mentioned», not a figurant: his article is not a source to classify.
    assert titles == {article_ids["digest"]: "Главное за день"}


def test_relevance_is_classified_and_cached(session_factory: sessionmaker[Session]) -> None:
    article_ids = _seed(session_factory)
    titles = {article_ids["digest"]: "Главное за день", article_ids["sirosh"]: "Обжалование"}
    # A roundup post is not about Петров's own case; Сирош's article is about his.
    relevance = {"Главное за день": False, "Обжалование": True}
    classifier = FakeDigestClassifier(relevance)

    result = DigestFinder(session_factory, classifier=classifier).run(titles)

    assert sorted(classifier.asked) == ["Главное за день", "Обжалование"]
    assert (result.asked_now, result.cached, result.failures) == (2, 0, 0)
    with session_factory() as session:
        rows = {
            row.article_id: row.relevant
            for row in session.scalars(select(ArticleDigestAnswerRecord))
        }
    assert rows == {article_ids["digest"]: False, article_ids["sirosh"]: True}

    again = FakeDigestClassifier(relevance)
    result = DigestFinder(session_factory, classifier=again).run(titles)

    assert again.asked == []
    assert (result.asked_now, result.cached) == (0, 2)
