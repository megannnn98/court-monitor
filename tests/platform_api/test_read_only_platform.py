"""Read-only platform operations delegate to the application services (PostgreSQL)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker
from support.monitoring_fixtures import SIDOROV, FakeUpstream, build_service, import_rf_snapshot

from platform_api.read_only import MAX_PLATFORM_RESULTS, PlatformError, ReadOnlyPlatform
from research.reports.provenance import verify_report_provenance
from research.workflow.models import WorkflowStatus

POLITICAL_NOT_IN_RF = {
    "object_type": "person",
    "criteria": {"persecution_status": "political", "rosfinmonitoring_status": "not_matched"},
}


@pytest.fixture
def platform(session_factory: sessionmaker[Session]) -> ReadOnlyPlatform:
    import_rf_snapshot(session_factory, [("Петр Петров", "03.03.1970")])
    upstream = FakeUpstream()
    upstream.publish("sidorov", SIDOROV)
    build_service(session_factory, {"ovd-info": upstream}).run_source("ovd-info")
    return ReadOnlyPlatform(session_factory)


def test_research_people_and_get_person(platform: ReadOnlyPlatform) -> None:
    response = platform.research_people(
        {"object_type": "person", "criteria": {"name": "Сидоров"}, "limit": 10}
    )

    [result] = response.results
    assert result.person.canonical_name == "Сергей Сидоров"
    assert result.person.id is not None
    details = platform.get_person(result.person.id)
    assert details.found
    assert details.person is not None and details.person.evidence
    assert platform.get_person(999_999).found is False


def test_research_report_is_evidence_backed(
    platform: ReadOnlyPlatform, session_factory: sessionmaker[Session]
) -> None:
    result = platform.get_research_report(POLITICAL_NOT_IN_RF)

    assert result.status is WorkflowStatus.COMPLETED
    assert result.report is not None
    assert [item.canonical_name for item in result.report.items] == ["Сергей Сидоров"]
    with session_factory() as session:
        assert verify_report_provenance(session, result.report, result.results) == []


def test_monitoring_findings_and_status(platform: ReadOnlyPlatform) -> None:
    page = platform.list_monitoring_findings(limit=10)
    status = platform.get_monitoring_status()

    assert [finding.finding_type for finding in page.findings] == [
        "political_persecution_not_in_rf"
    ]
    assert status.active_findings == 1
    assert [state.source_name for state in status.sources] == ["ovd-info"]


@pytest.mark.parametrize(
    ("call", "code"),
    [
        (
            lambda p: p.research_people({"object_type": "person", "criteria": {"x": 1}}),
            "invalid_research_request",
        ),
        (
            lambda p: p.research_people(
                {"object_type": "person", "limit": MAX_PLATFORM_RESULTS + 1}
            ),
            "limit_too_large",
        ),
        (
            lambda p: p.research_people(
                {"object_type": "person", "criteria": {"semantic_query": "протест"}}
            ),
            "semantic_query_not_supported",
        ),
        (
            lambda p: p.research_people(
                {"object_type": "person", "criteria": {"snapshot_id": 999}}
            ),
            "snapshot_not_found",
        ),
        (lambda p: p.get_person(0), "invalid_person_id"),
        (lambda p: p.list_monitoring_findings(limit=1000), "invalid_page"),
    ],
)
def test_invalid_calls_fail_with_stable_codes(
    platform: ReadOnlyPlatform, call: object, code: str
) -> None:
    with pytest.raises(PlatformError) as raised:
        call(platform)  # type: ignore[operator]
    assert raised.value.code == code
