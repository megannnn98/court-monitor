from typing import Protocol

from sources.models import SearchHit, SearchQuery


class SearchBackend(Protocol):
    def search(self, query: SearchQuery) -> list[SearchHit]: ...
