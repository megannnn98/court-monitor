from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

# The discovery and fetch ports live in `monitor_core.ports`; re-exported for existing imports.
from monitor_core.ports.discovery import SourceAdapter
from monitor_core.ports.fetcher import DocumentFetcher
from sources.models import SourceReference

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
