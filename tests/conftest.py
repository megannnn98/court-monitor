"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from court_monitor.config.loader import MonitoringConfig
from court_monitor.storage.orm import Base

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_HTML = REPO_ROOT / "tests" / "fixtures" / "html"


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def monitoring_cfg() -> MonitoringConfig:
    return MonitoringConfig(
        criminal_articles=["205", "205.1", "205.2", "282", "275"],
        keywords=["финансирование терроризма", "шпионаж", "диверсия"],
    )


@pytest.fixture()
def fixtures_html_dir() -> Path:
    return FIXTURES_HTML
