"""The list page from the Internet Archive, for a server fedsfm.ru does not serve."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from rosfinmonitoring.download import (
    ARCHIVE_LATEST_COPY_URL,
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
SERVED = f"https://web.archive.org/web/{STAMP}id_/{RF_LIST_URL}"
DATED = "Fri, 09 Oct 2026 10:19:10 GMT"


class _Answer:
    """What the archive answered after its redirect to the latest capture."""

    def __init__(
        self, body: bytes = LIST, *, status: int = 200, url: str = SERVED, dated: str | None = DATED
    ) -> None:
        self.status_code, self.content, self.url = status, body, url
        self.headers = {"memento-datetime": dated} if dated else {}


def _archive(answer: _Answer) -> Any:
    def get(url: str, **options: Any) -> _Answer:
        assert url == ARCHIVE_LATEST_COPY_URL.format(url=RF_LIST_URL)
        assert options == {}
        return answer

    return get


def test_the_archive_s_latest_capture_is_the_list_of_the_day_it_was_captured() -> None:
    page = archived_rf_list(_archive(_Answer()))

    assert page == ListPage(
        LIST,
        f"https://web.archive.org/web/{STAMP}/{RF_LIST_URL}",
        datetime(2026, 10, 9, 10, 19, 10, tzinfo=UTC),
    )


def test_the_day_is_the_served_capture_s_not_the_one_asked_for() -> None:
    # The archive sent on to a capture of two days before.
    earlier = _Answer(
        url=f"https://web.archive.org/web/20261007080000id_/{RF_LIST_URL}",
        dated="Wed, 07 Oct 2026 08:00:00 GMT",
    )

    assert archived_rf_list(_archive(earlier)).captured_at == datetime(2026, 10, 7, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (_Answer(status=503), "веб-архив вернул 503 на копию перечня"),
        # A capture of the site's refusal, and one cut short before the page's end.
        (_Answer(b"<html>Access denied</html>"), "не перечень целиком"),
        (_Answer(LIST.removesuffix(b"</html>\r\n")), "не перечень целиком"),
        # Not a capture of this page: the archive's own page, another site's capture.
        (_Answer(url="https://web.archive.org/"), "веб-архив отдал не копию перечня"),
        (
            _Answer(url=f"https://web.archive.org/web/{STAMP}id_/https://example.org/"),
            "веб-архив отдал не копию перечня",
        ),
        # The answer's own date disagrees with its address, or is missing.
        (_Answer(dated="Wed, 07 Oct 2026 08:00:00 GMT"), "веб-архив не подтвердил день копии"),
        (_Answer(dated=None), "веб-архив не подтвердил день копии"),
        (_Answer(dated="yesterday"), "веб-архив не подтвердил день копии"),
    ],
)
def test_an_answer_that_is_not_the_whole_list_of_a_known_day_is_refused(
    answer: _Answer, said: str
) -> None:
    with pytest.raises(RosfinmonitoringDownloadError, match=said):
        archived_rf_list(_archive(answer))


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
