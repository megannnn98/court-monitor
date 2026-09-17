"""The bulk CLI stages in worker processes: the result must not depend on the worker count."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from operator import itemgetter

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import IVANOV, PETROV, SIDOROV, import_rf_snapshot

import cli_batches
from cli_batches import (
    BatchWorkerError,
    classify_persons,
    extract_articles,
    match_persons,
    merge_extraction_results,
    run_chunks,
    worker_count,
)
from db.maintenance import truncate_disposable_tables
from db.orm_models import (
    EntityMentionRecord,
    ParsedArticleRecord,
    PersecutionClassificationRecord,
    PersonRecord,
    RosfinMatchRecord,
)
from extraction.parallel_resolution import resolve_runs
from extraction.persistence import SqlAlchemyExtractionPersistence
from extraction.person_ner.config import PersonExtractionStrategy, PersonNerSettings
from sources.models import ParsedArticle, RawDocument
from sources.sqlalchemy_persistence import SqlAlchemyIngestionPersistence

TEXTS = [SIDOROV, PETROV, IVANOV]


def _settings(strategy: PersonExtractionStrategy, device: str | None) -> PersonNerSettings:
    return PersonNerSettings(
        strategy=strategy, model_id="m", revision=None, device=device, min_score=0.5
    )


def test_worker_count_is_capped_by_items_cores_and_gpu_memory() -> None:
    assert worker_count(8, 3) == 3
    assert worker_count(64, 10_000) == cli_batches.MAX_WORKERS
    assert worker_count(8, 10_000, uses_gpu=True) == cli_batches.MAX_GPU_WORKERS
    assert worker_count(8, 0) == 1
    with pytest.raises(ValueError, match="greater than zero"):
        worker_count(0, 10)


def test_only_a_model_on_cuda_counts_as_gpu_extraction() -> None:
    rules = _settings(PersonExtractionStrategy.RULE_BASED, "cuda")
    assert cli_batches.extraction_uses_gpu(rules) is False
    assert cli_batches.extraction_uses_gpu(_settings(PersonExtractionStrategy.HYBRID, "cuda:0"))
    assert not cli_batches.extraction_uses_gpu(_settings(PersonExtractionStrategy.NER, "cpu"))


@pytest.mark.parametrize("workers", [1, 3])
def test_every_item_is_processed_once(workers: int) -> None:
    ids = list(range(1, 101))
    progress: list[int] = []

    # `sum` is picklable, so it also runs in spawned workers.
    results = run_chunks("sum", sum, ids, workers=workers, on_progress=progress.append)

    assert sorted(results) == sorted(
        sum(ids[start : start + cli_batches.CHUNK_SIZE])
        for start in range(0, len(ids), cli_batches.CHUNK_SIZE)
    )
    assert sum(progress) == len(ids)


def test_failed_chunks_name_every_unprocessed_id() -> None:
    ids = list(range(1, 31))

    # Indexing past a chunk's end raises in every chunk; each must be awaited and reported.
    past_the_end = itemgetter(cli_batches.CHUNK_SIZE)
    with pytest.raises(BatchWorkerError) as raised:
        run_chunks("index", past_the_end, ids, workers=2)

    assert raised.value.failed_ids == ids


def _save_articles(session_factory: sessionmaker[Session]) -> list[int]:
    persistence = SqlAlchemyIngestionPersistence(
        session_factory=session_factory,
        source_name="ОВД-Инфо",
        source_base_url="https://ovd.info",
    )
    for index, text in enumerate(TEXTS):
        external_id = f"batch-{index}"
        url = f"https://ovd.info/{external_id}"
        persistence.save(
            RawDocument(
                external_id=external_id,
                url=url,
                fetched_at=datetime(2026, 9, 15, tzinfo=UTC),
                content_type="text/html",
                content=text.encode(),
            ),
            ParsedArticle(
                external_id=external_id,
                url=url,
                title=external_id,
                published_at=datetime(2026, 9, 15, tzinfo=UTC),
                text=text,
            ),
        )
    with session_factory() as session:
        return sorted(session.scalars(select(ParsedArticleRecord.id)).all())


def _mentions(session_factory: sessionmaker[Session]) -> list[tuple[str, int, str]]:
    with session_factory() as session:
        rows = session.execute(
            select(
                EntityMentionRecord.entity_type,
                EntityMentionRecord.start_offset,
                EntityMentionRecord.normalized_text,
            )
        ).all()
    return sorted((str(kind), int(start), str(text)) for kind, start, text in rows)


def _extract(session_factory: sessionmaker[Session], database_url: str, workers: int) -> list[int]:
    article_ids = _save_articles(session_factory)
    result = merge_extraction_results(
        run_chunks("extract", partial(extract_articles, database_url), article_ids, workers=workers)
    )
    assert result.articles_failed == 0
    assert result.articles_processed == len(TEXTS)
    return article_ids


def _database_url(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def test_parallel_extraction_finds_what_one_process_finds(
    test_engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    database_url = _database_url(test_engine)

    _extract(session_factory, database_url, workers=1)
    sequential = _mentions(session_factory)
    truncate_disposable_tables(test_engine)
    _extract(session_factory, database_url, workers=3)
    parallel = _mentions(session_factory)

    assert any(kind == "person" for kind, _, _ in sequential)
    assert parallel == sequential


def test_parallel_matching_and_classification_agree_with_one_process(
    test_engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    database_url = _database_url(test_engine)
    article_ids = _extract(session_factory, database_url, workers=1)
    extraction = SqlAlchemyExtractionPersistence(session_factory)
    run_ids = [extraction.get_latest_run_by_article_id(article_id) for article_id in article_ids]
    resolve_runs(database_url, [run_id for run_id in run_ids if run_id is not None], workers=1)
    snapshot_id = import_rf_snapshot(session_factory, [("Иванов Иван", "01.01.1980")])
    with session_factory() as session:
        person_ids = sorted(session.scalars(select(PersonRecord.id)).all())
    assert len(person_ids) >= 3

    def matched(workers: int) -> dict[str, int]:
        work = partial(match_persons, database_url, snapshot_id)
        total: dict[str, int] = {}
        for counts in run_chunks("match", work, person_ids, workers=workers):
            for status, count in counts.items():
                total[status] = total.get(status, 0) + count
        return total

    def classified(workers: int) -> tuple[int, int]:
        work = partial(classify_persons, database_url)
        chunks = run_chunks("classify", work, person_ids, workers=workers)
        return sum(c.classified for c in chunks), sum(c.political for c in chunks)

    def persons_with(
        record: type[RosfinMatchRecord | PersecutionClassificationRecord],
    ) -> list[int]:
        with session_factory() as session:
            return sorted(set(session.scalars(select(record.person_id)).all()))

    sequential_matches = matched(workers=1)
    assert sum(sequential_matches.values()) == len(person_ids)
    # Saved by the worker itself, not returned for the caller to save.
    assert persons_with(RosfinMatchRecord) == person_ids
    assert matched(workers=3) == sequential_matches

    sequential_classes = classified(workers=1)
    assert sequential_classes[0] == len(person_ids)
    assert persons_with(PersecutionClassificationRecord) == person_ids
    assert sequential_classes[1] >= 1
    assert classified(workers=3) == sequential_classes
