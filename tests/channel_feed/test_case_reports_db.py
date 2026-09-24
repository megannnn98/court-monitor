"""Read-side selection must keep unnamed events and ignore superseded extraction runs."""

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session, sessionmaker
from support.research_db_fixtures import ResearchSeeder

from channel_feed.case_age import CaseAge
from channel_feed.case_reports import load_case_reports
from db.orm_models import ArticleExtractionRunRecord


def test_event_reports_need_no_person_or_snapshot_and_use_latest_run(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("test", "https://example.test")
        quote = "Сегодня против жителя возбудили уголовное дело по ст. 275 УК РФ."
        article, run = seed.article(
            source,
            external_id="unnamed",
            title="Без имени",
            text=quote,
            published_at=datetime(2026, 9, 23, tzinfo=UTC),
        )
        old_event = seed.event(run, quote, event_type="case_opened", event_date=None, links=[])
        latest = ArticleExtractionRunRecord(
            article_id=article,
            article_content_hash="new",
            extractor_name="test",
            extractor_version="2",
            normalizer_version="2",
            status="succeeded",
            started_at=datetime(2026, 9, 23, tzinfo=UTC),
        )
        session.add(latest)
        session.flush()
        # Same sentence and offsets, but only the newest successful run is authoritative.
        from db.orm_models import ExtractedEventRecord

        new_event = ExtractedEventRecord(
            extraction_run_id=latest.id,
            event_type="case_opened",
            event_date=None,
            start_offset=0,
            end_offset=len(quote),
            confidence=0.9,
            attributes={},
            extractor_name="test",
            extractor_version="2",
        )
        session.add(new_event)
        session.flush()
        reports = load_case_reports(
            session,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 9, 23),
        )
        assert len(reports) == 1
        assert reports[0].event_id == new_event.id != old_event
        assert reports[0].names == ()
        assert reports[0].timing.age is CaseAge.NEW


def test_filters_follow_event_targets_and_local_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        seed = ResearchSeeder(session)
        source = seed.source("test", "https://example.test")
        text = (
            "В январе 2026 года против Ивана Петрова возбудили дело по ст. 275 УК РФ. "
            "Сегодня против жителя возбудили дело по ст. 275 УК РФ. "
            "Соседа оштрафовали за побои."
        )
        _, run = seed.article(
            source,
            external_id="digest",
            title="Сводка",
            text=text,
            published_at=datetime(2026, 9, 23, tzinfo=UTC),
        )
        mention = seed.mention(run, "Ивана Петрова", person_id=None)
        old = seed.event(
            run,
            text[: text.index("Сегодня")].strip(),
            event_type="case_opened",
            event_date=None,
            links=[],
            entity_links=[(mention, "target")],
        )
        new = seed.event(
            run,
            "Сегодня против жителя возбудили дело по ст. 275 УК РФ.",
            event_type="case_opened",
            event_date=None,
            links=[],
        )
        seed.event(
            run,
            "Соседа оштрафовали за побои.",
            event_type="fine",
            event_date=None,
            links=[],
        )
        reports = load_case_reports(
            session, period_start=date(2026, 8, 1), period_end=date(2026, 9, 23)
        )
        assert [report.event_id for report in reports] == [new, old]
        assert reports[1].names == ("ивана петрова",)
        assert [
            report.event_id
            for report in load_case_reports(
                session,
                unnamed_only=True,
                period_start=date(2026, 8, 1),
                period_end=date(2026, 9, 23),
            )
        ] == [new]
        assert [
            report.event_id
            for report in load_case_reports(
                session,
                age=CaseAge.UPDATE,
                period_start=date(2026, 8, 1),
                period_end=date(2026, 9, 23),
            )
        ] == [old]
