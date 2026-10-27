"""The shared test database is accepted only at this code's single Alembic head."""

from __future__ import annotations

import pytest
from support.schema_revision import revision_mismatch

HEAD = "r2e3r4t5y6u7"
FOREIGN = "r2e3d4f5a6b7"


def test_the_head_alone_is_accepted() -> None:
    assert revision_mismatch([HEAD], HEAD) is None


@pytest.mark.parametrize(
    "revisions",
    [[], [FOREIGN], [HEAD, FOREIGN], [FOREIGN, HEAD]],
    ids=["unmigrated", "another", "head-then-another", "another-then-head"],
)
def test_anything_else_is_refused_with_every_revision_named(revisions: list[str]) -> None:
    refusal = revision_mismatch(revisions, HEAD)

    assert refusal is not None
    assert f"head is {HEAD}" in refusal
    for revision in revisions:
        assert revision in refusal.split("head is")[0]
