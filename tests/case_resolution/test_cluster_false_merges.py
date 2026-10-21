"""The false merges of the event clusters: the cases that must stay apart.

A cluster that joins two independent events is the error the whole stage is built to
avoid. These are the cases where the rules have to refuse: two namesakes, two steps of
one case, two cases of one person, and two events whose courts have nothing in common.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from case_resolution.world import World, at
from cases.clusters.service import resolve
from cases.storage.models import EventClusterMemberRecord

IVAN = "Иван Петров"
PETR = "Пётр Сидоров"
MOSCOW = ("location", "Москва")
OMSK = ("location", "Омск")
FSB = ("authority", "ФСБ")
COURT = ("court", "Мосгорсуд")
OTHER_COURT = ("court", "Омский облсуд")
LAW = ("205.2/3",)
DRUGS = ("228/1",)


def clusters(factory: sessionmaker[Session]) -> list[list[int]]:
    with factory() as session:
        members = session.execute(
            select(EventClusterMemberRecord.cluster_id, EventClusterMemberRecord.event_id).where(
                EventClusterMemberRecord.active.is_(True),
                EventClusterMemberRecord.cluster_id.is_not(None),
            )
        ).all()
    grouped: dict[object, list[int]] = {}
    for cluster_id, event_id in members:
        grouped.setdefault(cluster_id, []).append(event_id)
    return sorted([sorted(ids) for ids in grouped.values()], key=lambda ids: ids[0])


def test_two_namesakes_with_the_same_words_stay_apart(
    session_factory: sessionmaker[Session],
) -> None:
    """Two people with the same surname, the same step, the same day and the same
    article: two clusters, never one."""
    world = World(session_factory)
    words = "Петрова задержали в Москве по делу о терроризме"
    first = world.report(
        IVAN, sentence=words, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )
    second = world.report(
        PETR, sentence=words, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )

    resolve(session_factory)

    assert clusters(session_factory) == [[first], [second]]


def test_two_courts_that_have_nothing_in_common_stay_apart(
    session_factory: sessionmaker[Session],
) -> None:
    """The same person, the same step, the same day: two courts that are not the same
    court forbid the join."""
    world = World(session_factory)
    first = world.report(
        IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(COURT, MOSCOW)
    )
    second = world.report(
        IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(OTHER_COURT, OMSK)
    )

    resolve(session_factory)

    assert clusters(session_factory) == [[first], [second]]


def test_the_steps_of_one_case_stay_apart(
    session_factory: sessionmaker[Session],
) -> None:
    """The steps of one case are one case, but two events: two clusters."""
    world = World(session_factory)
    opened = world.report(
        IVAN, kind="case_opened", day=at(2024, 2, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )
    arrested = world.report(
        IVAN, kind="arrest", day=at(2024, 3, 1), charges=LAW, orgs=(COURT, MOSCOW)
    )

    resolve(session_factory)

    assert clusters(session_factory) == [[opened], [arrested]]


def test_two_cases_of_one_person_stay_apart(
    session_factory: sessionmaker[Session],
) -> None:
    """One person, two cases, two articles of the same step on the same day: the
    different article keeps them apart."""
    world = World(session_factory)
    first = world.report(
        IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )
    second = world.report(
        IVAN, kind="detention", day=at(2024, 3, 1), charges=DRUGS, orgs=(FSB, MOSCOW)
    )

    resolve(session_factory)

    assert clusters(session_factory) == [[first], [second]]


def test_one_person_one_step_one_day_and_nothing_else_stays_apart(
    session_factory: sessionmaker[Session],
) -> None:
    """The same person, the same step, the same day and nothing else in common: two
    detentions, not one.

    This is the case the rules must refuse: a series of administrative arrests of one
    activist looks exactly like this.
    """
    world = World(session_factory)
    first = world.report(IVAN, kind="detention", day=at(2024, 3, 1))
    second = world.report(IVAN, kind="detention", day=at(2024, 3, 1))

    resolve(session_factory)

    assert clusters(session_factory) == [[first], [second]]


def test_one_person_one_step_a_day_apart_and_nothing_else_stays_apart(
    session_factory: sessionmaker[Session],
) -> None:
    """The same person, the same step, a day apart and nothing else in common: two
    detentions."""
    world = World(session_factory)
    first = world.report(IVAN, kind="detention", day=at(2024, 3, 1))
    second = world.report(IVAN, kind="detention", day=at(2024, 3, 2))

    resolve(session_factory)

    assert clusters(session_factory) == [[first], [second]]


def test_a_series_of_arrests_on_adjacent_days_stays_apart(
    session_factory: sessionmaker[Session],
) -> None:
    """Three arrests on three days, in three different courts: three clusters."""
    world = World(session_factory)
    first = world.report(IVAN, kind="arrest", day=at(2024, 3, 1), charges=LAW, orgs=(COURT, MOSCOW))
    second = world.report(
        IVAN, kind="arrest", day=at(2024, 3, 2), charges=LAW, orgs=(OTHER_COURT, OMSK)
    )
    third = world.report(IVAN, kind="arrest", day=at(2024, 3, 3), charges=LAW, orgs=(FSB, MOSCOW))

    resolve(session_factory)

    assert clusters(session_factory) == [[first], [second], [third]]
