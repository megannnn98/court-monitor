"""The clusters as the API reads them: a list, one cluster with its members, and the
clusters one publication is in.

One publication may tell two steps (a charge and the opening of a case): it is then in
two clusters, and both are returned.
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from case_resolution.world import World, at
from cases.clusters import read
from cases.clusters.service import resolve

IVAN = "Иван Петров"
OTHER = "Пётр Сидоров"
MOSCOW = ("location", "Москва")
FSB = ("authority", "ФСБ")
COURT = ("court", "Мосгорсуд")
LAW = ("205.2/3",)


def test_the_list_and_the_detail_of_a_cluster(
    session_factory: sessionmaker[Session],
) -> None:
    world = World(session_factory)
    words = "Ивана Петрова задержали в Москве по делу о терроризме и поместили в изолятор"
    first = world.report(IVAN, sentence=words, kind="detention", day=at(2024, 3, 1), charges=LAW)
    second = world.report(IVAN, sentence=words, kind="detention", day=at(2024, 3, 1), charges=LAW)
    resolve(session_factory)

    with session_factory() as session:
        total, items = read.list_clusters(session, limit=10)
        assert total == 1
        summary = items[0]
        assert (summary.members, summary.articles) == (2, 2)
        assert summary.status == "confirmed"
        assert summary.event_type == "detention"

        detail = read.cluster_detail(session, summary.id)
        assert detail is not None
        assert [member.role for member in detail.members] == ["core", "member"]
        assert all(member.article_id in (first, second) for member in detail.members)
        assert detail.members[1].reasons  # the reasons the second one joined
        assert {source.article_id for source in detail.sources} == {first, second}

        # The publication is in one cluster.
        assert len(read.clusters_of_article(session, first)) == 1


def test_a_publication_that_tells_two_steps_is_in_two_clusters(
    session_factory: sessionmaker[Session],
) -> None:
    """One article, two events of different types: two clusters, and the article is in
    both."""
    world = World(session_factory)
    first = "Ивана Петрова задержали в Москве"
    second = "Петру Сидорову вынесли приговор"
    article = world.article(
        f"Заметка. {first}. {second}. Иван Петров Пётр Сидоров Москва ФСБ Мосгорсуд"
    )
    world.event(
        article, first, IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB,)
    )
    world.event(
        article, second, OTHER, kind="sentence", day=at(2024, 3, 2), charges=LAW, orgs=(COURT,)
    )
    resolve(session_factory)

    with session_factory() as session:
        found = read.clusters_of_article(session, article.id)
        assert len(found) == 2
        assert sorted(cluster.event_type for cluster in found) == ["detention", "sentence"]
