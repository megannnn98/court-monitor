"""Which candidates count as administrative or criminal on the candidates page."""

from __future__ import annotations

import pytest

from web.candidate_rows import _has_criminal_events, _is_administrative_only

CRIMINAL = ["Политическая статья: УК РФ ст. 207.3"]
ADMINISTRATIVE = ["Политическая статья: КоАП РФ ст. 20.3.3"]
NO_CHARGE = ["Статья содержит признаки политического преследования"]


@pytest.mark.parametrize(
    ("event_type", "reasons", "expected"),
    [
        # A criminal-code charge is criminal whatever the latest news is about.
        ("fine", CRIMINAL, True),
        ("sentence", CRIMINAL, True),
        # «Арестован на 15 суток» under КоАП is an administrative arrest, not a case.
        ("arrest", ADMINISTRATIVE, False),
        ("detention", ADMINISTRATIVE, False),
        # Without charge references the event is the only evidence.
        ("sentence", NO_CHARGE, True),
        ("arrest", NO_CHARGE, True),
        ("fine", NO_CHARGE, False),
        # A person with no linked event proves nothing criminal.
        (None, NO_CHARGE, False),
        (None, [], False),
    ],
)
def test_criminal_events(event_type: str | None, reasons: list[str], expected: bool) -> None:
    assert _has_criminal_events(event_type, reasons) is expected


@pytest.mark.parametrize(
    ("event_type", "reasons", "expected"),
    [
        ("arrest", ADMINISTRATIVE, True),
        ("fine", NO_CHARGE, True),
        ("fine", CRIMINAL, False),
        ("sentence", NO_CHARGE, False),
        (None, NO_CHARGE, False),
    ],
)
def test_administrative_only(event_type: str | None, reasons: list[str], expected: bool) -> None:
    assert _is_administrative_only(reasons, event_type) is expected
