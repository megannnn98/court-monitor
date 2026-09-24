from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

from sources.models import RawDocument, SourceReference


class DocumentFetcher(Protocol):
    async def fetch(
        self,
        reference: SourceReference,
    ) -> RawDocument: ...


class SourceAdapter(DocumentFetcher, Protocol):
    async def discover(
        self,
        *,
        limit: int,
    ) -> list[SourceReference]: ...


# Which of these external ids are already stored.
KnownIds = Callable[[Sequence[str]], set[str]]


@runtime_checkable
class DiscoversUntilKnown(Protocol):
    """A newest-first source that can stop paging once it reaches what is already stored."""

    async def discover_until_known(
        self, *, limit: int, known: KnownIds
    ) -> list[SourceReference]: ...
