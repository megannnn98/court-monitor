"""What a full collection pass consists of, independent of who renders it.

``run-all`` on the command line and the ``fetch_all`` background job do the
same thing: walk both source flavours — legacy ``sources.yaml`` sudrf entries
and ``source_registry.yaml`` Telegram channels — fetching and parsing each.
That walk used to be written out in both places, with the same
try/accumulate/except shape and the same knowledge of which flavours exist.
Only the *rendering* genuinely differs: colour and section headings on the
terminal, a per-source summary dict in the job result.

So the walk lives here and the rendering stays with the caller. Sessions stay
with the caller too, deliberately: the CLI opens one per source so a single bad
source cannot roll back the rest, while the job runs everything in one.

**This changes what the background job does in fixture mode.** It used to call
``process_registry_source`` without a ``fixture_path`` at all, so with
``live=False`` the adapter had nothing to read, logged ``telegram.fixture.missing``
and returned nothing — the web UI's "Собрать источники" button collected zero
Telegram posts unless live was ticked, while the identical CLI command worked.
That is the same CLI-vs-job divergence ``SourceStats`` was introduced to stop,
so the two are made to agree here rather than the discrepancy being preserved.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from court_monitor.config.loader import MonitoringConfig, load_sources
from court_monitor.config.registry import load_registry
from court_monitor.services import SourceStats, process_registry_source, process_source

REPO_ROOT = Path(__file__).resolve().parents[3]

# Saved Telegram previews for the no-network default. Anchored to the repo
# rather than the CWD so a run works from anywhere.
TELEGRAM_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "telegram"


def telegram_fixture_path(entry) -> Path | None:
    """Where a registry entry's saved preview would live, if it has one."""
    if entry.source_type == "telegram":
        return TELEGRAM_FIXTURE_DIR / f"tg_preview_{entry.id}.html"
    return None


@dataclass(frozen=True)
class SourceWork:
    """One source to collect from, ready to run against a caller's session."""

    label: str
    fixture_missing: bool
    run: Callable[[Session], SourceStats]


@dataclass(frozen=True)
class SourceGroup:
    """A named flavour of source, possibly with nothing enabled in it.

    Empty groups are kept rather than filtered out: the CLI prints a heading
    and "нет включённых источников" underneath, and silently dropping the
    section would leave an operator unable to tell "none configured" from
    "this flavour no longer exists".
    """

    title: str
    empty_note: str
    items: list[SourceWork] = field(default_factory=list)


def plan_all_work(monitoring: MonitoringConfig, *, live: bool = False) -> list[SourceGroup]:
    """Everything a full pass would collect, in the order it should be done."""
    return [
        SourceGroup(
            title="Sudrf-источники",
            empty_note="нет включённых источников в config/sources.yaml",
            items=[_sudrf_work(src, monitoring) for src in _cached_sources() if src.enabled],
        ),
        SourceGroup(
            title="Telegram-каналы",
            empty_note="нет включённых каналов в config/source_registry.yaml",
            items=[
                _registry_work(entry, monitoring, live=live)
                for entry in _cached_registry()
                if entry.enabled
            ],
        ),
    ]


@lru_cache(maxsize=1)
def _cached_sources():
    """load_sources() with a per-process cache — the YAML never changes at runtime."""
    return load_sources()


@lru_cache(maxsize=1)
def _cached_registry():
    """load_registry() with a per-process cache — the YAML never changes at runtime."""
    return load_registry()


def clear_source_caches() -> None:
    """Invalidate the source/registry caches (used by tests that monkeypatch)."""
    _cached_sources.cache_clear()
    _cached_registry.cache_clear()


def _sudrf_work(src, monitoring: MonitoringConfig) -> SourceWork:
    # Called positionally rather than bound with keywords: the plan should not
    # depend on what the service happens to name its parameters.
    def _run(session: Session) -> SourceStats:
        return process_source(session, src, monitoring)

    return SourceWork(label=src.name, fixture_missing=False, run=_run)


def _registry_work(entry, monitoring: MonitoringConfig, *, live: bool) -> SourceWork:
    fixture_path = telegram_fixture_path(entry)
    # telegram_fixture_path also returns None for any non-telegram source_type,
    # which _build_registry_adapter does not support at all — only call it a
    # missing fixture when that is actually the reason.
    fixture_missing = (
        not live
        and entry.source_type == "telegram"
        and (fixture_path is None or not fixture_path.exists())
    )

    def _run(session: Session) -> SourceStats:
        return process_registry_source(
            session,
            entry,
            monitoring,
            live=live,
            fixture_path=str(fixture_path) if fixture_path else None,
        )

    return SourceWork(label=entry.id, fixture_missing=fixture_missing, run=_run)
