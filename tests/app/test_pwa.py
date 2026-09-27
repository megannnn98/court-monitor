"""The local investigator UI is installable from the phone browser."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import app
from web.ui import layout

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src/static"


def _png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_manifest_describes_a_standalone_local_app() -> None:
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))

    assert manifest["name"] == "court-monitor — Следователь"
    assert manifest["start_url"] == "/ui/overview"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert {(icon["src"], icon["sizes"]) for icon in manifest["icons"]} == {
        ("/static/icon-192.png", "192x192"),
        ("/static/icon-512.png", "512x512"),
    }


def test_manifest_and_icons_are_served_with_the_declared_sizes() -> None:
    assert _png_dimensions(STATIC / "icon-192.png") == (192, 192)
    assert _png_dimensions(STATIC / "icon-512.png") == (512, 512)

    client = TestClient(app)
    manifest = client.get("/static/manifest.webmanifest")
    icon = client.get("/static/icon-192.png")

    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith(("application/manifest+json", "text/plain"))
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"


def test_every_ui_page_links_the_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        layout,
        "_status_counts",
        lambda _db: {"articles": 0, "people": 0, "queue": 0, "latest_run": "нет", "result": 0},
    )

    page = layout._page(
        "Проверка",
        "",
        active="overview",
        instruction="",
        next_action="",
        db=object(),  # type: ignore[arg-type]
    ).body.decode()

    assert '<link rel="manifest" href="/static/manifest.webmanifest">' in page
    assert '<meta name="theme-color"' in page
