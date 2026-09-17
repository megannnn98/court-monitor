"""RV4 scenarios on a small replayed corpus against real PostgreSQL (no Internet).

The corpus is written as OVD-Info HTML into a temporary raw cache with a
manifest, so the production parser, monitoring stages and derived stages run
exactly as in `evaluate-real-world --full`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from db.maintenance import truncate_disposable_tables
from evaluation.real_world.corpus_cache import CacheEntryStatus, RawCorpusCache, article_entry
from evaluation.real_world.corpus_run import CorpusRunner, RfSnapshotFile, articles_by_period
from evaluation.real_world.evaluator import IN_MEMORY_QDRANT, REAL_WORLD_COLLECTIONS
from evaluation.real_world.models import (
    CorpusManifest,
    ManifestArticle,
    TemporalPeriod,
    sha256_bytes,
    sha256_text,
)
from evaluation.real_world.monitoring_simulation import (
    TokenHashEmbedder,
    crash_recovery,
    manual_review_continuation,
    no_rf_snapshot,
    postgres_interruption,
    qdrant_outage,
    rf_review_findings,
    run_temporal_simulation,
    semantic_indexer_factory,
    source_failures,
    together_failure,
)
from evaluation.real_world.results import GateStatus
from evaluation.real_world.state_snapshot import check_invariants
from sources.models import RawDocument
from sources.source_registry import OVD_INFO

STORIES = [
    (
        "Иван Сидоров, известный правозащитник, задержан на антивоенном митинге. "
        "Активисты считают дело политически мотивированным."
    ),
    "Суд арестовал активиста Олега Кузнецова по делу о дискредитации армии.",
    "Полиция задержала Петра Петрова за мелкое хулиганство. Составлен протокол по КоАП.",
    "Против журналистки Анны Смирновой возбудили дело о «фейках» об армии.",
    "Суд оштрафовал Игоря Лебедева за антивоенный пикет.",
    "Анну Смирнову отпустили под подписку о невыезде.",
    "Иван Сидоров снова задержан на антивоенном митинге.",
    # The second sentence has no case event: that name-only match of a T0 person stays in
    # review, which the manual-review scenario needs (a repeat inside a case event links
    # by the case context).
    (
        "Активиста Максима Орлова приговорили к штрафу по делу о дискредитации армии. "
        "Иван Сидоров рассказал о давлении после митинга."
    ),
    "Суд арестовал Олега Кузнецова по делу о дискредитации армии.",
    "Жительницу Казани Марину Волкову задержали за пикет против войны.",
]
RF_CSV = "full_name,birth_date,inclusion_reason\nКУЗНЕЦОВ ОЛЕГ,01.01.1980,test\n"


def _html(title: str, published: datetime, text: str) -> bytes:
    return (
        f'<html><body><h1 class="express-text-heading">{title}</h1>'
        f'<div id="article_published">{published:%d.%m.%Y, %H:%M}</div>'
        f'<div class="field--name-field-express-text"><p>{text}</p></div></body></html>'
    ).encode()


def build_corpus(
    directory: Path, source: str = "ovd-info"
) -> tuple[CorpusManifest, RawCorpusCache]:
    cache = RawCorpusCache(directory)
    splits = [TemporalPeriod.T0] * 7 + [TemporalPeriod.T1, TemporalPeriod.T2, TemporalPeriod.T3]
    articles = []
    for index, (text, split) in enumerate(zip(STORIES, splits, strict=True)):
        published = datetime(2026, 3, 1, 12, tzinfo=UTC) + timedelta(days=index)
        external_id = f"/express-news/{published:%Y/%m/%d}/story-{index}"
        raw = RawDocument(
            external_id=external_id,
            url=f"https://ovd.info{external_id}",
            fetched_at=published,
            content_type="text/html",
            content=_html(f"Новость {index}", published, text),
        )
        cache.append(
            article_entry(source, raw, status=CacheEntryStatus.ARTICLE, published_at=published)
        )
        articles.append(
            ManifestArticle(
                source=source,
                external_id=external_id,
                canonical_url=raw.url,
                published_at=published,
                content_hash=sha256_text(text),
                raw_content_hash=sha256_bytes(raw.content),
                corpus_split=split,
                duplicate_group=external_id,
            )
        )
    manifest = CorpusManifest(
        period_start=date(2026, 3, 1),
        period_end=date(2026, 8, 31),
        sampling_seed=1,
        targets={"ovd-info": 10},
        total_target=10,
        evaluation_sample_size=0,
        built_at=datetime(2026, 9, 1, tzinfo=UTC),
        sources=[],
        articles=articles,
    )
    return manifest, cache


@pytest.fixture
def runner(test_engine: Engine, tmp_path: Path) -> Iterator[CorpusRunner]:
    truncate_disposable_tables(test_engine)
    manifest, cache = build_corpus(tmp_path / "cache")
    yield CorpusRunner(
        engine=test_engine,
        manifest=manifest,
        cache=cache,
        definitions={OVD_INFO.name: OVD_INFO},
    )
    truncate_disposable_tables(test_engine)


@pytest.fixture
def rf_snapshot(tmp_path: Path) -> RfSnapshotFile:
    path = tmp_path / "rf.csv"
    path.write_text(RF_CSV, encoding="utf-8")
    return RfSnapshotFile(snapshot_id="rf-test", path=path)


def _memory_indexer(runner: CorpusRunner) -> None:
    runner.create_semantic_indexer = semantic_indexer_factory(
        runner.session_factory, IN_MEMORY_QDRANT, REAL_WORLD_COLLECTIONS
    )
    runner.rebuild_service()


def test_temporal_simulation_rerun_adds_nothing_and_keeps_invariants(
    runner: CorpusRunner, rf_snapshot: RfSnapshotFile
) -> None:
    _memory_indexer(runner)
    outcome = run_temporal_simulation(runner, rf_snapshot, rerun=True)

    assert [p.period for p in outcome.periods] == ["T0", "T1", "T2", "T3"]
    assert [p.new["source_documents"] for p in outcome.periods] == [7, 1, 1, 1]
    assert all(status == "completed" for p in outcome.periods for status in p.run_status.values())
    assert sum(outcome.rerun_duplicates.values()) == 0, outcome.rerun_duplicates
    assert outcome.periods[0].new["semantic_indexed"] > 0
    assert set(check_invariants(runner.engine).values()) == {0}
    assert rf_review_findings(runner.session_factory, runner.snapshot_id).status is GateStatus.PASS


def test_crash_after_each_stage_recovers_the_uninterrupted_state(
    runner: CorpusRunner, rf_snapshot: RfSnapshotFile
) -> None:
    periods = articles_by_period(runner.manifest)
    results = crash_recovery(
        runner,
        rf_snapshot,
        periods[TemporalPeriod.T0],
        [*periods[TemporalPeriod.T1], *periods[TemporalPeriod.T2]],
        semantic=lambda: semantic_indexer_factory(
            runner.session_factory, IN_MEMORY_QDRANT, REAL_WORLD_COLLECTIONS
        ),
    )
    assert {r.name: r.status for r in results} == {
        f"crash_after_{stage}": GateStatus.PASS
        for stage in (
            "ingestion",
            "extraction",
            "entity_resolution",
            "classification",
            "semantic_indexing",
        )
    }, [r.detail for r in results]


def test_qdrant_outage_keeps_domain_state_and_derived_retry_repairs_index(
    runner: CorpusRunner, rf_snapshot: RfSnapshotFile
) -> None:
    result = qdrant_outage(
        runner,
        rf_snapshot,
        articles_by_period(runner.manifest)[TemporalPeriod.T0],
        semantic_indexer_factory(
            runner.session_factory, IN_MEMORY_QDRANT, REAL_WORLD_COLLECTIONS, TokenHashEmbedder()
        ),
        REAL_WORLD_COLLECTIONS,
    )
    assert result.status is GateStatus.PASS, result.detail


def test_source_and_database_failures_stay_isolated(
    runner: CorpusRunner, rf_snapshot: RfSnapshotFile
) -> None:
    articles = articles_by_period(runner.manifest)[TemporalPeriod.T0]
    sources = source_failures(runner, rf_snapshot, articles)
    assert sources.status is GateStatus.PASS, sources.detail
    database = postgres_interruption(runner, rf_snapshot, articles)
    assert database.status is GateStatus.PASS, database.detail


def test_together_failure_and_missing_rf_snapshot(runner: CorpusRunner) -> None:
    missing = no_rf_snapshot(runner)
    assert missing.status is GateStatus.PASS, missing.detail
    assert isinstance(missing.metrics["political_persons"], int)
    assert missing.metrics["political_persons"] > 0
    together = together_failure(runner)
    assert together.status is GateStatus.PASS, together.detail


def test_manual_review_decision_continues_through_derived_processing(
    runner: CorpusRunner, rf_snapshot: RfSnapshotFile
) -> None:
    _memory_indexer(runner)
    run_temporal_simulation(runner, rf_snapshot, rerun=False)
    result = manual_review_continuation(runner, has_semantic=True)
    assert result.status is GateStatus.PASS, result.detail


def test_crash_is_injected_into_the_source_that_processes_the_increment(
    test_engine: Engine, tmp_path: Path, rf_snapshot: RfSnapshotFile
) -> None:
    """External review: the crash fired on the first source run of the period even when
    that source had no increment articles, so recovery could PASS without being tested.

    "ovd-info" runs first and only has a baseline article; the increment is on "sota-vision"
    (replayed with the OVD-Info parser).
    """
    from dataclasses import replace

    truncate_disposable_tables(test_engine)
    manifest, cache = build_corpus(tmp_path / "cache", source="sota-vision")
    published = datetime(2026, 3, 2, 18, tzinfo=UTC)
    external_id = "/express-news/2026/03/02/archive"
    text = "Суд оштрафовал Павла Зайцева за пикет против войны."
    raw = RawDocument(
        external_id=external_id,
        url=f"https://ovd.info{external_id}",
        fetched_at=published,
        content_type="text/html",
        content=_html("Архив", published, text),
    )
    cache.append(
        article_entry("ovd-info", raw, status=CacheEntryStatus.ARTICLE, published_at=published)
    )
    archive = ManifestArticle(
        source="ovd-info",
        external_id=external_id,
        canonical_url=raw.url,
        published_at=published,
        content_hash=sha256_text(text),
        raw_content_hash=sha256_bytes(raw.content),
        corpus_split=TemporalPeriod.T0,
        duplicate_group=external_id,
    )
    manifest = manifest.model_copy(update={"articles": [*manifest.articles, archive]})
    runner = CorpusRunner(
        engine=test_engine,
        manifest=manifest,
        cache=cache,
        definitions={
            OVD_INFO.name: OVD_INFO,
            "sota-vision": replace(
                OVD_INFO, name="sota-vision", source_name="SOTA", base_url="https://sota.vision"
            ),
        },
    )
    periods = articles_by_period(manifest)
    try:
        results = crash_recovery(
            runner,
            rf_snapshot,
            periods[TemporalPeriod.T0],
            [*periods[TemporalPeriod.T1], *periods[TemporalPeriod.T2]],
            semantic=lambda: semantic_indexer_factory(
                runner.session_factory, IN_MEMORY_QDRANT, REAL_WORLD_COLLECTIONS
            ),
        )
    finally:
        truncate_disposable_tables(test_engine)
    for result in results:
        assert result.status is GateStatus.PASS, result.detail
        assert result.metrics["crashed_source"] == "sota-vision", result.metrics
        work = result.metrics["crashed_stage_work"]
        assert isinstance(work, int) and work > 0, result.metrics
