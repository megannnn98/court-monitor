from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

from monitor_core.model import SourceReference

# The discovery and fetch ports live in `monitor_core.ports`; re-exported for existing imports.
from monitor_core.ports import DocumentFetcher, SourceAdapter

__all__ = [
    "DiscoversUntilKnown",
    "DocumentFetcher",
    "KnownIds",
    "SourceAdapter",
]


# Which of these external ids are already stored.
KnownIds = Callable[[Sequence[str]], set[str]]


@runtime_checkable
class DiscoversUntilKnown(Protocol):
    """A newest-first source that can stop paging once it reaches what is already stored."""

    async def discover_until_known(
        self, *, limit: int, known: KnownIds
    ) -> list[SourceReference]: ...
