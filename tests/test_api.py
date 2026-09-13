"""Tests for FastAPI application."""

import pytest
from fastapi.testclient import TestClient

from api import _get_session_factory, app


def test_api_health_check() -> None:
    """Test health check endpoint."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_has_persons_endpoint() -> None:
    """Test that persons endpoint exists."""
    client = TestClient(app)
    # Just check the endpoint exists (will fail due to no DB, but that's OK)
    response = client.get("/persons")
    # Should get 503 due to no DATABASE_URL, not 404
    assert response.status_code in (200, 503)


def test_api_has_candidates_endpoint() -> None:
    """Test that candidates endpoint exists."""
    client = TestClient(app)
    response = client.get("/candidates?snapshot_id=1")
    # Should get 503 due to no DATABASE_URL, not 404
    assert response.status_code in (200, 422, 503)


def test_api_has_rosfinmonitoring_snapshots_endpoint() -> None:
    """Test that rosfinmonitoring snapshots endpoint exists."""
    client = TestClient(app)
    response = client.get("/rosfinmonitoring/snapshots")
    # Should get 503 due to no DATABASE_URL, not 404
    assert response.status_code in (200, 503)


def test_api_has_reviews_endpoint() -> None:
    """Test that reviews endpoint exists."""
    client = TestClient(app)
    response = client.get("/reviews")
    # Should get 503 due to no DATABASE_URL, not 404
    assert response.status_code in (200, 503)


def test_api_missing_database_url_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that missing DATABASE_URL is a controlled service error."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _get_session_factory.cache_clear()
    client = TestClient(app)

    response = client.get("/persons")

    assert response.status_code == 503
    assert response.json()["detail"] == "DATABASE_URL environment variable is not set"
