"""Downloading the published Rosfinmonitoring list from fedsfm.ru.

The site answers `403 Forbidden` to anything it does not recognise as a browser: a plain
`httpx` request with a hand-written `User-Agent` is turned away, which is why the list
used to have to be saved by hand and uploaded. The way past it is a real browser
fingerprint, the same impersonation `torrent-loader` uses for Rutracker.

This is the one place in the project that reaches outside on a schedule, so what it
returns matters: a `403` page read as a list would replace the snapshot with nothing. A
response is therefore checked before it is handed on — it must carry the list, not just
succeed. `validate=False` on TLS is gone with the rest: the fingerprint is what gets us
in, and turning certificate checking off is no longer needed to do it.

A server in a data centre is not served at all: the connection opens, some 16 KB arrive
and the stream stops — whatever the fingerprint, the HTTP version or the VPN in between
(their exits are in data centres too). Such a server takes the page from the Internet
Archive, which captures it every few days: the same page of fedsfm.ru, as it was on the
day of the capture (`fetch_rf_list`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

logger = logging.getLogger("entities")


class RosfinmonitoringDownloadError(RuntimeError):
    """The published list could not be read, for any reason.

    Its own type rather than a bare `RuntimeError` so that the caller catching a failed
    download catches *this* and not every other error in the process: the promise that a
    failed download leaves the last snapshot in use is only as good as the handler.
    """


RF_LIST_URL = "https://www.fedsfm.ru/documents/terrorists-catalog-portal-act"
DOWNLOAD_TIMEOUT_SECONDS = 180.0
# A stream that stopped is given up after this long, not after the whole timeout: the
# page takes two seconds where it is served at all.
STALLED_SECONDS = 30
IMPERSONATE = "chrome146"
# The Internet Archive's latest capture of a page, as it was fetched (`id_`: the original
# bytes, without the archive's banner and rewritten links). A partial timestamp is
# redirected to the latest capture; its own address and `Memento-Datetime` then say
# which capture was served. The archive's «availability» API is not asked: it answers
# with nothing at times.
ARCHIVE_LATEST_COPY_URL = "https://web.archive.org/web/2id_/{url}"
ARCHIVE_PAGE_URL = "https://web.archive.org/web/{stamp}/{url}"
_ARCHIVE_COPY = re.compile(r"https?://web\.archive\.org/web/(\d{14})id_/(.+)")
ARCHIVE_TIMEOUT_SECONDS = 120.0

# The list page is a numbered table of names with birth dates. Any of these appearing in
# a response means it is the list and not an error page dressed as a 200.
_MARKERS = ("г.р.", "Росфинмониторинг")


@dataclass(frozen=True)
class ListPage:
    """The list page and where it came from. `captured_at` is the day an archive saw the
    page; None for the site itself, which is asked now."""

    content: bytes
    source_url: str = RF_LIST_URL
    captured_at: datetime | None = None


def _is_the_list(body: bytes) -> bool:
    page = body.decode("utf-8", "replace")
    return all(marker in page for marker in _MARKERS)


def _get(url: str, **options: Any) -> Any:
    from curl_cffi import requests

    return requests.get(url, timeout=ARCHIVE_TIMEOUT_SECONDS, **options)


def archived_rf_list(get: Callable[..., Any] = _get) -> ListPage:
    """The Internet Archive's latest capture of the list page, checked like a download:
    it must be the list, and whole — a capture cut short would lose the list's end.

    The day of the capture is read from the answer itself, not from what was asked for:
    the archive may serve another capture than the one named."""
    try:
        response = get(ARCHIVE_LATEST_COPY_URL.format(url=RF_LIST_URL))
        status, body, served = response.status_code, response.content, str(response.url)
        said = response.headers.get("memento-datetime")
    except Exception as exc:  # a transport failure, an answer of another shape
        raise RosfinmonitoringDownloadError(
            f"копия перечня в веб-архиве не получена: {type(exc).__name__}: {exc}"
        ) from exc
    if status != 200:
        raise RosfinmonitoringDownloadError(f"веб-архив вернул {status} на копию перечня")
    copy = _ARCHIVE_COPY.fullmatch(served)
    if copy is None or copy.group(2).split("://", 1)[-1] != RF_LIST_URL.split("://", 1)[-1]:
        raise RosfinmonitoringDownloadError(f"веб-архив отдал не копию перечня: {served[:200]}")
    stamp = copy.group(1)
    unconfirmed = f"веб-архив не подтвердил день копии: в адресе {stamp}, в ответе {said!r}"
    try:
        # Fourteen digits are not yet a day: «20261309…» has no thirteenth month.
        captured_at = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        dated = parsedate_to_datetime(said) if said else None
    except ValueError as exc:
        raise RosfinmonitoringDownloadError(unconfirmed) from exc
    if dated != captured_at:
        raise RosfinmonitoringDownloadError(unconfirmed)
    day = f"{captured_at:%d.%m.%Y}"
    if not _is_the_list(body) or b"</html>" not in body[-200:]:
        raise RosfinmonitoringDownloadError(
            f"копия веб-архива от {day} — не перечень целиком. Снимок не тронут."
        )
    logger.info("event=rf_list_archived bytes=%d captured=%s", len(body), stamp)
    return ListPage(body, ARCHIVE_PAGE_URL.format(stamp=stamp, url=RF_LIST_URL), captured_at)


def fetch_rf_list(
    site: Callable[[], bytes] | None = None,
    archive: Callable[[], ListPage] = archived_rf_list,
) -> ListPage:
    """The list page: from the site, and where the site does not serve us, from the
    archive's latest capture. A failure of both says both."""
    try:
        return ListPage((site or download_rf_list)())
    except RosfinmonitoringDownloadError as refused:
        logger.warning("event=rf_list_site_failed error=%s", refused)
        try:
            return archive()
        except RosfinmonitoringDownloadError as missing:
            raise RosfinmonitoringDownloadError(f"{refused}; {missing}") from missing


def download_rf_list(url: str = RF_LIST_URL) -> bytes:
    """The whole published list page, as the parser expects it.

    Raises on a `403`, and on a `200` that is not the list — a captcha or a maintenance
    page would otherwise be parsed as a list of nobody.
    """
    from curl_cffi import requests
    from curl_cffi.const import CurlOpt

    try:
        response = requests.get(
            url,
            impersonate=IMPERSONATE,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            curl_options={CurlOpt.LOW_SPEED_LIMIT: 1, CurlOpt.LOW_SPEED_TIME: STALLED_SECONDS},
        )
    except Exception as exc:  # a transport failure reads the same here, whichever kind it was
        raise RosfinmonitoringDownloadError(f"перечень РФМ не скачался: {exc}") from exc
    if response.status_code != 200:
        raise RosfinmonitoringDownloadError(f"fedsfm.ru вернул {response.status_code} на {url}")
    body: bytes = response.content
    if not _is_the_list(body):
        raise RosfinmonitoringDownloadError(
            "fedsfm.ru ответил, но это не перечень: страница без строк списка. Снимок не тронут."
        )
    logger.info("event=rf_list_downloaded bytes=%d", len(body))
    return body
