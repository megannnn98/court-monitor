"""The rollback of a case link: the link stops counting, the decision is kept.

ADR 0033 narrows ADR 0032, which excluded the manual editing of the cases: a person
may take one link back, with a reason, and the row keeps who decided, when and why.
Nothing is deleted, and the events and the cases are untouched.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from case_resolution.world import World, at
from cases.service import resolve, rollback_link
from cases.storage.models import CaseEventLinkRecord

IVAN = "Иван Петров"
MOSCOW = ("location", "Москва")
FSB = ("authority", "ФСБ")
LAW = ("205.2/3",)


def test_a_rolled_back_link_is_inactive_and_keeps_its_decision(
    session_factory: sessionmaker[Session],
) -> None:
    world = World(session_factory)
    words = "Ивана Петрова задержали в Москве по делу о терроризме"
    world.report(
        IVAN, sentence=words, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )
    world.report(
        IVAN, sentence=words, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW)
    )

    resolve(session_factory)

    with session_factory() as session:
        links = list(session.scalars(select(CaseEventLinkRecord)))
        assert len(links) == 2
        joined = [link for link in links if link.score > 0]
        assert len(joined) == 1
        target = joined[0]
        assert rollback_link(
            session, target.evidence_key, author="operator", reason="это другое событие"
        )
        session.commit()

    with session_factory() as session:
        kept = session.get(CaseEventLinkRecord, target.evidence_key)
        assert kept is not None
        assert kept.active is False
        assert kept.status == "rejected"
        assert kept.decided_by == "operator"
        assert kept.decision_note == "это другое событие"
        # The row is kept: nothing is deleted.
        assert len(list(session.scalars(select(CaseEventLinkRecord)))) == 2


def test_a_rollback_needs_an_author_and_a_reason(
    session_factory: sessionmaker[Session],
) -> None:
    world = World(session_factory)
    world.report(IVAN, kind="detention", day=at(2024, 3, 1), charges=LAW, orgs=(FSB, MOSCOW))
    resolve(session_factory)
    with session_factory() as session:
        key = next(iter(session.scalars(select(CaseEventLinkRecord.evidence_key))))
        with pytest.raises(ValueError):
            rollback_link(session, key, author="", reason="причина")
        assert rollback_link(session, "несуществующий", author="operator", reason="нет") is False
