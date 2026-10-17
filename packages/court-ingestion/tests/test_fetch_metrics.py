from __future__ import annotations

from ingestion.concurrency import FetchMetrics


def test_a_success_only_domain_has_a_report_without_errors() -> None:
    metrics = FetchMetrics()
    metrics.started("ovd.info")
    metrics.finished("ovd.info", 200, 0.01)

    report = metrics.report()

    assert report.succeeded == 1
    assert report.failed == 0
    assert report.per_domain["ovd.info"].failed == 0


def test_each_domain_reports_its_own_peak_and_retries() -> None:
    metrics = FetchMetrics()
    metrics.started("ovd.info")
    metrics.started("ovd.info")
    metrics.started("sota.vision")
    metrics.finished("ovd.info", 200, 0.01)
    metrics.finished("ovd.info", 200, 0.01)
    metrics.finished("sota.vision", 200, 0.01)
    metrics.retried("sota.vision")

    report = metrics.report()

    assert report.peak_concurrency == 3
    assert report.retries == 1
    assert report.per_domain["ovd.info"].peak_concurrency == 2
    assert report.per_domain["ovd.info"].retries == 0
    assert report.per_domain["sota.vision"].peak_concurrency == 1
    assert report.per_domain["sota.vision"].retries == 1


def test_a_retry_is_not_counted_as_a_failed_request() -> None:
    metrics = FetchMetrics()
    metrics.started("ovd.info")
    metrics.finished("ovd.info", 200, 0.01)
    metrics.retried("ovd.info")

    assert metrics.report().per_domain["ovd.info"].failed == 0
