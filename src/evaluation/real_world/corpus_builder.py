"""Build the real-world corpus through the existing source adapters and parsers.

    discovery (source adapter, polite client)
    → fetch candidates (date hint from the URL when the source has one)
    → parse (source parser) → keep publications inside the period
    → raw cache (local) → deterministic selection → manifest (committed)

Only the manifest (references, hashes, splits, tags) goes to Git; texts stay
in the local cache and are rebuilt from the sites with the same command.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx

from evaluation.real_world.corpus_cache import (
    CacheEntryStatus,
    RawCacheEntry,
    RawCorpusCache,
    article_entry,
)
from evaluation.real_world.models import (
    EVALUATION_VERSION,
    CorpusManifest,
    ManifestArticle,
    SourceBuildReport,
    SourceCorpusStatus,
    article_key,
    sha256_bytes,
    sha256_text,
)
from evaluation.real_world.rate_limit import (
    DomainRateLimiter,
    PoliteTransport,
    SourceHealth,
    SourceStoppedError,
)
from evaluation.real_world.sampling import (
    DatedKey,
    duplicate_groups,
    select_corpus,
    stratified_sample,
    temporal_split,
)
from evaluation.real_world.tags import sampling_tags
from sources.ingestion_errors import (
    DiscoveryError,
    IngestionError,
    ParseError,
    PermanentFetchError,
    TransientFetchError,
)
from sources.models import ParsedArticle, RawDocument, SourceReference
from sources.retrying_fetcher import RetryingDocumentFetcher
from sources.source_registry import SourceDefinition

logger = logging.getLogger("evaluation.real_world")

DEFAULT_PERIOD_START = date(2026, 3, 1)
DEFAULT_PERIOD_END = date(2026, 8, 31)
DEFAULT_SAMPLING_SEED = 20260301
DEFAULT_TARGETS = {"ovd-info": 600, "sota-vision": 400}
DEFAULT_TOTAL_TARGET = 1000
DEFAULT_EVALUATION_SAMPLE_SIZE = 200
DEFAULT_DISCOVERY_LIMIT = 1000
MAX_DISCOVERY_LIMIT = 8000
HTTP_TIMEOUT_SECONDS = 15.0
# Same identification as production monitoring (application.HTTP_USER_AGENT).
HTTP_USER_AGENT = "my-app/1.0"

_URL_DATE = re.compile(r"/(20\d\d)/(\d\d)/(\d\d)/")


def published_date_hint(external_id: str) -> date | None:
    """The date some sources put into the article path (e.g. /express-news/2026/03/01/...)."""
    match = _URL_DATE.search(external_id)
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


@dataclass(frozen=True)
class CorpusBuildConfig:
    period_start: date = DEFAULT_PERIOD_START
    period_end: date = DEFAULT_PERIOD_END
    sampling_seed: int = DEFAULT_SAMPLING_SEED
    targets: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_TARGETS))
    total_target: int = DEFAULT_TOTAL_TARGET
    evaluation_sample_size: int = DEFAULT_EVALUATION_SAMPLE_SIZE
    min_interval_seconds: float = 1.0
    discovery_limit: int = DEFAULT_DISCOVERY_LIMIT
    max_discovery_limit: int = MAX_DISCOVERY_LIMIT
    # Offline: rebuild the manifest from the cache without any request.
    offline: bool = False

    def in_period(self, published: date) -> bool:
        return self.period_start <= published <= self.period_end


class PoliteDocumentFetcher:
    """WebsiteAdapter's error mapping over the shared rate-limited client."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(self, reference: SourceReference) -> RawDocument:
        try:
            response = await self._client.get(reference.url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429 or status_code >= 500:
                raise TransientFetchError(
                    f"Temporary failure fetching {reference.url}: HTTP {status_code}"
                ) from exc
            raise PermanentFetchError(
                f"Failed to fetch {reference.url}: HTTP {status_code}"
            ) from exc
        except httpx.TransportError as exc:
            raise TransientFetchError(f"Temporary failure fetching {reference.url}") from exc
        return RawDocument(
            external_id=reference.external_id,
            url=str(response.url),
            fetched_at=datetime.now(tz=UTC),
            content_type=response.headers.get("content-type", ""),
            content=response.content,
        )


class RealWorldCorpusBuilder:
    def __init__(
        self,
        *,
        sources: Mapping[str, SourceDefinition],
        cache: RawCorpusCache,
        config: CorpusBuildConfig,
        create_transport: Callable[[], httpx.AsyncBaseTransport] = httpx.AsyncHTTPTransport,
        limiter: DomainRateLimiter | None = None,
        retry_base_delay_seconds: float = 0.5,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._sources = sources
        self._cache = cache
        self._config = config
        self._create_transport = create_transport
        self._limiter = limiter or DomainRateLimiter(config.min_interval_seconds)
        self._health = SourceHealth(self._limiter)
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._now = now

    async def build(self) -> CorpusManifest:
        reports: dict[str, SourceBuildReport] = {}
        entries: dict[str, dict[str, RawCacheEntry]] = {}
        for name in sorted(self._sources):
            if self._config.offline:
                entries[name] = self._cache.entries(name)
                reports[name] = SourceBuildReport(
                    source=name,
                    status=SourceCorpusStatus.OK,
                    from_cache=len(entries[name]),
                    min_interval_seconds=self._config.min_interval_seconds,
                )
                continue
            reports[name], entries[name] = await self._build_source(self._sources[name])
        return self._manifest(reports, entries)

    async def reconstruct(self, manifest: CorpusManifest) -> list[SourceBuildReport]:
        """Fetch manifest articles missing from the cache by URL (no discovery).

        Listings move on; the manifest URLs do not. Content drift is visible in
        `real-world-corpus-status` (parsed text hash differs from the manifest).
        """
        reports = []
        for name in sorted({article.source for article in manifest.articles}):
            source = self._sources[name]
            cached = self._cache.entries(name)
            wanted = [
                article
                for article in manifest.articles
                if article.source == name and article.external_id not in cached
            ]
            fetched = failed = 0
            status, stop_reason = SourceCorpusStatus.OK, None
            transport = PoliteTransport(self._create_transport(), self._limiter, self._health)
            async with httpx.AsyncClient(
                transport=transport,
                timeout=HTTP_TIMEOUT_SECONDS,
                headers={"User-Agent": HTTP_USER_AGENT},
            ) as client:
                fetcher = RetryingDocumentFetcher(
                    PoliteDocumentFetcher(client),
                    max_attempts=3,
                    base_delay_seconds=self._retry_base_delay_seconds,
                )
                parser = source.create_parser()
                for article in wanted:
                    reference = SourceReference(
                        external_id=article.external_id, url=article.canonical_url
                    )
                    try:
                        raw = await fetcher.fetch(reference)
                    except SourceStoppedError as exc:
                        status, stop_reason = SourceCorpusStatus.STOPPED, exc.reason
                        break
                    except IngestionError:
                        failed += 1
                        continue
                    fetched += 1
                    self._cache.append(self._cache_entry(name, raw, parser.parse))
            host = httpx.URL(source.base_url).host
            reports.append(
                SourceBuildReport(
                    source=name,
                    status=status,
                    stop_reason=stop_reason,
                    fetch_candidates=len(wanted),
                    fetched=fetched,
                    fetch_failed=failed,
                    from_cache=sum(1 for a in manifest.articles if a.source == name) - len(wanted),
                    http_status_counts=dict(sorted(self._health.host(host).status_counts.items())),
                    min_interval_seconds=self._limiter.interval(host),
                )
            )
        return reports

    # -- network ---------------------------------------------------------------------------

    async def _build_source(
        self, source: SourceDefinition
    ) -> tuple[SourceBuildReport, dict[str, RawCacheEntry]]:
        config = self._config
        cached = self._cache.entries(source.name)
        counters: dict[str, int] = {
            "fetched": 0,
            "from_cache": 0,
            "fetch_failed": 0,
        }
        status = SourceCorpusStatus.OK
        stop_reason: str | None = None
        references: list[SourceReference] = []
        limit = config.discovery_limit
        transport = PoliteTransport(self._create_transport(), self._limiter, self._health)
        async with httpx.AsyncClient(
            transport=transport,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": HTTP_USER_AGENT},
        ) as client:
            fetcher = RetryingDocumentFetcher(
                PoliteDocumentFetcher(client),
                max_attempts=3,
                base_delay_seconds=self._retry_base_delay_seconds,
            )
            adapter = source.create_adapter(client, fetcher)
            try:
                references = await self._discover(adapter.discover, limit)
                limit = max(limit, len(references))
            except SourceStoppedError as exc:
                status, stop_reason = SourceCorpusStatus.STOPPED, exc.reason
            except DiscoveryError as exc:
                status, stop_reason = SourceCorpusStatus.UNAVAILABLE, str(exc)
            candidates = [ref for ref in references if self._worth_fetching(ref)]
            logger.info(
                "event=real_world_discovery source=%s discovered=%d candidates=%d",
                source.name,
                len(references),
                len(candidates),
            )
            parser = source.create_parser()
            for reference in candidates if status is SourceCorpusStatus.OK else []:
                if reference.external_id in cached:
                    counters["from_cache"] += 1
                    continue
                try:
                    raw = await fetcher.fetch(reference)
                except SourceStoppedError as exc:
                    status, stop_reason = SourceCorpusStatus.STOPPED, exc.reason
                    break
                except IngestionError as exc:
                    counters["fetch_failed"] += 1
                    logger.warning(
                        "event=real_world_fetch_failed source=%s url=%s error=%s",
                        source.name,
                        reference.url,
                        exc,
                    )
                    continue
                counters["fetched"] += 1
                entry = self._cache_entry(source.name, raw, parser.parse)
                self._cache.append(entry)
                cached[entry.external_id] = entry
        in_period = sum(1 for e in cached.values() if e.status is CacheEntryStatus.ARTICLE)
        host = httpx.URL(source.base_url).host
        report = SourceBuildReport(
            source=source.name,
            status=status,
            stop_reason=stop_reason,
            discovery_limit=limit,
            discovered=len(references),
            fetch_candidates=len(candidates),
            in_period=in_period,
            out_of_period=sum(
                1 for e in cached.values() if e.status is CacheEntryStatus.OUT_OF_PERIOD
            ),
            fetched=counters["fetched"],
            from_cache=counters["from_cache"],
            fetch_failed=counters["fetch_failed"],
            parse_failed=sum(
                1 for e in cached.values() if e.status is CacheEntryStatus.PARSE_FAILED
            ),
            http_status_counts=dict(sorted(self._health.host(host).status_counts.items())),
            min_interval_seconds=self._limiter.interval(host),
        )
        return report, cached

    async def _discover(
        self, discover: Callable[..., Awaitable[list[SourceReference]]], limit: int
    ) -> list[SourceReference]:
        """Grow the discovery limit until the listing passes the period start or ends.

        Sources without a date in their URLs are exhausted by the first call or
        by the maximum limit: only fetched documents tell their dates.
        """
        config = self._config
        while True:
            references = await discover(limit=limit)
            hints = [h for ref in references if (h := published_date_hint(ref.external_id))]
            exhausted = len(references) < limit
            passed_start = bool(hints) and min(hints) < config.period_start
            if exhausted or passed_start or limit >= config.max_discovery_limit:
                return references
            limit = min(limit * 2, config.max_discovery_limit)

    def _worth_fetching(self, reference: SourceReference) -> bool:
        hint = published_date_hint(reference.external_id)
        if hint is None:
            return True
        margin = timedelta(days=1)
        return self._config.period_start - margin <= hint <= self._config.period_end + margin

    def _cache_entry(
        self, source: str, raw: RawDocument, parse: Callable[[RawDocument], ParsedArticle]
    ) -> RawCacheEntry:
        try:
            article = parse(raw)
        except ParseError as exc:
            return article_entry(
                source, raw, status=CacheEntryStatus.PARSE_FAILED, published_at=None
            ).model_copy(update={"error": str(exc)})
        published = article.published_at
        status = (
            CacheEntryStatus.ARTICLE
            if published is not None and self._config.in_period(published.date())
            else CacheEntryStatus.OUT_OF_PERIOD
        )
        return article_entry(source, raw, status=status, published_at=published)

    # -- manifest --------------------------------------------------------------------------

    def _manifest(
        self,
        reports: Mapping[str, SourceBuildReport],
        entries: Mapping[str, Mapping[str, RawCacheEntry]],
    ) -> CorpusManifest:
        config = self._config
        parsed: dict[str, tuple[RawCacheEntry, ParsedArticle]] = {}
        for name, source_entries in entries.items():
            parser = self._sources[name].create_parser()
            for entry in source_entries.values():
                if entry.status is not CacheEntryStatus.ARTICLE:
                    continue
                article = parser.parse(entry.raw_document())
                if article.published_at is None or not config.in_period(
                    article.published_at.date()
                ):
                    continue
                parsed[article_key(name, entry.external_id)] = (entry, article)

        available = {
            name: sorted(key for key in parsed if key.startswith(f"{name}:"))
            for name in sorted(entries)
        }
        selected_by_source = select_corpus(
            available, config.targets, config.total_target, config.sampling_seed
        )
        selected = sorted(key for keys in selected_by_source.values() for key in keys)

        split = temporal_split([DatedKey(key, _published_at(parsed[key][1])) for key in selected])
        tags = {key: sampling_tags(parsed[key][1].title, parsed[key][1].text) for key in selected}
        groups = duplicate_groups({key: parsed[key][1].text for key in selected})
        sample = set(stratified_sample(tags, config.evaluation_sample_size, config.sampling_seed))

        articles = []
        for key in selected:
            entry, article = parsed[key]
            published_at = _published_at(article)
            articles.append(
                ManifestArticle(
                    source=entry.source,
                    external_id=entry.external_id,
                    canonical_url=entry.url,
                    published_at=published_at,
                    content_hash=sha256_text(article.text),
                    raw_content_hash=sha256_bytes(entry.raw_document().content),
                    corpus_split=split[key],
                    sampling_tags=tags[key],
                    evaluation_sample=key in sample,
                    duplicate_group=groups[key],
                )
            )
        final_reports = [
            report.model_copy(update={"selected": len(selected_by_source.get(name, []))})
            for name, report in sorted(reports.items())
        ]
        return CorpusManifest(
            dataset_version=EVALUATION_VERSION,
            period_start=config.period_start,
            period_end=config.period_end,
            sampling_seed=config.sampling_seed,
            targets=dict(config.targets),
            total_target=config.total_target,
            evaluation_sample_size=config.evaluation_sample_size,
            built_at=self._now(),
            sources=final_reports,
            articles=sorted(articles, key=lambda a: (a.published_at, a.key)),
        )


def _published_at(article: ParsedArticle) -> datetime:
    if article.published_at is None:
        raise ValueError(f"{article.external_id} has no publication time")
    return article.published_at


def parse_targets(values: Sequence[str]) -> dict[str, int]:
    """`ovd-info=600` pairs from the command line."""
    targets: dict[str, int] = {}
    for value in values:
        name, _, count = value.partition("=")
        if not name or not count.isdigit():
            raise ValueError(f"target must look like source=count, got {value!r}")
        targets[name] = int(count)
    return targets
