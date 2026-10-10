"""The list page from the Internet Archive, for a server fedsfm.ru does not serve."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from rosfinmonitoring.download import (
    ARCHIVE_LATEST_URL,
    RF_LIST_URL,
    ListPage,
    RosfinmonitoringDownloadError,
    archived_rf_list,
    fetch_rf_list,
)

LIST = (
    "<html>Росфинмониторинг<p>1. ИВАНОВ ИВАН ИВАНОВИЧ, 01.01.1990 г.р.</p></body>\r\n</html>\r\n"
).encode()
STAMP = "20261009101910"
COPY = f"https://web.archive.org/web/{STAMP}id_/{RF_LIST_URL}"


class _Answer:
    def __init__(self, status: int = 200, body: bytes = b"", data: Any = None) -> None:
        self.status_code, self.content, self._data = status, body, data

    def json(self) -> Any:
        return self._data


def _archive(copy: _Answer, latest: Any = None) -> Any:
    found = {"archived_snapshots": {"closest": {"timestamp": STAMP}}} if latest is None else latest

    def get(url: str, **options: Any) -> _Answer:
        if url == ARCHIVE_LATEST_URL:
            assert options == {"params": {"url": RF_LIST_URL}}
            return _Answer(data=found)
        assert url == COPY
        return copy

    return get


def test_the_archive_s_latest_capture_is_the_list_of_the_day_it_was_captured() -> None:
    page = archived_rf_list(_archive(_Answer(body=LIST)))

    assert page == ListPage(
        LIST,
        f"https://web.archive.org/web/{STAMP}/{RF_LIST_URL}",
        datetime(2026, 10, 9, 10, 19, 10, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("copy", "latest", "said"),
    [
        (_Answer(status=503), None, "веб-архив вернул 503 на копию от 09.10.2026"),
        # A capture of the site's refusal, and one cut short before the page's end.
        (_Answer(body=b"<html>Access denied</html>"), None, "не перечень целиком"),
        (_Answer(body=LIST.removesuffix(b"</html>\r\n")), None, "не перечень целиком"),
        # The page was never captured.
        (_Answer(body=LIST), {"archived_snapshots": {}}, "копия перечня в веб-архиве не получена"),
    ],
)
def test_a_capture_that_is_not_the_whole_list_is_refused(
    copy: _Answer, latest: Any, said: str
) -> None:
    with pytest.raises(RosfinmonitoringDownloadError, match=said):
        archived_rf_list(_archive(copy, latest))


def test_a_transport_failure_of_the_archive_is_a_download_error() -> None:
    def get(url: str, **options: Any) -> _Answer:
        raise TimeoutError("archive.org is slow")

    with pytest.raises(RosfinmonitoringDownloadError, match="TimeoutError: archive.org is slow"):
        archived_rf_list(get)


def test_the_site_that_answers_is_not_replaced_by_the_archive() -> None:
    def archive() -> ListPage:
        raise AssertionError("the archive must not be asked")

    assert fetch_rf_list(lambda: LIST, archive) == ListPage(LIST, RF_LIST_URL, None)


def _refusing() -> bytes:
    raise RosfinmonitoringDownloadError("перечень РФМ не скачался: timeout")


def test_the_archive_is_asked_when_the_site_does_not_serve_the_page() -> None:
    captured = ListPage(LIST, "https://web.archive.org/web/x/y", datetime(2026, 10, 9, tzinfo=UTC))

    assert fetch_rf_list(_refusing, lambda: captured) is captured


def test_a_failure_of_both_says_both() -> None:
    def archive() -> ListPage:
        raise RosfinmonitoringDownloadError("веб-архив вернул 503 на копию от 09.10.2026")

    with pytest.raises(RosfinmonitoringDownloadError) as failure:
        fetch_rf_list(_refusing, archive)

    assert str(failure.value) == (
        "перечень РФМ не скачался: timeout; веб-архив вернул 503 на копию от 09.10.2026"
    )
