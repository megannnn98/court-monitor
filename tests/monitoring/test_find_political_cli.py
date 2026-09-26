"""`find-political`, the final step: the Rosfinmonitoring check, then the political
verdicts, then the unnamed figurants — and a list that cannot be had costs no verdict."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from db.orm_models import (
    EntityGroupRecord,
    EntityGroupRoleRecord,
    RosfinmonitoringEntryRecord,
    RosfinmonitoringSnapshotRecord,
)
from entities.rf_check import EntityRfCheck
from monitoring import cli
from monitoring.cli import add_monitoring_arguments, run_monitoring_command


def _parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_monitoring_arguments(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(argv)


def _figurant(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        entity = EntityGroupRecord(
            key="иван петрович иванов",
            name="Иван Петрович Иванов",
            variants=[["Иван Петрович Иванов", 1]],
            mention_count=1,
            article_count=1,
            event_types={},
        )
        session.add(entity)
        session.flush()
        session.add(
            EntityGroupRoleRecord(
                group_id=entity.id,
                role="figurant",
                kind="accused",
                method="model",
                reason="обвиняемый",
                quote="",
            )
        )


def _snapshot(session_factory: sessionmaker[Session]) -> int:
    with session_factory.begin() as session:
        snapshot = RosfinmonitoringSnapshotRecord(
            snapshot_date=datetime(2026, 9, 25, tzinfo=UTC),
            source_url="https://fedsfm.test",
            content_hash="old",
            entry_count=1,
            fetched_at=datetime(2026, 9, 25, tzinfo=UTC),
        )
        session.add(snapshot)
        session.flush()
        session.add(
            RosfinmonitoringEntryRecord(
                snapshot_id=snapshot.id,
                full_name="ИВАНОВ ИВАН ПЕТРОВИЧ",
                normalized_name="иванов иван петрович",
                matching_key="ивановиванпетрович",
            )
        )
        return snapshot.id


def _with_download(monkeypatch: pytest.MonkeyPatch, download: Callable[[], bytes]) -> None:
    """The list's site, stood in for: the check runs as the CLI builds it."""

    def check(session_factory: sessionmaker[Session], **kwargs: Any) -> EntityRfCheck:
        return EntityRfCheck(session_factory, download=download, **kwargs)

    monkeypatch.setattr(cli, "EntityRfCheck", check)


def _offline() -> bytes:
    raise httpx.ConnectError("no route to fedsfm.ru")


def _run(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> dict[str, Any]:
    # No model: the rules decide, the rest is «unclear» — enough to see a verdict written.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert run_monitoring_command(_parse("find-political"), session_factory)
    return dict(json.loads(capsys.readouterr().out))


def _counts(session_factory: sessionmaker[Session]) -> tuple[int, int]:
    with session_factory() as session:
        matches = session.scalar(text("SELECT count(*) FROM entity_group_rf_matches")) or 0
        verdicts = session.scalar(text("SELECT count(*) FROM entity_group_politics")) or 0
    return matches, verdicts


def test_a_failed_download_checks_by_the_last_snapshot_then_judges(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _figurant(session_factory)
    snapshot_id = _snapshot(session_factory)
    _with_download(monkeypatch, _offline)

    output = _run(session_factory, monkeypatch, capsys)

    # The list, checked by the snapshot kept; then the verdicts; then the unnamed.
    assert output["snapshot_id"] == snapshot_id
    assert output["snapshot_date"].startswith("2026-09-25")
    assert output["download_error"].startswith("ConnectError")
    assert (output["rf_full"], output["rf_possible"], output["rf_error"]) == (1, 0, None)
    assert output["figurants"] == 1 and output["unclear"] == 1
    assert "unnamed" in output and "unnamed_cost_usd" in output
    # What the political cases' latest news is: none political here, nothing asked.
    assert (output["news_new_case"], output["news_sentence"], output["news_unknown"]) == (0, 0, 0)
    assert "news_cost_usd" in output
    assert _counts(session_factory) == (1, 1)


def test_no_snapshot_leaves_the_list_unchecked_and_still_judges(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _figurant(session_factory)
    _with_download(monkeypatch, _offline)

    output = _run(session_factory, monkeypatch, capsys)

    assert output["snapshot_id"] is None and output["download_error"].startswith("ConnectError")
    assert output["figurants"] == 1
    assert _counts(session_factory) == (0, 1)


def test_a_failed_check_costs_no_verdict(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _figurant(session_factory)
    _snapshot(session_factory)

    def broken(session_factory: sessionmaker[Session], **_kwargs: Any) -> EntityRfCheck:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(cli, "EntityRfCheck", broken)

    output = _run(session_factory, monkeypatch, capsys)

    assert output["snapshot_id"] is None
    assert output["rf_error"] == "RuntimeError: the database went away"
    assert output["figurants"] == 1
    assert _counts(session_factory) == (0, 1)
