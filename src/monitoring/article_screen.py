"""What a junk screen is to the purge, whichever way it judges an article.

The purge asks a screen for a score per article and holds, never deletes, what the screen
says may be a criminal case. A screen that cannot judge raises `JunkScreenError`, and the
purge stops before its batch. The screens themselves: `junk_screen.EmbeddingScreen`,
`decision_screen.DecisionScreen`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class JunkScreenError(Exception):
    """The screen is on and cannot judge: nothing must be deleted."""


class ArticleScreen(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def cutoff(self) -> float: ...

    def scores(self, articles: Sequence[tuple[str, str]]) -> list[float]:
        """A score per (title, text): the higher, the likelier a criminal case."""
        ...
