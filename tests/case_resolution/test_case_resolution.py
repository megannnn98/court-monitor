"""Case resolution on PostgreSQL: the scenarios of the task, over a world built the way the
extraction and the entity rebuild leave it."""

from __future__ import annotations

import random
from uuid import UUID

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from case_resolution.world import World, at
from cases import read
from cases.service import ResolutionReport, resolve
from cases.storage.models import CaseEventLinkRecord
from evaluation.disposable import wipe

IVAN = "Иван Петров"
PETR = "Пётр Сидоров"
MOSCOW = ("location", "Москва")
FSB = ("authority", "ФСБ")
COURT = ("court", "Мосгорсуд")
OMSK = ("location", "Омск")
LAW = ("205.2/3",)
DRUGS = ("228/1",)


def run(factory: sessionmaker[Session], **kwargs: int | bool) -> ResolutionReport:
    return resolve(factory, **kwargs)  # type: ignore[arg-type]


def cases(factory: sessionmaker[Session]) -> list[read.CaseDetail]:
    with factory() as session:
        _, summaries = read.list_cases(session, limit=1000)
        return [d for s in summaries if (d := read.case_detail(session, s.id)) is not None]


def partition(factory: sessionmaker[Session], labels: dict[int, str]) -> set[frozenset[str]]:
    """The cases as sets of event labels: what must not depend on the order of arrival."""
    return {
        frozenset(labels[event.event_id] for event in case.events if event.event_id in labels)
        for case in cases(factory)
    }


def test_several_reports_of_one_detention_are_one_case(
    session_factory: sessionmaker[Session],
) -> None:
    world = World(session_factory)
    words = "Ивана Петрова задержали в Москве по делу о терроризме и поместили в изолятор"
    for _ in range(3):
        world.report(IVAN, sentence=words, charges=LAW, orgs=(MOSCOW,))

    report = run(session_factory)

    assert (report.opened, report.joined) == (1, 2)
    (case,) = cases(session_factory)
    assert case.summary.events == 3 and case.summary.articles == 3
    assert all(event.reasons for event in case.events[1:] if event.score)


def test_detention_trial_and_sentence_two_years_apart_are_one_case(
    session_factory: sessionmaker[Session],
) -> None:
    world = World(session_factory)
    world.report(IVAN, kind="case_opened", day=at(2024, 2, 1), charges=LAW, orgs=(FSB, MOSCOW))
    world.report(IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW))
    world.report(IVAN, kind="arrest", day=at(2024, 3, 3), charges=LAW, orgs=(COURT, MOSCOW))
    world.report(IVAN, kind="sentence", day=at(2026, 5, 10), charges=LAW, orgs=(COURT, MOSCOW))

    run(session_factory)

    (case,) = cases(session_factory)
    assert [e.event_type for e in case.events] == ["case_opened", "detention", "arrest", "sentence"]
    assert case.participants[0].person_key == IVAN.lower()


def test_one_person_two_different_cases(session_factory: sessionmaker[Session]) -> None:
    world = World(session_factory)
    world.report(IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW))
    world.report(IVAN, kind="detention", day=at(2025, 6, 1), charges=DRUGS, orgs=(OMSK,))
    world.report(IVAN, kind="sentence", day=at(2026, 5, 10), charges=LAW, orgs=(COURT, MOSCOW))

    run(session_factory)

    with session_factory() as session:
        mine = read.cases_of_person(session, IVAN.lower())
    assert sorted(case.events for case in mine) == [1, 2]


def test_several_accused_in_one_case(session_factory: sessionmaker[Session]) -> None:
    world = World(session_factory)
    world.report(IVAN, PETR, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(MOSCOW,))
    world.report(IVAN, kind="sentence", day=at(2025, 3, 1), charges=LAW, orgs=(COURT, MOSCOW))
    world.report(PETR, kind="sentence", day=at(2025, 3, 1), charges=LAW, orgs=(COURT, MOSCOW))

    run(session_factory)

    (case,) = cases(session_factory)
    assert {p.person_key for p in case.participants} == {IVAN.lower(), PETR.lower()}


def test_a_judge_in_many_publications_joins_no_cases(
    session_factory: sessionmaker[Session],
) -> None:
    world = World(session_factory)
    judge = "Сергей Судьин"
    world.report(IVAN, kind="sentence", day=at(2025, 1, 1), charges=LAW, judges=(judge,))
    world.report(PETR, kind="sentence", day=at(2025, 1, 1), charges=LAW, judges=(judge,))

    run(session_factory)

    found = cases(session_factory)
    assert len(found) == 2
    assert all(judge.lower() not in {p.person_key for p in c.participants} for c in found)
    with session_factory() as session:
        assert read.cases_of_person(session, judge.lower()) == []


def test_a_publication_without_a_name_forms_no_case(
    session_factory: sessionmaker[Session],
) -> None:
    """Nobody is named: no case is formed, and the link records that the event was
    looked at (ADR 0033)."""
    world = World(session_factory)
    words = "Задержали участника акции, сообщили в полиции города, возбуждено дело о беспорядках"
    world.report(IVAN, sentence=words, charges=LAW, orgs=(MOSCOW,))
    unnamed = world.report(sentence=words, charges=LAW, orgs=(MOSCOW,))

    report = run(session_factory)

    found = cases(session_factory)
    assert len(found) == 1
    assert report.without_case == 1
    with session_factory() as session:
        link = session.scalar(
            select(CaseEventLinkRecord).where(CaseEventLinkRecord.event_id == unnamed)
        )
        assert link is not None
        assert link.case_id is None
        assert link.status == "candidate"

    # The second run does not look at it again.
    again = run(session_factory)
    assert again.without_case == 0
    assert again.unchanged >= 1


def test_one_article_with_two_cases(session_factory: sessionmaker[Session]) -> None:
    world = World(session_factory)
    first = "Ивана Петрова арестовали в Москве по статье о терроризме"
    second = "Пётр Сидоров осуждён в Омске по статье о наркотиках"
    article = world.article(f"Дайджест. {first}. {second}. Иван Петров Пётр Сидоров Москва Омск")
    world.event(article, first, IVAN, kind="arrest", charges=LAW, orgs=(MOSCOW,))
    world.event(article, second, PETR, kind="sentence", charges=DRUGS, orgs=(OMSK,))

    run(session_factory)

    found = cases(session_factory)
    assert len(found) == 2
    assert {s.article_id for c in found for s in c.sources} == {article.id}


def test_contradicting_dates_do_not_join(session_factory: sessionmaker[Session]) -> None:
    world = World(session_factory)
    words = "Ивана Петрова задержали в Москве по делу о терроризме, сообщили в ФСБ России"
    world.report(IVAN, sentence=words, day=at(2024, 3, 1), charges=LAW, orgs=(MOSCOW,))
    world.report(IVAN, sentence=words, day=at(2025, 3, 1), charges=LAW, orgs=(MOSCOW,))

    run(session_factory)

    assert len(cases(session_factory)) == 2


def test_a_second_run_changes_nothing(session_factory: sessionmaker[Session]) -> None:
    world = World(session_factory)
    world.report(IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(MOSCOW,))
    world.report(IVAN, kind="sentence", day=at(2025, 3, 1), charges=LAW, orgs=(MOSCOW,))
    world.report(PETR, kind="detention", charges=DRUGS, orgs=(OMSK,))
    run(session_factory)
    before = {c.summary.id: c.summary.events for c in cases(session_factory)}

    again = run(session_factory)

    assert (again.opened, again.joined, again.reattached) == (0, 0, 0)
    assert again.unchanged == 3
    assert {c.summary.id: c.summary.events for c in cases(session_factory)} == before


def test_a_re_extracted_event_is_tied_back_not_made_again(
    session_factory: sessionmaker[Session],
) -> None:
    world = World(session_factory)
    first = world.report(IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(MOSCOW,))
    world.report(IVAN, kind="sentence", day=at(2025, 3, 1), charges=LAW, orgs=(MOSCOW,))
    run(session_factory)
    before = {c.summary.id for c in cases(session_factory)}

    new_id = world.reextract(first)
    report = run(session_factory)

    assert report.reattached == 1 and report.opened == 0 and report.deactivated == 0
    (case,) = cases(session_factory)
    assert case.summary.id in before
    assert new_id in {e.event_id for e in case.events}


def test_an_article_whose_words_changed_leaves_no_dangling_link(
    session_factory: sessionmaker[Session],
) -> None:
    world = World(session_factory)
    words = "Ивана Петрова задержали в Москве по делу о терроризме, сообщили в ФСБ России"
    event = world.report(IVAN, sentence=words, kind="detention", charges=LAW, orgs=(MOSCOW,))
    run(session_factory)

    world.reextract(event, sentence=words.replace("задержали", "арестовали"))
    report = run(session_factory)

    assert report.deactivated == 1
    with session_factory() as session:
        detail = read.case_detail(session, cases(session_factory)[0].summary.id)
    assert detail is not None
    assert all(e.event_id is not None for e in detail.events)


def test_a_rebuild_of_the_groups_multiplies_no_cases(
    session_factory: sessionmaker[Session],
) -> None:
    world = World(session_factory)
    world.report(IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(MOSCOW,))
    world.report(IVAN, kind="sentence", day=at(2025, 3, 1), charges=LAW, orgs=(MOSCOW,))
    world.report(PETR, kind="detention", charges=DRUGS, orgs=(OMSK,))
    run(session_factory)
    before = {c.summary.id for c in cases(session_factory)}

    world.rebuild_groups()
    for event_id in list(world.specs):
        world.reextract(event_id)
    report = run(session_factory)

    assert report.opened == 0
    assert {c.summary.id for c in cases(session_factory)} == before
    names = {p.name for c in cases(session_factory) for p in c.participants}
    assert names == {IVAN, PETR}


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_the_order_of_arrival_does_not_change_the_cases(
    session_factory: sessionmaker[Session], test_engine: Engine, seed: int
) -> None:
    def build(order: list[str]) -> set[frozenset[str]]:
        wipe(test_engine)
        world = World(session_factory)
        made = {}
        # `bridge` matches both `detention` and `sentence`, which do not match each other:
        # in the arrival order detention, sentence, bridge it would be an ambiguity.
        specs = {
            "detention": (IVAN, "detention", at(2024, 3, 1), LAW, (FSB, MOSCOW)),
            "bridge": (IVAN, "arrest", at(2024, 3, 3), LAW, (FSB, COURT)),
            "sentence": (IVAN, "sentence", at(2026, 5, 1), LAW, (COURT, OMSK)),
            "petr-detention": (PETR, "detention", None, DRUGS, (OMSK,)),
            "petr-other": (PETR, "detention", None, LAW, (MOSCOW,)),
        }
        for label in order:
            who, kind, day, charges, orgs = specs[label]
            made[world.report(who, kind=kind, day=day, charges=charges, orgs=orgs)] = label
        run(session_factory)
        return partition(session_factory, made)

    labels = ["detention", "bridge", "sentence", "petr-detention", "petr-other"]
    shuffled = labels[:]
    random.Random(seed).shuffle(shuffled)

    assert build(labels) == build(shuffled)
    assert {"detention", "bridge", "sentence"} in [set(group) for group in build(shuffled)]


def test_a_case_id_is_a_uuid_and_survives_a_second_batch_size(
    session_factory: sessionmaker[Session],
) -> None:
    world = World(session_factory)
    for day in (1, 2, 3):
        world.report(IVAN, kind="detention", day=at(2024, 3, day), charges=LAW, orgs=(MOSCOW,))
    report = run(session_factory, batch_size=1)

    assert report.batches == 3
    assert all(isinstance(c.summary.id, UUID) for c in cases(session_factory))


def test_a_dry_run_writes_nothing(session_factory: sessionmaker[Session]) -> None:
    world = World(session_factory)
    world.report(IVAN, kind="detention", charges=LAW, orgs=(MOSCOW,))

    report = run(session_factory, dry_run=True)

    assert report.opened == 1 and report.dry_run
    assert cases(session_factory) == []
