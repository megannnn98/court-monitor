"""Local raw-document cache (git-ignored): what makes the corpus replayable offline.

One JSON line per fetched document per source. Out-of-period and unparsable
documents are remembered too (without content), so a resumed build does not
fetch them again.
"""

from __future__ import annotations

import base64
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from sources.models import RawDocument, SourceReference


class CacheEntryStatus(StrEnum):
    ARTICLE = "article"
    OUT_OF_PERIOD = "out_of_period"
    PARSE_FAILED = "parse_failed"


class RawCacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    external_id: str
    url: str
    status: CacheEntryStatus
    fetched_at: datetime
    published_at: datetime | None = None
    content_type: str = ""
    # Base64 HTML for ARTICLE and PARSE_FAILED entries (a fixed parser can re-read it).
    content_b64: str | None = None
    error: str | None = None

    def raw_document(self) -> RawDocument:
        if self.content_b64 is None:
            raise ValueError(f"{self.source}:{self.external_id} has no cached content")
        return RawDocument(
            external_id=self.external_id,
            url=self.url,
            fetched_at=self.fetched_at,
            content_type=self.content_type,
            content=base64.b64decode(self.content_b64),
        )

    def reference(self) -> SourceReference:
        return SourceReference(external_id=self.external_id, url=self.url)


class RawCorpusCache:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory

    def _path(self, source: str) -> Path:
        return self._directory / "raw" / f"{source}.jsonl"

    def entries(self, source: str) -> dict[str, RawCacheEntry]:
        """Latest entry per external id (a later line wins)."""
        path = self._path(source)
        if not path.exists():
            return {}
        entries: dict[str, RawCacheEntry] = {}
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    entry = RawCacheEntry.model_validate_json(line)
                    entries[entry.external_id] = entry
        return entries

    def append(self, entry: RawCacheEntry) -> None:
        path = self._path(entry.source)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")


def article_entry(
    source: str, raw: RawDocument, *, status: CacheEntryStatus, published_at: datetime | None
) -> RawCacheEntry:
    return RawCacheEntry(
        source=source,
        external_id=raw.external_id,
        url=raw.url,
        status=status,
        fetched_at=raw.fetched_at,
        published_at=published_at,
        content_type=raw.content_type,
        content_b64=(
            base64.b64encode(raw.content).decode("ascii")
            if status is not CacheEntryStatus.OUT_OF_PERIOD
            else None
        ),
    )
