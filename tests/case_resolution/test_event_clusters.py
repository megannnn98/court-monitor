"""Event clusters on PostgreSQL: the scenarios of the task, over a world built the way
the extraction and the entity rebuild leave them.

A cluster is one event told by several publications. The rules are conservative: a
publication joins a cluster only when it matches the cluster's anchor directly, and two
clusters that match are an ambiguity, never a merge.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from case_resolution.world import World, at
from cases.clusters.service import ClusterReport, resolve
from cases.storage.models import EventClusterMemberRecord, EventClusterRecord

IVAN = "Иван Петров"
PETR = "Пётр Сидоров"
MOSCOW = ("location", "Москва")
FSB = ("authority", "ФСБ")
COURT = ("court", "Мосгорсуд")
LAW = ("205.2/3",)
DRUGS = ("228/1",)


def run(factory: sessionmaker[Session], **kwargs: int | bool) -> ClusterReport:
    return resolve(factory, **kwargs)  # type: ignore[arg-type]


def clusters(factory: sessionmaker[Session]) -> list[list[int]]:
    """The clusters as sets of event ids, in a stable order."""
    with factory() as session:
        members = session.execute(
            select(EventClusterMemberRecord.cluster_id, EventClusterMemberRecord.event_id).where(
                EventClusterMemberRecord.active.is_(True),
                EventClusterMemberRecord.cluster_id.is_not(None),
            )
        ).all()
    grouped: dict[UUID, list[int]] = {}
    for cluster_id, event_id in members:
        grouped.setdefault(cluster_id, []).append(event_id)
    return sorted([sorted(ids) for ids in grouped.values()], key=lambda ids: ids[0])


def test_several_publications_of_one_detention_are_one_cluster(
    session_factory: sessionmaker[Session],
) -> None:
    """Scenario 1: three publications of one detention — one cluster."""
    world = World(session_factory)
    words = "Ивана Петрова задержали в Москве по делу о терроризме и поместили в изолятор"
    ids = [world.report(IVAN, sentence=words, charges=LAW, orgs=(MOSCOW,)) for _ in range(3)]

    report = run(session_factory)

    assert (report.opened, report.joined) == (1, 2)
    (only,) = clusters(session_factory)
    assert sorted(only) == sorted(ids)


def test_two_detentions_of_one_person_in_different_years_are_two_clusters(
    session_factory: sessionmaker[Session],
) -> None:
    """Scenario 2: the same person, the same step, two years apart — two clusters."""
    world = World(session_factory)
    first = world.report(
        IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )
    second = world.report(
        IVAN, kind="detention", day=at(2026, 4, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )

    run(session_factory)

    assert clusters(session_factory) == [[first], [second]]


def test_two_namesakes_are_two_clusters(
    session_factory: sessionmaker[Session],
) -> None:
    """Scenario 3: the same surname, different people — different clusters."""
    world = World(session_factory)
    first = world.report(
        IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )
    second = world.report(
        PETR, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )

    run(session_factory)

    assert clusters(session_factory) == [[first], [second]]


def test_the_steps_of_one_case_are_not_one_cluster(
    session_factory: sessionmaker[Session],
) -> None:
    """Scenario 4: the steps of one case are linked to the case, not merged into one
    cluster."""
    world = World(session_factory)
    opened = world.report(
        IVAN, kind="case_opened", day=at(2024, 2, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )
    detained = world.report(
        IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )
    arrested = world.report(
        IVAN, kind="arrest", day=at(2024, 3, 3), charges=LAW, orgs=(COURT, MOSCOW)
    )

    run(session_factory)

    assert clusters(session_factory) == [[opened], [detained], [arrested]]


def test_two_cases_of_one_person_are_not_mixed(
    session_factory: sessionmaker[Session],
) -> None:
    """Scenario 5: two cases of one person stay apart."""
    world = World(session_factory)
    first = world.report(
        IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )
    second = world.report(
        IVAN, kind="detention", day=at(2026, 3, 1), charges=DRUGS, orgs=(FSB, MOSCOW)
    )

    run(session_factory)

    assert clusters(session_factory) == [[first], [second]]


def test_an_article_without_a_name_gets_candidates_only(
    session_factory: sessionmaker[Session],
) -> None:
    """Scenario 6: an unnamed article with a known event beside it is a candidate."""
    world = World(session_factory)
    words = "Ивана Петрова задержали в Москве по делу о терроризме"
    known = world.report(
        IVAN,
        sentence=words,
        kind="detention",
        day=at(2024, 3, 1),
        charges=LAW,
        orgs=(FSB, MOSCOW),
    )
    # The same words, but nobody is named as the subject: the article is about the same
    # event and must only become a candidate.
    unnamed = world.report(
        sentence=words, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )

    run(session_factory)

    assert clusters(session_factory) == [[known]]
    with session_factory() as session:
        member = session.scalar(
            select(EventClusterMemberRecord).where(EventClusterMemberRecord.event_id == unnamed)
        )
        assert member is not None
        assert member.cluster_id is None
        assert member.status == "candidate"
        assert member.candidates


def test_an_article_without_a_name_and_without_grounds_gets_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    """Scenario 7: an unnamed article with no grounds gets nothing."""
    world = World(session_factory)
    unnamed = world.report(kind="detention", day=at(2024, 3, 1))

    report = run(session_factory)

    assert report.candidates_only == 1
    with session_factory() as session:
        member = session.scalar(
            select(EventClusterMemberRecord).where(EventClusterMemberRecord.event_id == unnamed)
        )
        assert member is not None
        assert member.cluster_id is None
        assert not member.candidates


def test_a_chain_does_not_join_two_publications(
    session_factory: sessionmaker[Session],
) -> None:
    """Scenario 8: A matches B, B matches C, but A and C are apart."""
    world = World(session_factory)
    first = world.report(
        IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )
    middle = world.report(
        IVAN, kind="detention", day=at(2024, 3, 2), charges=LAW, orgs=(FSB, MOSCOW)
    )
    last = world.report(IVAN, kind="detention", day=at(2024, 3, 3), charges=LAW, orgs=(FSB, MOSCOW))

    run(session_factory)

    # The middle publication matches the anchor (a day later), the third one does not
    # (two days later is a different step), so it opens a cluster of its own: the chain
    # A-B-C never becomes one cluster.
    groups = clusters(session_factory)
    assert groups == [[first, middle], [last]]


def test_a_late_publication_joins_the_old_cluster(
    session_factory: sessionmaker[Session],
) -> None:
    """Scenario 9: a publication that arrives late about an old event joins the old
    cluster."""
    world = World(session_factory)
    first = world.report(
        IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )
    run(session_factory)
    late = world.report(IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW))

    report = run(session_factory)

    assert report.joined == 1
    assert clusters(session_factory) == [[first, late]]


def test_a_second_run_makes_no_duplicates(
    session_factory: sessionmaker[Session],
) -> None:
    """Scenario 10: the same publications processed again change nothing."""
    world = World(session_factory)
    world.report(IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW))
    world.report(IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW))
    first = run(session_factory)
    before = clusters(session_factory)

    second = run(session_factory)

    assert (second.events_seen, second.unchanged) == (first.events_seen, first.events_seen)
    assert clusters(session_factory) == before
    with session_factory() as session:
        assert len(list(session.scalars(select(EventClusterRecord.id)))) == len(before)


def test_a_rolled_back_membership_stays_in_the_history(
    session_factory: sessionmaker[Session],
) -> None:
    """Scenario 11: a rolled back membership is inactive, and the decision is kept."""
    world = World(session_factory)
    world.report(IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW))
    world.report(IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW))
    run(session_factory)

    with session_factory() as session:
        members = list(
            session.scalars(
                select(EventClusterMemberRecord).where(EventClusterMemberRecord.role == "member")
            )
        )
        assert len(members) == 1
        member = members[0]
        member.active = False
        member.status = "rejected"
        member.decided_by = "operator"
        member.decision_note = "это другое событие"
        session.commit()

    with session_factory() as session:
        kept = session.get(EventClusterMemberRecord, members[0].evidence_key)
        assert kept is not None
        assert kept.active is False
        assert kept.status == "rejected"
        assert kept.decided_by == "operator"
        assert kept.decision_note == "это другое событие"
    # The cluster of the rolled back member is gone; the anchor's cluster stays.
    assert len(clusters(session_factory)) == 1
