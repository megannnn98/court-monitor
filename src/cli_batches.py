"""The bulk CLI stages — extraction and Rosfinmonitoring matching — in workers.

Each item of these stages is independent of the others: an article is extracted on its
own, a person is matched from their own rows. Unlike person resolution
(`extraction.parallel_resolution`), the result therefore does not depend on the order or
on the number of workers.

Workers are spawned, not forked: a forked child would inherit the parent's database pool
and could not initialize CUDA for the person recognizer. Each worker builds its engine
and its extraction pipeline once and reuses them for every chunk it receives.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from functools import cache
from multiprocessing import get_context

from sqlalchemy.orm import Session, sessionmaker

from db.database import DatabasePoolSettings, create_database_engine, create_session_factory
from extraction.documents import SqlAlchemyExtractionDocumentRepository
from extraction.events import RuleBasedEventExtractor
from extraction.models import BatchExtractionResult, ExtractionRunStatus
from extraction.normalizers import RuleBasedMentionNormalizer
from extraction.persistence import SqlAlchemyExtractionPersistence
from extraction.person_ner.config import PersonExtractionStrategy, PersonNerSettings
from extraction.person_ner.factory import build_entity_extractor
from extraction.pipeline import ExtractionPipeline
from rosfinmonitoring.matcher import RuleBasedRosfinmonitoringMatcher
from rosfinmonitoring.matcher_persistence import RosfinMatchPersistence

logger = logging.getLogger("cli_batches")

DEFAULT_WORKERS = 8
# Every worker holds a connection out of the 100 PostgreSQL allows.
MAX_WORKERS = min(os.cpu_count() or 4, 16)
# A worker running the person recognizer on the GPU holds its own copy of the model, and
# the corpus's longest article needs 4.3 GiB on top of it: four workers ran a 12 GiB
# RTX 3060 out of memory. The card is saturated at two anyway.
MAX_GPU_WORKERS = 2
# Small enough for a smooth progress bar, large enough that pickling is negligible.
CHUNK_SIZE = 25
_WORKER_POOL = DatabasePoolSettings(pool_size=1, max_overflow=0)


class BatchWorkerError(RuntimeError):
    """A chunk failed; its items are named so they can be re-run."""

    def __init__(self, stage: str, failed_ids: list[int], cause: BaseException) -> None:
        super().__init__(f"{stage} failed for ids {failed_ids}: {type(cause).__name__}: {cause}")
        self.failed_ids = failed_ids


def worker_count(workers: int, items: int, *, uses_gpu: bool = False) -> int:
    """Never more workers than items, cores, or — with a model on the GPU — its memory."""
    if workers < 1:
        raise ValueError("workers must be greater than zero")
    cap = MAX_GPU_WORKERS if uses_gpu else MAX_WORKERS
    return max(1, min(workers, items, cap))


def extraction_uses_gpu(settings: PersonNerSettings | None = None) -> bool:
    """Whether the configured person recognizer would load a model onto CUDA."""
    settings = settings or PersonNerSettings.from_env()
    if settings.strategy is PersonExtractionStrategy.RULE_BASED:
        return False
    if settings.device is not None:
        return settings.device.startswith("cuda")
    try:
        import torch
    except ImportError:
        return False
    return bool(torch.cuda.is_available())


def run_chunks[R](
    stage: str,
    work: Callable[[list[int]], R],
    ids: Sequence[int],
    *,
    workers: int,
    on_progress: Callable[[int], None] | None = None,
) -> list[R]:
    """Run `work` over `ids` in chunks, in `workers` spawned processes (1 = in this one).

    `work` must be picklable (a module-level function or a `partial` of one). Every chunk
    is awaited even after a failure, so the error names every id left unprocessed.
    """
    chunks = [list(ids[index : index + CHUNK_SIZE]) for index in range(0, len(ids), CHUNK_SIZE)]
    if workers == 1:
        results = []
        for chunk in chunks:
            results.append(work(chunk))
            if on_progress is not None:
                on_progress(len(chunk))
        return results

    logger.info("event=batch_parallel stage=%s workers=%d items=%d", stage, workers, len(ids))
    results = []
    failed: list[int] = []
    first_error: BaseException | None = None
    with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
        running: dict[Future[R], list[int]] = {pool.submit(work, chunk): chunk for chunk in chunks}
        for future in as_completed(running):
            chunk = running[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - reported per chunk, re-raised below
                logger.error(
                    "event=batch_chunk_failed stage=%s ids=%s error=%s",
                    stage,
                    chunk,
                    type(exc).__name__,
                )
                failed.extend(chunk)
                first_error = first_error or exc
            if on_progress is not None:
                on_progress(len(chunk))
    if first_error is not None:
        raise BatchWorkerError(stage, sorted(failed), first_error) from first_error
    return results


@cache
def _session_factory(database_url: str) -> sessionmaker[Session]:
    # One engine per worker process: connections are never shared across processes.
    return create_session_factory(create_database_engine(database_url, _WORKER_POOL))


@cache
def _extraction_pipeline(database_url: str) -> ExtractionPipeline:
    # Built once per worker: loading the person recognizer takes seconds.
    return ExtractionPipeline(
        extractors=[build_entity_extractor()],
        normalizers=[RuleBasedMentionNormalizer()],
        event_extractor=RuleBasedEventExtractor(),
        persistence=SqlAlchemyExtractionPersistence(_session_factory(database_url)),
    )


def extract_articles(database_url: str, article_ids: list[int]) -> BatchExtractionResult:
    documents = SqlAlchemyExtractionDocumentRepository(_session_factory(database_url))
    pipeline = _extraction_pipeline(database_url)
    result = BatchExtractionResult()
    for article_id in article_ids:
        save_result = pipeline.run(documents.get_by_article_id(article_id))
        if save_result.status is ExtractionRunStatus.SUCCEEDED:
            if save_result.skipped_existing:
                result.articles_skipped += 1
            else:
                result.articles_processed += 1
                result.mentions_created += save_result.mentions_created
                result.events_created += save_result.events_created
        else:
            result.articles_failed += 1
            result.failures.append(f"article_id={article_id}: {save_result.error_message}")
    return result


def merge_extraction_results(results: Sequence[BatchExtractionResult]) -> BatchExtractionResult:
    total = BatchExtractionResult()
    for result in results:
        total.articles_processed += result.articles_processed
        total.articles_skipped += result.articles_skipped
        total.articles_failed += result.articles_failed
        total.mentions_created += result.mentions_created
        total.events_created += result.events_created
        total.failures.extend(result.failures)
    return total


def match_persons(database_url: str, snapshot_id: int, person_ids: list[int]) -> Counter[str]:
    """Match and save each person at once, so a failure loses no finished result."""
    session_factory = _session_factory(database_url)
    matcher = RuleBasedRosfinmonitoringMatcher(session_factory)
    persistence = RosfinMatchPersistence(session_factory)
    statuses: Counter[str] = Counter()
    for person_id in person_ids:
        match_result = matcher.match_person(person_id=person_id, snapshot_id=snapshot_id)
        persistence.save_match_result(match_result)
        statuses[match_result.status.value] += 1
    return statuses
