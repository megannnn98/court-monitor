from typing import Protocol

from models import SearchHit, SearchQuery


class SearchBackend(Protocol):
    def search(self, query: SearchQuery) -> list[SearchHit]: ...
