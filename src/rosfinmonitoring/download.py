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
"""

from __future__ import annotations

import logging

logger = logging.getLogger("entities")


class RosfinmonitoringDownloadError(RuntimeError):
    """The published list could not be read, for any reason.

    Its own type rather than a bare `RuntimeError` so that the caller catching a failed
    download catches *this* and not every other error in the process: the promise that a
    failed download leaves the last snapshot in use is only as good as the handler.
    """


RF_LIST_URL = "https://www.fedsfm.ru/documents/terrorists-catalog-portal-act"
DOWNLOAD_TIMEOUT_SECONDS = 180.0
IMPERSONATE = "chrome146"

# The list page is a numbered table of names with birth dates. Any of these appearing in
# a response means it is the list and not an error page dressed as a 200.
_MARKERS = ("г.р.", "Росфинмониторинг")


def download_rf_list(url: str = RF_LIST_URL) -> bytes:
    """The whole published list page, as the parser expects it.

    Raises on a `403`, and on a `200` that is not the list — a captcha or a maintenance
    page would otherwise be parsed as a list of nobody.
    """
    from curl_cffi import requests

    try:
        response = requests.get(url, impersonate=IMPERSONATE, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except Exception as exc:  # a transport failure reads the same here, whichever kind it was
        raise RosfinmonitoringDownloadError(f"перечень РФМ не скачался: {exc}") from exc
    if response.status_code != 200:
        raise RosfinmonitoringDownloadError(f"fedsfm.ru вернул {response.status_code} на {url}")
    body = response.content
    page = body.decode("utf-8", "replace")
    if not all(marker in page for marker in _MARKERS):
        raise RosfinmonitoringDownloadError(
            "fedsfm.ru ответил, но это не перечень: страница без строк списка. Снимок не тронут."
        )
    logger.info("event=rf_list_downloaded bytes=%d", len(body))
    return body
