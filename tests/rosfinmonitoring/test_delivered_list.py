"""The list page delivered as a file: a server the site turns away takes it from there."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rosfinmonitoring import download
from rosfinmonitoring.download import (
    FRESH_FOR,
    LIST_FILE_ENV,
    RosfinmonitoringDownloadError,
    delivered_list,
    fetch_rf_list,
)

LIST = "<html>Росфинмониторинг<p>1. ИВАНОВ ИВАН ИВАНОВИЧ, 01.01.1990 г.р.</p></html>".encode()
SITE = "<html>Росфинмониторинг<p>2. ПЕТРОВ ПЁТР ПЕТРОВИЧ, 02.02.1991 г.р.</p></html>".encode()
NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)


def _file(tmp_path: Path, body: bytes, *, age: timedelta) -> Path:
    path = tmp_path / "list.html"
    path.write_bytes(body)
    stamp = (NOW - age).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_a_fresh_delivered_file_is_the_list(tmp_path: Path) -> None:
    assert delivered_list(_file(tmp_path, LIST, age=timedelta(days=1)), NOW) == (LIST, None)


def test_no_file_is_no_delivery_and_no_complaint(tmp_path: Path) -> None:
    assert delivered_list(tmp_path / "list.html", NOW) == (None, None)


def test_a_file_the_sender_stopped_renewing_is_not_taken_for_today_s_list(tmp_path: Path) -> None:
    old = _file(tmp_path, LIST, age=FRESH_FOR + timedelta(hours=1))

    assert delivered_list(old, NOW) == (
        None,
        "присланный файл перечня от 02.10.2026 устарел: новый давно не присылали",
    )


def test_a_delivered_page_that_is_not_the_list_is_not_used(tmp_path: Path) -> None:
    captcha = _file(tmp_path, b"<html>Access denied</html>", age=timedelta(hours=1))

    body, unused = delivered_list(captcha, NOW)

    assert body is None
    assert unused == "присланный файл от 10.10.2026 — не перечень: страница без строк списка"


def _site(monkeypatch: pytest.MonkeyPatch, answer: bytes | Exception) -> list[int]:
    asked: list[int] = []

    def fake() -> bytes:
        asked.append(1)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(download, "download_rf_list", fake)
    return asked


def test_the_list_is_taken_from_the_delivered_file_and_the_site_is_not_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "list.html"
    path.write_bytes(LIST)
    monkeypatch.setenv(LIST_FILE_ENV, str(path))
    asked = _site(monkeypatch, SITE)

    assert fetch_rf_list() == LIST
    assert asked == []


@pytest.mark.parametrize("named", ["", "missing.html"])
def test_without_a_delivered_file_the_site_is_asked_as_before(
    named: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LIST_FILE_ENV, str(tmp_path / named) if named else "")
    asked = _site(monkeypatch, SITE)

    assert fetch_rf_list() == SITE
    assert asked == [1]


def test_a_stale_file_and_a_failed_download_are_both_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "list.html"
    path.write_bytes(LIST)
    long_ago = (datetime.now(UTC) - FRESH_FOR - timedelta(days=1)).timestamp()
    os.utime(path, (long_ago, long_ago))
    monkeypatch.setenv(LIST_FILE_ENV, str(path))
    _site(monkeypatch, RosfinmonitoringDownloadError("перечень РФМ не скачался: timeout"))

    with pytest.raises(RosfinmonitoringDownloadError) as failure:
        fetch_rf_list()

    message = str(failure.value)
    assert message.startswith("перечень РФМ не скачался: timeout; присланный файл перечня от ")
    assert message.endswith("устарел: новый давно не присылали")


def test_a_stale_file_does_not_stop_a_download_that_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "list.html"
    path.write_bytes(LIST)
    long_ago = (datetime.now(UTC) - FRESH_FOR - timedelta(days=1)).timestamp()
    os.utime(path, (long_ago, long_ago))
    monkeypatch.setenv(LIST_FILE_ENV, str(path))
    _site(monkeypatch, SITE)

    assert fetch_rf_list() == SITE
