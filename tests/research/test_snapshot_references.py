"""Deterministic extraction of explicit snapshot references from a query."""

from __future__ import annotations

import pytest

from research.workflow.snapshot_references import extract_explicit_snapshot_ids


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Кого нет в перечне по snapshot 3?", [3]),
        ("snapshot #12", [12]),
        ("Snapshot_ID=5, политические", [5]),
        ("snapshot_id: 8", [8]),
        ("по снапшоту №4", [4]),
        ("в снапшоте 21", [21]),
        ("снэпшот номер 9", [9]),
        ("snapshot 3 и снапшот 3", [3]),
        ("сравни snapshot 3 и snapshot 5", [3, 5]),
    ],
)
def test_explicit_snapshot_references_are_extracted(query: str, expected: list[int]) -> None:
    assert extract_explicit_snapshot_ids(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "Найди 3 человека, которых нет в перечне Росфинмониторинга",
        "политически преследуемые 30–35 лет",
        "дела за 2024 год, статья 207.3",
        "snapshots are imported daily",
        "",
    ],
)
def test_bare_numbers_are_not_snapshot_references(query: str) -> None:
    assert extract_explicit_snapshot_ids(query) == []
