"""Test that --live flag correctly switches sudrf backend from fixture to http."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

from court_monitor.config.loader import SourceConfig
from court_monitor.domain.models import SourceBackend, SourceType
from court_monitor.services import process_source


def _make_sudrf_config() -> SourceConfig:
    """Create a sudrf source config with fixture backend."""
    return SourceConfig(
        name="2zovs-test",
        type=SourceType.sudrf,
        backend=SourceBackend.fixture,
        base_url="https://2zovs.msk.sudrf.ru",
        fixture_path="tests/fixtures/sudrf-live/2zovs/press",
        court_name="2-й Западный окружной военный суд",
        press_module="press_dep",
    )


def test_process_source_live_overrides_backend(db_session, monitoring_cfg):
    """Verify that live=True creates runtime copy with http backend."""
    config = _make_sudrf_config()
    assert config.backend == SourceBackend.fixture

    # Mock the crawler to avoid actual HTTP requests
    with patch("court_monitor.sources.sudrf.SudrfPressCrawler") as mock_crawler:
        mock_instance = MagicMock()
        mock_instance.fetch_new.return_value = iter([])
        mock_crawler.return_value = mock_instance

        # Call with live=True
        process_source(db_session, config, monitoring_cfg, live=True)

        # Verify crawler was called with http backend config
        call_args = mock_crawler.call_args
        assert call_args is not None
        passed_config = call_args[0][0]  # First positional argument
        assert passed_config.backend == SourceBackend.http
        assert passed_config.name == config.name
        assert passed_config.base_url == config.base_url


def test_process_source_without_live_keeps_fixture(db_session, monitoring_cfg):
    """Verify that live=False (default) keeps fixture backend."""
    config = _make_sudrf_config()
    assert config.backend == SourceBackend.fixture

    # Mock the crawler
    with patch("court_monitor.sources.sudrf.SudrfPressCrawler") as mock_crawler:
        mock_instance = MagicMock()
        mock_instance.fetch_new.return_value = iter([])
        mock_crawler.return_value = mock_instance

        # Call without live parameter (default False)
        process_source(db_session, config, monitoring_cfg)

        # Verify crawler was called with fixture backend config
        call_args = mock_crawler.call_args
        assert call_args is not None
        passed_config = call_args[0][0]
        assert passed_config.backend == SourceBackend.fixture


def test_process_source_live_false_explicit(db_session, monitoring_cfg):
    """Verify that live=False explicitly keeps fixture backend."""
    config = _make_sudrf_config()

    with patch("court_monitor.sources.sudrf.SudrfPressCrawler") as mock_crawler:
        mock_instance = MagicMock()
        mock_instance.fetch_new.return_value = iter([])
        mock_crawler.return_value = mock_instance

        # Call with live=False explicitly
        process_source(db_session, config, monitoring_cfg, live=False)

        # Verify crawler was called with fixture backend config
        call_args = mock_crawler.call_args
        assert call_args is not None
        passed_config = call_args[0][0]
        assert passed_config.backend == SourceBackend.fixture


def test_process_source_already_http_not_duplicated(db_session, monitoring_cfg):
    """Verify that if config already has http backend, live=True doesn't change it."""
    config = replace(_make_sudrf_config(), backend=SourceBackend.http)
    assert config.backend == SourceBackend.http

    with patch("court_monitor.sources.sudrf.SudrfPressCrawler") as mock_crawler:
        mock_instance = MagicMock()
        mock_instance.fetch_new.return_value = iter([])
        mock_crawler.return_value = mock_instance

        # Call with live=True
        process_source(db_session, config, monitoring_cfg, live=True)

        # Verify crawler was called with http backend (unchanged)
        call_args = mock_crawler.call_args
        assert call_args is not None
        passed_config = call_args[0][0]
        assert passed_config.backend == SourceBackend.http
