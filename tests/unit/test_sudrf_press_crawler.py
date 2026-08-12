"""Tests for the sudrf press crawler using real HTML fixtures."""

from __future__ import annotations

from pathlib import Path

from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import SourceBackend, SourceType
from court_monitor.parsers.sudrf_press import parse_press_release
from court_monitor.sources.sudrf import SudrfPressCrawler

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sudrf-live" / "2zovs" / "press"


def _make_config(*, fixture_path: str | None = None) -> SourceConfig:
    return SourceConfig(
        name="2zovs",
        type=SourceType.sudrf,
        backend=SourceBackend.fixture,
        base_url="https://2zovs.msk.sudrf.ru",
        fixture_path=fixture_path or str(FIXTURE_DIR),
        court_name="2-й Западный окружной военный суд",
        press_module="press_dep",
    )


class TestSudrfPressCrawlerFixture:
    def test_parses_individual_release(self) -> None:
        config = _make_config()
        crawler = SudrfPressCrawler(config)

        release_file = FIXTURE_DIR / "release-234-razlugo.html"
        content = release_file.read_text(encoding="utf-8")

        result = crawler._parse_release(
            content, "https://2zovs.msk.sudrf.ru/modules.php?name=press_dep&op=1&did=234", did="234"
        )

        assert result is not None
        assert result.external_id == "234"
        assert result.source_name == "2zovs"
        assert result.source_id == "2zovs"
        assert result.title is not None
        assert "Брянск" in result.title or "финансирование" in result.title
        assert result.published_at is not None
        assert result.published_at.year == 2026
        assert result.published_at.month == 4
        assert result.published_at.day == 2
        assert "Разлуго" in result.text
        assert "205" in result.text

    def test_extracts_publication_links_from_archive(self) -> None:
        config = _make_config()
        crawler = SudrfPressCrawler(config)

        archive_file = FIXTURE_DIR / "archive-2026-04.html"
        content = archive_file.read_text(encoding="utf-8")

        links = crawler._extract_publication_links(content)

        assert len(links) > 0
        dids = [did for did, _ in links]
        assert "234" in dids
        assert "235" in dids
        assert "233" in dids
        assert "231" in dids

    def test_extracts_archive_months(self) -> None:
        config = _make_config()
        crawler = SudrfPressCrawler(config)

        archive_file = FIXTURE_DIR / "archive-2026-04.html"
        content = archive_file.read_text(encoding="utf-8")

        months = crawler._extract_archive_months(content)

        assert "2026-04" in months
        assert "2026-03" in months
        assert "2025-12" in months

    def test_detects_archive_page(self) -> None:
        config = _make_config()
        crawler = SudrfPressCrawler(config)

        archive_file = FIXTURE_DIR / "archive-2026-04.html"
        release_file = FIXTURE_DIR / "release-234-razlugo.html"

        assert crawler._is_archive_page(archive_file.read_text(encoding="utf-8"))
        assert not crawler._is_archive_page(release_file.read_text(encoding="utf-8"))

    def test_fixture_backend_yields_releases(self) -> None:
        config = _make_config()
        crawler = SudrfPressCrawler(config)

        results = list(crawler.fetch_new())

        assert len(results) >= 4
        external_ids = [r.external_id for r in results if hasattr(r, "external_id")]
        assert "234" in external_ids
        assert "235" in external_ids

    def test_incremental_fetch_skips_known_ids(self) -> None:
        config = _make_config()
        known_ids = {"234", "235"}
        crawler = SudrfPressCrawler(config, known_ids=known_ids)

        results = list(crawler.fetch_new())

        fetched_dids = [
            r.external_id for r in results if hasattr(r, "external_id") and r.external_id
        ]
        assert "234" not in fetched_dids
        assert "235" not in fetched_dids
        assert "233" in fetched_dids or "231" in fetched_dids

    def test_full_rescan_ignores_known_ids(self) -> None:
        config = _make_config()
        known_ids = {"234", "235"}
        crawler = SudrfPressCrawler(config, known_ids=known_ids, full_rescan=True)

        results = list(crawler.fetch_new())

        fetched_dids = [
            r.external_id for r in results if hasattr(r, "external_id") and r.external_id
        ]
        assert "234" in fetched_dids
        assert "235" in fetched_dids


class TestSudrfPressParserRealHtml:
    def test_parses_razlugo_release(self) -> None:
        release_file = FIXTURE_DIR / "release-234-razlugo.html"
        html = release_file.read_text(encoding="utf-8")

        parsed = parse_press_release(html)

        assert parsed.title is not None
        assert "Брянск" in parsed.title or "финансирование" in parsed.title
        assert parsed.published_at is not None
        assert parsed.published_at.year == 2026
        assert parsed.published_at.month == 4
        assert parsed.published_at.day == 2
        assert "Разлуго" in parsed.text
        assert "205" in parsed.text
        assert "9 лет" in parsed.text or "лишения свободы" in parsed.text

    def test_parses_voronezh_architect_release(self) -> None:
        release_file = FIXTURE_DIR / "release-235-voronezh-architect.html"
        html = release_file.read_text(encoding="utf-8")

        parsed = parse_press_release(html)

        assert parsed.title is not None
        assert "Воронеж" in parsed.title or "архитектор" in parsed.title.lower()
        assert parsed.published_at is not None
        assert parsed.published_at.year == 2026
        assert parsed.published_at.month == 4
        assert parsed.published_at.day == 3

    def test_parses_bpla_operator_release(self) -> None:
        release_file = FIXTURE_DIR / "release-233-bpla-operator.html"
        html = release_file.read_text(encoding="utf-8")

        parsed = parse_press_release(html)

        assert parsed.title is not None
        assert "БПЛА" in parsed.title or "оператор" in parsed.title
        assert parsed.published_at is not None
        assert parsed.published_at.year == 2026
        assert parsed.published_at.month == 4
        assert parsed.published_at.day == 1

    def test_parses_kaluga_arsonists_release(self) -> None:
        release_file = FIXTURE_DIR / "release-231-kaluga-arsonists.html"
        html = release_file.read_text(encoding="utf-8")

        parsed = parse_press_release(html)

        assert parsed.title is not None
        assert "Калуж" in parsed.title or "поджигател" in parsed.title
        assert parsed.published_at is not None
        assert parsed.published_at.year == 2026
        assert parsed.published_at.month == 4
        assert parsed.published_at.day == 1
