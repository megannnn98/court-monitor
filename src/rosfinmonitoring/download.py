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

The site also turns away the addresses of hosting companies, whatever the fingerprint: a
server in a data centre gets the first kilobytes and then nothing. Such a server is sent
the page as a file by a computer the site does answer (`RFM_LIST_FILE`, `fetch_rf_list`).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


# The page as a file, delivered by a computer the site answers (a daily timer there).
LIST_FILE_ENV = "RFM_LIST_FILE"
# A delivered file older than this is not taken for today's list: the computer that sends
# it has been silent, and the operator must hear of it rather than read an old list.
FRESH_FOR = timedelta(days=8)


def _is_the_list(body: bytes) -> bool:
    page = body.decode("utf-8", "replace")
    return all(marker in page for marker in _MARKERS)


def delivered_list(path: Path, now: datetime) -> tuple[bytes | None, str | None]:
    """(the delivered page, why it is not used). Both None when no file was delivered."""
    try:
        delivered_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        body = path.read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"присланный файл перечня не прочитан: {exc}"
    day = f"{delivered_at:%d.%m.%Y}"
    if now - delivered_at > FRESH_FOR:
        return None, f"присланный файл перечня от {day} устарел: новый давно не присылали"
    if not _is_the_list(body):
        return None, f"присланный файл от {day} — не перечень: страница без строк списка"
    return body, None


def fetch_rf_list() -> bytes:
    """The published list: the delivered file when there is a fresh one, else the site.

    A file that cannot be used does not stop the download; if the download fails as well,
    the error says both, so that a silent sender is seen and not taken for the site's
    refusal alone."""
    named = os.environ.get(LIST_FILE_ENV, "")
    body, unused = delivered_list(Path(named), datetime.now(UTC)) if named else (None, None)
    if body is not None:
        logger.info("event=rf_list_delivered bytes=%d file=%s", len(body), named)
        return body
    if unused is None:
        return download_rf_list()
    logger.warning("event=rf_list_delivered_unused reason=%s", unused)
    try:
        return download_rf_list()
    except RosfinmonitoringDownloadError as exc:
        raise RosfinmonitoringDownloadError(f"{exc}; {unused}") from exc


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
    body: bytes = response.content
    if not _is_the_list(body):
        raise RosfinmonitoringDownloadError(
            "fedsfm.ru ответил, но это не перечень: страница без строк списка. Снимок не тронут."
        )
    logger.info("event=rf_list_downloaded bytes=%d", len(body))
    return body
