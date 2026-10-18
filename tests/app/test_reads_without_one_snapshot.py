"""A page reads each owner in a statement of its own (ADR 0031): under READ COMMITTED a
rebuild of the groups committed between two of them may take away a group the first one
saw. The page then leaves that group out instead of failing."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from processing.read import Group
from web.ui import dossier, publications


def _group(group_id: int, name: str) -> Group:
    return Group(
        id=group_id,
        key=name.lower(),
        name=name,
        mention_count=1,
        article_count=1,
        last_published_at=None,
        gender=None,
        name_source="rules",
        collected_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def _evidence(*pairs: tuple[int, int]) -> list[Any]:
    return [SimpleNamespace(article_id=article, group_id=group) for article, group in pairs]


def test_a_publication_lists_only_the_groups_still_there(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        publications, "person_evidence", lambda *_a, **_k: _evidence((10, 1), (10, 2))
    )
    # Group 2 was taken away by a rebuild after the evidence was read.
    monkeypatch.setattr(
        publications.identity, "groups", lambda _db, _ids: {1: _group(1, "Иван Петров")}
    )
    monkeypatch.setattr(publications.identity, "event_counts", lambda _db, _ids: [])
    monkeypatch.setattr(publications, "group_followers", lambda: None)

    people, _events = publications.people_and_events(None, [10])  # type: ignore[arg-type]

    assert people == {10: [("иван петров", "Иван Петров", 1)]}


def test_related_people_leave_out_a_group_gone_between_two_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dossier, "person_evidence", lambda *_a, **_k: _evidence((10, 1), (10, 2), (10, 3))
    )
    # Group 3 is gone by the ordering read, group 2 by the names' read.
    monkeypatch.setattr(dossier.identity, "by_name", lambda _db, _ids: [2, 1])
    monkeypatch.setattr(dossier.identity, "groups", lambda _db, _ids: {1: _group(1, "Иван Петров")})

    related = dossier.related_people(None, 99, _evidence((10, 99)), None)  # type: ignore[arg-type]

    assert related == [("иван петров", "Иван Петров", 1)]
