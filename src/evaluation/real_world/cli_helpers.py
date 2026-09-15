"""Shared real-world validation CLI helpers."""

from __future__ import annotations

from evaluation.real_world.corpus_cache import RawCorpusCache
from evaluation.real_world.models import CorpusManifest
from evaluation.real_world.replay import load_replay_entries
from sources.source_registry import SOURCES

# Documented exit codes (docs/wiki/RealWorldValidation.md).
EXIT_OK = 0
EXIT_QUALITY_FAILURE = 1
EXIT_INFRASTRUCTURE_ERROR = 2


def corpus_texts(cache: RawCorpusCache, manifest: CorpusManifest) -> dict[str, str]:
    """Parsed texts of the manifest articles from the local cache (for offset checks)."""
    entries, _ = load_replay_entries(cache, manifest.articles, SOURCES)
    texts: dict[str, str] = {}
    for article in manifest.articles:
        raw = entries[article.source][article.external_id].raw_document()
        texts[article.key] = SOURCES[article.source].create_parser().parse(raw).text
    return texts
