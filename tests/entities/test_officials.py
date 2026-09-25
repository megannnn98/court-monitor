"""A title right before a name in the texts: a judge, a prosecutor, an official."""

from __future__ import annotations

import pytest

from entities.officials import kind_of_title, title_before, titled


@pytest.mark.parametrize(
    ("context", "kind"),
    [
        ("Мосгорсуд в составе: судья ", "judge"),
        ("по жалобе к председателю суда ", "judge"),
        ("высказывался о главе СК ", "police"),
        ("запрос следователю ", "police"),
        ("начальник полиции ", "police"),
        ("ходатайство прокурора ", "prosecutor"),
        ("письмо губернатору Тюменской области ", "official"),
        ("встреча с министром обороны ", "official"),
        ("по приказу министра внутренних дел ", "official"),
        # A former judge is a person now, charged or not.
        ("к делу бывшего судьи ", None),
        # The title belongs to someone else: the name is after the verb.
        ("прокурор попросил для ", None),
        ("", None),
    ],
)
def test_the_title_right_before_a_name(context: str, kind: str | None) -> None:
    title = title_before(context)
    assert (kind_of_title(title) if title else None) == kind


def test_a_title_counts_in_two_mentions_or_half_of_them() -> None:
    contexts = [
        # Бастрыкин: every time the head of СК.
        (1, "глава СК "),
        (1, "главе СК "),
        (1, "сообщил "),
        # Мурзагулов: once among many, beside a title that is someone else's.
        (2, "критик главы региона "),
        *[(2, "активист ") for _ in range(5)],
        # Минакова: her only mention.
        (3, "судья "),
    ]

    assert titled(contexts) == {1: ("police", "глава ск"), 3: ("judge", "судья")}
