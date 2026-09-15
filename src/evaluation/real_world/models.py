"""Real-world corpus manifest: references and hashes, never full article texts."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVALUATION_VERSION = "real-world-v1"
MANIFEST_VERSION = 1

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
# Committed evaluation data: manifest, golden dataset, policy, benchmarks.
DEFAULT_DATA_DIR = REPOSITORY_ROOT / "evaluation" / "real_world"
DEFAULT_MANIFEST_PATH = DEFAULT_DATA_DIR / "corpus_manifest.json"
# Local, git-ignored: raw fetched documents needed to replay the corpus offline.
DEFAULT_CACHE_DIR = REPOSITORY_ROOT / "var" / "real_world"
DEFAULT_REPORT_DIR = REPOSITORY_ROOT / "reports"


class TemporalPeriod(StrEnum):
    """Publication-time split of the raw corpus (never random)."""

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class SourceCorpusStatus(StrEnum):
    OK = "ok"
    # The site answered 403/429 or failed repeatedly: stopped, not bypassed.
    STOPPED = "stopped"
    # Discovery itself failed (network, listing error).
    UNAVAILABLE = "unavailable"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceBuildReport(_Strict):
    """What happened while (re)building one source: infrastructure, not quality."""

    source: str
    status: SourceCorpusStatus
    stop_reason: str | None = None
    discovery_limit: int = 0
    discovered: int = 0
    fetch_candidates: int = 0
    fetched: int = 0
    from_cache: int = 0
    in_period: int = 0
    out_of_period: int = 0
    fetch_failed: int = 0
    parse_failed: int = 0
    selected: int = 0
    http_status_counts: dict[str, int] = Field(default_factory=dict)
    min_interval_seconds: float = 1.0


class ManifestArticle(_Strict):
    source: str
    external_id: str
    canonical_url: str
    published_at: datetime
    # sha256 of the parsed article text (what extraction reads).
    content_hash: str
    # sha256 of the fetched HTML (changes with page chrome; informational).
    raw_content_hash: str
    corpus_split: TemporalPeriod
    sampling_tags: list[str] = Field(default_factory=list)
    evaluation_sample: bool = False
    # Articles that are copies of one story share a group; golden splits keep groups together.
    duplicate_group: str

    @property
    def key(self) -> str:
        return article_key(self.source, self.external_id)


def article_key(source: str, external_id: str) -> str:
    return f"{source}:{external_id}"


class CorpusManifest(_Strict):
    manifest_version: int = MANIFEST_VERSION
    dataset_version: str = EVALUATION_VERSION
    period_start: date
    period_end: date
    sampling_seed: int
    targets: dict[str, int]
    total_target: int
    evaluation_sample_size: int
    built_at: datetime
    sources: list[SourceBuildReport]
    articles: list[ManifestArticle]

    @model_validator(mode="after")
    def _consistent(self) -> CorpusManifest:
        keys = [article.key for article in self.articles]
        if len(keys) != len(set(keys)):
            raise ValueError("manifest articles must be unique by source and external_id")
        for article in self.articles:
            published = article.published_at.date()
            if not self.period_start <= published <= self.period_end:
                raise ValueError(f"{article.key}: published {published} outside the period")
        return self

    def content_fingerprint(self) -> str:
        """Hash of what defines the corpus (not build time or source health)."""
        payload = {
            "manifest_version": self.manifest_version,
            "dataset_version": self.dataset_version,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "sampling_seed": self.sampling_seed,
            "articles": [
                article.model_dump(mode="json")
                for article in sorted(self.articles, key=lambda a: a.key)
            ],
        }
        return sha256_json(payload)

    def by_key(self) -> dict[str, ManifestArticle]:
        return {article.key: article for article in self.articles}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_json(payload: object) -> str:
    return sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> CorpusManifest:
    return CorpusManifest.model_validate_json(path.read_text(encoding="utf-8"))


def write_manifest(manifest: CorpusManifest, path: Path = DEFAULT_MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
