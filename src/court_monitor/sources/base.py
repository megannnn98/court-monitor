"""Base classes for source adapters."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import FetchHealth, SourceType

_WS_RE = re.compile(r"\s+", flags=re.UNICODE)


def normalize_text(raw: str) -> str:
    """Collapse whitespace (Unicode-aware). Used for hashing/dedup, not storage.

    Identity is preserved (case retained). Only whitespace variation is removed
    so minor reformatting of a page does not defeat deduplication.
    """
    return _WS_RE.sub(" ", raw).strip()


def content_hash(text: str) -> str:
    """SHA-256 of normalized text. Returns hex digest."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FetchResult:
    """A single fetched document ready for ingestion."""

    url: str
    content: str  # raw payload (HTML / text)
    text: str  # normalized visible text
    content_hash: str
    fetched_at: datetime
    http_status: int = 200
    canonical_url: str | None = None
    source_type: SourceType = SourceType.sudrf
    source_name: str | None = None
    source_id: str | None = None
    external_id: str | None = None
    content_type: str | None = None
    adapter_version: str | None = None
    published_at: datetime | None = None
    title: str | None = None

    @classmethod
    def from_content(
        cls,
        *,
        url: str,
        content: str,
        source_type: SourceType,
        source_name: str | None = None,
        source_id: str | None = None,
        external_id: str | None = None,
        content_type: str | None = None,
        adapter_version: str | None = None,
        http_status: int = 200,
        canonical_url: str | None = None,
        published_at: datetime | None = None,
        title: str | None = None,
        text: str | None = None,
    ) -> FetchResult:
        """Build a FetchResult from raw content.

        If ``text`` is not provided, falls back to ``content`` (which may be
        HTML).  The ``text`` field stores *whitespace-normalized* visible text
        when a dedicated extractor is available; otherwise it is a best-effort
        fold of the raw payload.
        """
        visible = text if text is not None else content
        return cls(
            url=url,
            content=content,
            text=normalize_text(visible),
            content_hash=content_hash(visible),
            fetched_at=datetime.now(UTC),
            http_status=http_status,
            canonical_url=canonical_url,
            source_type=source_type,
            source_name=source_name,
            source_id=source_id,
            external_id=external_id,
            content_type=content_type,
            adapter_version=adapter_version,
            published_at=published_at,
            title=title,
        )


@dataclass(frozen=True)
class FetchProblem:
    """A fetch attempt that produced no usable content.

    Surfaced to the service layer (which holds a DB session) instead of being
    silently dropped inside the adapter — adapters themselves stay
    side-effect-free (spec: "Sources... are pure"). The service layer turns
    this into a ``ReviewItem`` (item_type="source_blocked") so an operator
    sees a blocked/erroring source instead of only a log line (technical-debt
    D-011).
    """

    url: str
    health: FetchHealth
    http_status: int
    source_id: str | None = None
    source_name: str | None = None


class SourceAdapter(Protocol):
    """Protocol every source adapter satisfies."""

    config: SourceConfig

    def fetch_new(self) -> Iterator[FetchResult | FetchProblem]: ...


def get_adapter(source_cfg: SourceConfig) -> SourceAdapter:
    """Factory selecting an adapter by type."""
    if source_cfg.type == SourceType.sudrf:
        # Deferred import: court_monitor.sources.sudrf imports this module
        # (FetchResult), so we resolve SudrfAdapter lazily to avoid a cycle.
        from court_monitor.sources.sudrf import SudrfAdapter  # noqa: PLC0415

        return SudrfAdapter(source_cfg)
    raise ValueError(f"Unsupported source type: {source_cfg.type!r} for {source_cfg.name!r}")
