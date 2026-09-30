"""Reading a list from a public Airtable share link, with no token and no login.

A share link of the form `https://airtable.com/{applicationId}/{shareId}` opens a page
anyone may read. The rows are not in the page: the page carries a `window.initData`
object holding a `sharedViewId` and an `accessPolicy`, and the second is the right to
read — a *signed* JSON string with a signature, an expiry, the share id and the list of
actions the visitor is allowed. Two requests then read the list, both anonymous:

    GET /v0.3/view/{viewId}/downloadCsv          → the rows as CSV, preferred
    GET /v0.3/view/{viewId}/readSharedViewData   → the rows as msgpack, not used

`downloadCsv` is preferred because the file-mode reader already speaks CSV, and because
msgpack would need a dependency for a format we would only decode to throw away.

Four things about this protocol are not obvious and cost real time to find, so they are
written down here rather than left to be rediscovered:

- **A CSRF token must not be sent.** `x-airtable-csrftoken` turns an anonymous read into
  an application call, and the answer is `401 AUTHORIZATION_REQUIRED`. The right to read
  is the signed policy, not a token.
- **`accessPolicy` travels in the query string**, and without it the request does not
  fail — it falls through to the application's own HTML. A 200 that is not CSV. This is
  the worst failure mode here: a list that reads as empty, which for a sync means data
  loss rather than an error.
- **`x-time-zone` is required** by `downloadCsv`, which answers `400` naming it.
- **The `accessPolicy` in the page is already the signed one**, `signature` and `expires`
  included. Nothing has to be signed locally.

The signature lasts weeks, not minutes: the one observed on 30.09.2026 was good until
22.10.2026. The policy is read from the page on every call rather than cached, because
that is one extra request and makes an expired signature impossible to trip over.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from airtable.client import AirtableError, AirtableRecord

logger = logging.getLogger("airtable")

# The browser fingerprint the request is made under. Airtable serves a login wall to
# anything it does not recognise; this is the same impersonation `torrent-loader` uses
# for Rutracker, and the same one that gets fedsfm.ru past its 403.
IMPERSONATE = "chrome146"

# The build of Airtable's web client. Sent because Airtable's own client sends it; it is
# a version marker, not a credential, and a stale value costs nothing.
CODE_VERSION = "cf32083c89c00ebe303ad7d79334a82e1c86f2a7"

# Where the page keeps what the visitor may do.
_INIT_DATA = "window.initData = "
_SHARE_URL = re.compile(r"https://airtable\.com/(app[A-Za-z0-9]+)/(shr[A-Za-z0-9]+)")


def share_url_of(link: str) -> tuple[str, str] | None:
    """The `(application id, share id)` a public link names, or None if it names neither."""
    found = _SHARE_URL.search(link.strip())
    return (found.group(1), found.group(2)) if found else None


@dataclass(frozen=True)
class SharePage:
    """What a share page says about itself: which view to read, and on what terms."""

    application_id: str
    view_id: str
    access_policy: str
    page_load_id: str


def read_page(share_url: str, *, session: Any = None) -> SharePage:
    """Open a public share page and read the permission it grants.

    Raises `AirtableError` when the page is not a share page, or carries no policy: both
    mean the link is wrong or revoked, and guessing past them would read nothing.
    """
    from curl_cffi import requests  # imported here: only this mode needs a fingerprint

    http = session or requests.Session(impersonate=IMPERSONATE)
    try:
        response = http.get(share_url, timeout=REQUEST_TIMEOUT)
    except Exception as exc:  # a transport failure reads the same here, whichever kind it was
        raise AirtableError(f"страница {share_url} не открылась: {exc}") from exc
    if response.status_code != 200:
        raise AirtableError(f"страница {share_url} вернула {response.status_code}")
    page = _init_data(response.text)
    if page is None:
        raise AirtableError(f"по ссылке {share_url} нет данных для чтения")
    policy = page.get("accessPolicy")
    view_id = page.get("sharedViewId")
    if not policy or not view_id:
        raise AirtableError(f"ссылка {share_url} не даёт права на чтение")
    return SharePage(
        application_id=str(page.get("applicationIdOfInitialPageLoadForLogging") or ""),
        view_id=str(view_id),
        access_policy=policy if isinstance(policy, str) else json.dumps(policy),
        page_load_id=str(page.get("pageLoadId") or ""),
    )


def _init_data(page: str) -> dict[str, Any] | None:
    """The `window.initData` object.

    It is assigned raw in the page and its length moves between requests, so it cannot
    be sliced to a fixed end: it is parsed as one JSON value and the rest of the line is
    left alone.
    """
    start = page.find(_INIT_DATA)
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(page[start + len(_INIT_DATA) :])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


REQUEST_TIMEOUT = 120


def read_records(share_url: str, *, session: Any = None) -> list[AirtableRecord]:
    """Every row of a public share view, as records the sync already understands.

    The rows come as CSV, so the `AirtableRecord`s are built with the same column names
    a hand-exported file has — which is the point: one reader, one parser, whether the
    list arrived from the API, from a file, or from a link.
    """
    from curl_cffi import requests

    http = session or requests.Session(impersonate=IMPERSONATE)
    page = read_page(share_url, session=http)
    policy = page.access_policy
    application_id = page.application_id
    if not application_id:
        parts = share_url_of(share_url)
        if parts is None:
            raise AirtableError(f"ссылка {share_url} не похожа на публичную ссылку Airtable")
        application_id = parts[0]
    headers = {
        "x-airtable-application-id": application_id,
        "x-airtable-inter-service-client": "webClient",
        "x-airtable-inter-service-client-code-version": CODE_VERSION,
        "x-airtable-page-load-id": page.page_load_id,
        "x-requested-with": "XMLHttpRequest",
        "x-time-zone": "Asia/Almaty",
        "x-user-locale": "en",
        "referer": share_url,
        "accept": "*/*",
    }
    params = {
        "requestId": "req" + "0" * 12,
        "accessPolicy": policy,
        "stringifiedObjectParams": json.dumps({"includeBlanks": False}, separators=(",", ":")),
    }
    url = f"https://airtable.com/v0.3/view/{page.view_id}/downloadCsv"
    try:
        response = http.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    except Exception as exc:  # a transport failure reads the same here, whichever kind it was
        raise AirtableError(f"выгрузка по ссылке не удалась: {exc}") from exc
    if response.status_code != 200:
        raise AirtableError(f"выгрузка вернула {response.status_code}")
    content_type = response.headers.get("content-type", "")
    if "csv" not in content_type:
        # The request fell through to the application's own page. Raising here is the
        # whole point: read as "no rows" it would silently empty a list.
        raise AirtableError(
            "Airtable вернул не CSV, а страницу приложения: право на чтение не "
            f"применилось (тип {content_type or 'неизвестен'})"
        )
    return _records_from_csv(response.content)


def _records_from_csv(payload: bytes) -> list[AirtableRecord]:
    """Rows of a CSV export as records.

    Airtable marks the export with a BOM, which would otherwise become part of the first
    column's name and make the first column unreadable.
    """
    text = payload.decode("utf-8-sig", "replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return []
    records = []
    for number, row in enumerate(reader, start=1):
        if not any(cell.strip() for cell in row):
            continue
        fields = {
            name.strip(): value for name, value in zip(header, row, strict=False) if name.strip()
        }
        # A share view has no Airtable record id, and a CSV export does not carry one.
        # The name is the identity, which is how a hand-exported file was matched too.
        records.append(AirtableRecord(f"share:{number}", fields))
    logger.info("event=airtable_share_read rows=%d", len(records))
    return records


def iter_share_rows(share_url: str) -> Iterator[dict[str, str]]:
    """The raw rows of a share view, for a list that is not one of the four."""
    for record in read_records(share_url):
        yield dict(record.fields)
