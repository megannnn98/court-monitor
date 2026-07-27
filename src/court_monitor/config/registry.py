"""Local normalized source registry.

The registry is a YAML file (``config/source_registry.yaml``) materialised from
the Airtable shared view (or a CSV export). It is the **single source of truth**
for which external sources ``court-monitor`` is allowed to touch — no source is
ever added by hand outside of the table.

An import is a *preview* by default (``--dry-run``): it computes new / changed /
unchanged / duplicate counts without writing. A real write is idempotent: the
same registry re-imported produces no changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from court_monitor.normalization import canonicalize_url, url_domain
from court_monitor.sources.airtable_registry import (
    DEFAULT_REGISTRY_VIEW_URL,
    RawRegistryRow,
)

DEFAULT_REGISTRY_PATH = "config/source_registry.yaml"

# Default availability status before the first probe.
STATUS_UNKNOWN = "unknown"


@dataclass(frozen=True)
class DiscoveredFrom:
    type: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "url": self.url}


@dataclass(frozen=True)
class SourceRegistryEntry:
    """A normalized source derived from one Airtable row."""

    id: str
    name: str
    url: str
    domain: str
    source_type: str
    username: str | None = None
    topics: list[str] = field(default_factory=list)
    enabled: bool = True
    status: str = STATUS_UNKNOWN
    last_checked_at: str | None = None
    disable_reason: str | None = None
    discovered_from: DiscoveredFrom = field(
        default_factory=lambda: DiscoveredFrom(
            type="airtable_shared_view", url=DEFAULT_REGISTRY_VIEW_URL
        )
    )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["discovered_from"] = self.discovered_from.to_dict()
        return d


@dataclass
class NormalizeStats:
    rows: int = 0
    recognized: int = 0
    without_url: int = 0
    duplicates: int = 0


@dataclass
class ImportPreview:
    rows: int
    recognized: int
    duplicates: int
    without_url: int
    new_sources: int
    changed_sources: int

    def render(self) -> str:
        return (
            f"Найдено строк: {self.rows}\n"
            f"Распознано источников: {self.recognized}\n"
            f"Дубликатов: {self.duplicates}\n"
            f"Строк без URL: {self.without_url}\n"
            f"Новых источников: {self.new_sources}\n"
            f"Изменённых источников: {self.changed_sources}"
        )


def normalize_rows(
    rows: list[RawRegistryRow],
    *,
    discovered_from: DiscoveredFrom | None = None,
) -> tuple[list[SourceRegistryEntry], NormalizeStats]:
    """Build entries, dropping rows without URL/username and deduping.

    Dedup keys: stable id, canonical URL, and Telegram username. A row that
    shares any key with an already-accepted row is counted as a duplicate and
    skipped.
    """
    stats = NormalizeStats(rows=len(rows))
    seen_keys: set[str] = set()
    entries: list[SourceRegistryEntry] = []
    for raw in rows:
        entry = build_entry(raw, discovered_from=discovered_from)
        if entry is None:
            stats.without_url += 1
            continue
        keys = _dedup_keys(entry)
        if any(k in seen_keys for k in keys):
            stats.duplicates += 1
            continue
        seen_keys.update(keys)
        entries.append(entry)
    stats.recognized = len(entries)
    return entries, stats


def make_stable_id(*, username: str | None, url: str | None, name: str | None) -> str | None:
    """Stable internal id derived from username, then domain, then name.

    Identical inputs always map to the same id so re-imports are idempotent.
    """
    base = ""
    if username:
        base = username.strip().lstrip("@").lower()
    if not base:
        domain = url_domain(url)
        if domain:
            base = domain.replace(".", "-")
    if not base and name:
        base = _slugify(name)
    return base or None


def build_entry(
    raw: RawRegistryRow,
    *,
    discovered_from: DiscoveredFrom | None = None,
) -> SourceRegistryEntry | None:
    """Normalize one raw row into a registry entry, or ``None`` if unusable.

    A row is usable when it has either a URL or a Telegram username. Rows with
    only a name (no link) are dropped with an ``without_url`` count.
    """
    username = raw.username
    url = canonicalize_url(raw.url)

    # A row is usable only when it has either a URL or a Telegram username.
    if url is None and not username:
        return None

    # A bare @username (or a t.me link) still lets us reach the channel.
    if url is None and username:
        url = canonicalize_url(f"https://t.me/{username.lstrip('@')}")

    entry_id = make_stable_id(username=username, url=raw.url, name=raw.name)
    if entry_id is None:
        return None

    source_type = _infer_source_type(url, username)
    domain = url_domain(url) or ""
    name = (raw.name or "").strip() or username or domain

    return SourceRegistryEntry(
        id=entry_id,
        name=name,
        url=url or "",
        domain=domain,
        source_type=source_type,
        username=username,
        topics=list(raw.topics),
        enabled=True,
        status=STATUS_UNKNOWN,
        last_checked_at=None,
        disable_reason=None,
        discovered_from=discovered_from
        or DiscoveredFrom(type="airtable_shared_view", url=DEFAULT_REGISTRY_VIEW_URL),
    )


def merge_registry(
    existing: list[SourceRegistryEntry],
    incoming: list[SourceRegistryEntry],
    stats: NormalizeStats | None = None,
) -> tuple[list[SourceRegistryEntry], ImportPreview]:
    """Idempotently merge ``incoming`` into ``existing``.

    Returns the merged list and a preview. ``rows``/``recognized``/
    ``duplicates``/``without_url`` come from the normalization step (passed in
    via ``stats``); ``new_sources``/``changed_sources`` are computed against the
    existing registry. Entries are matched by stable id; a change is detected
    when name, url, topics or source_type differ.
    """
    by_id: dict[str, SourceRegistryEntry] = {e.id: e for e in existing}
    duplicates = 0
    new_sources = 0
    changed_sources = 0
    seen_ids: set[str] = set()

    for entry in incoming:
        if entry.id in seen_ids:
            duplicates += 1
            continue
        seen_ids.add(entry.id)
        current = by_id.get(entry.id)
        if current is None:
            by_id[entry.id] = entry
            new_sources += 1
            continue
        if _entry_changed(current, entry):
            # Preserve last_checked_at / status from existing; refresh the rest.
            by_id[entry.id] = SourceRegistryEntry(
                id=current.id,
                name=entry.name,
                url=entry.url,
                domain=entry.domain,
                source_type=entry.source_type,
                username=entry.username,
                topics=entry.topics,
                enabled=current.enabled,
                status=current.status,
                last_checked_at=current.last_checked_at,
                disable_reason=current.disable_reason,
                discovered_from=entry.discovered_from,
            )
            changed_sources += 1
        else:
            duplicates += 1

    merged = list(by_id.values())
    st = stats or NormalizeStats()
    preview = ImportPreview(
        rows=st.rows,
        recognized=st.recognized,
        duplicates=st.duplicates + duplicates,
        without_url=st.without_url,
        new_sources=new_sources,
        changed_sources=changed_sources,
    )
    return merged, preview


def load_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> list[SourceRegistryEntry]:
    """Load the local registry YAML, returning ``[]`` when absent."""
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    raw_sources = data.get("sources") or []
    entries: list[SourceRegistryEntry] = []
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        df = item.get("discovered_from") or {}
        entries.append(
            SourceRegistryEntry(
                id=str(item["id"]),
                name=str(item.get("name", item["id"])),
                url=str(item.get("url", "")),
                domain=str(item.get("domain", "")),
                source_type=str(item.get("source_type", "website")),
                username=item.get("username"),
                topics=list(item.get("topics") or []),
                enabled=bool(item.get("enabled", True)),
                status=str(item.get("status", STATUS_UNKNOWN)),
                last_checked_at=item.get("last_checked_at"),
                disable_reason=item.get("disable_reason"),
                discovered_from=DiscoveredFrom(
                    type=str(df.get("type", "airtable_shared_view")),
                    url=str(df.get("url", DEFAULT_REGISTRY_VIEW_URL)),
                ),
            )
        )
    return entries


def save_registry(
    entries: list[SourceRegistryEntry], path: str | Path = DEFAULT_REGISTRY_PATH
) -> None:
    """Write the registry YAML (sorted by id for stable diffs)."""
    ordered = sorted(entries, key=lambda e: e.id)
    payload = {
        "sources": [e.to_dict() for e in ordered],
        "note": (
            "Local normalized source registry. Sources are imported from the "
            "Airtable shared view only — do not add entries by hand."
        ),
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


def set_entry_status(
    entries: list[SourceRegistryEntry],
    entry_id: str,
    *,
    status: str,
    reason: str | None = None,
) -> list[SourceRegistryEntry]:
    """Return a copy of ``entries`` with one entry's availability updated."""
    now = datetime.now(UTC).isoformat()
    out: list[SourceRegistryEntry] = []
    for e in entries:
        if e.id == entry_id:
            disable_reason = reason if status in {"unsupported", "invalid_url"} else None
            out.append(
                SourceRegistryEntry(
                    id=e.id,
                    name=e.name,
                    url=e.url,
                    domain=e.domain,
                    source_type=e.source_type,
                    username=e.username,
                    topics=e.topics,
                    enabled=e.enabled,
                    status=status,
                    last_checked_at=now,
                    disable_reason=disable_reason,
                    discovered_from=e.discovered_from,
                )
            )
        else:
            out.append(e)
    return out


def _dedup_keys(entry: SourceRegistryEntry) -> list[str]:
    keys: list[str] = [f"id:{entry.id}"]
    if entry.url:
        keys.append(f"url:{entry.url}")
    if entry.username:
        keys.append(f"u:{entry.username.strip().lstrip('@').lower()}")
    return keys


def _entry_changed(a: SourceRegistryEntry, b: SourceRegistryEntry) -> bool:
    return (
        a.name != b.name
        or a.url != b.url
        or a.source_type != b.source_type
        or a.username != b.username
        or a.topics != b.topics
    )


def _infer_source_type(url: str | None, username: str | None) -> str:
    target = (url or "").lower()
    if "t.me" in target or (username and not url):
        return "telegram"
    if any(target.endswith(ext) or f".{ext}/" in target + "/" for ext in ("rss", "xml", "atom")):
        return "rss"
    return "website"


def _slugify(value: str) -> str:
    import re  # noqa: PLC0415

    s = re.sub(r"[^\w\s-]", "", value.lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s or "source"
