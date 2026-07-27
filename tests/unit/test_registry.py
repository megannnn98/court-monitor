"""Tests: source registry normalization, dedup, idempotent import."""

from __future__ import annotations

from court_monitor.config.registry import (
    SourceRegistryEntry,
    build_entry,
    load_registry,
    make_stable_id,
    merge_registry,
    normalize_rows,
    save_registry,
)
from court_monitor.normalization import canonicalize_url, url_domain
from court_monitor.sources.airtable_registry import RawRegistryRow


def test_url_normalization_basic() -> None:
    assert canonicalize_url("HTTPS://T.me/Test/") == "https://t.me/Test"
    assert canonicalize_url("t.me/foo") == "https://t.me/foo"
    assert canonicalize_url("  https://x.io/a?utm_source=q&keep=1#f  ") == (
        "https://x.io/a?keep=1"
    )


def test_url_normalization_drops_tracking_params() -> None:
    a = canonicalize_url("https://ex.org/p?fbclid=abc&id=42")
    assert a == "https://ex.org/p?id=42"


def test_url_normalization_returns_none_for_garbage() -> None:
    assert canonicalize_url(None) is None
    assert canonicalize_url("") is None
    assert canonicalize_url("   ") is None
    assert url_domain(None) is None


def test_stable_id_prefers_username() -> None:
    assert make_stable_id(username="@Foo", url="https://t.me/foo", name="X") == "foo"
    assert make_stable_id(username=None, url="https://example.org", name=None) == "example-org"


def test_build_entry_fills_url_from_username_when_missing() -> None:
    entry = build_entry(RawRegistryRow(name="N", username="abc", url=None, topics=[], fields={}))
    assert entry is not None
    assert entry.url == "https://t.me/abc"
    assert entry.source_type == "telegram"
    assert entry.domain == "t.me"


def test_row_without_url_or_username_is_dropped() -> None:
    entries, stats = normalize_rows(
        [RawRegistryRow(name="Lonely", username=None, url=None, topics=[], fields={})]
    )
    assert entries == []
    assert stats.without_url == 1
    assert stats.recognized == 0


def test_duplicates_within_source_are_collapsed() -> None:
    rows = [
        RawRegistryRow(name="A", username="aaa", url="https://t.me/aaa", topics=[], fields={}),
        RawRegistryRow(name="A again", username="aaa", url="https://t.me/aaa", topics=[], fields={}),
    ]
    entries, stats = normalize_rows(rows)
    assert len(entries) == 1
    assert stats.duplicates == 1


def test_idempotent_reimport_changes_nothing() -> None:
    rows = [
        RawRegistryRow(name="A", username="aaa", url="https://t.me/aaa", topics=["Новости"], fields={}),
    ]
    entries, stats = normalize_rows(rows)
    merged, preview1 = merge_registry([], entries, stats=stats)
    assert preview1.new_sources == 1
    merged2, preview2 = merge_registry(merged, entries, stats=stats)
    assert preview2.new_sources == 0
    assert preview2.changed_sources == 0
    assert preview2.duplicates == 1
    assert len(merged2) == len(merged)


def test_changed_source_detected_on_diff() -> None:
    entries, stats = normalize_rows(
        [RawRegistryRow(name="A", username="aaa", url="https://t.me/aaa", topics=["Новости"], fields={})]
    )
    merged, _ = merge_registry([], entries, stats=stats)
    changed = [
        SourceRegistryEntry(
            id="aaa",
            name="A (новое имя)",
            url="https://t.me/aaa",
            domain="t.me",
            source_type="telegram",
            username="aaa",
            topics=["Новости"],
        )
    ]
    _, preview = merge_registry(merged, changed, stats=normalize_rows(changed)[1])
    assert preview.changed_sources == 1


def test_load_registry_roundtrip(tmp_path) -> None:
    entries, _ = normalize_rows(
        [
            RawRegistryRow(name="A", username="aaa", url="https://t.me/aaa", topics=["Новости"], fields={}),
            RawRegistryRow(name="B", username="bbb", url="https://t.me/bbb", topics=[], fields={}),
        ]
    )
    p = tmp_path / "registry.yaml"
    save_registry(entries, p)
    loaded = load_registry(p)
    assert {e.id for e in loaded} == {"aaa", "bbb"}
