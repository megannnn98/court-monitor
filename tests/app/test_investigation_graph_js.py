"""The browser side of the investigation graph, as far as it can be told without a
browser: its logic under node, the files the page asks for, the page's own markup."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.app import app

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "static"


def test_the_logic_of_the_graph_passes_its_own_tests() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests" / "js")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_library_and_the_scripts_are_served_from_here() -> None:
    client = TestClient(app)
    library = client.get("/static/vendor/vis-network/vis-network.min.js")

    assert library.status_code == 200 and len(library.content) > 300_000
    assert "vis-network" in library.text[:2000]
    for name in ("investigation-graph-core.js", "investigation-graph.js"):
        assert client.get(f"/static/{name}").status_code == 200
    # The licence travels with the bundle.
    for name in ("LICENSE-MIT", "LICENSE-APACHE-2.0", "README.md"):
        assert (STATIC / "vendor" / "vis-network" / name).is_file()


def test_the_page_script_writes_no_html_and_asks_nothing_outside() -> None:
    """Names, titles and sources are scraped text: they reach the page as text, and the
    scripts load nothing from another site."""
    for name in ("investigation-graph-core.js", "investigation-graph.js"):
        source = (STATIC / name).read_text()
        assert not re.search(
            r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(", source
        )
        assert not re.search(r"https?://", source), name
    page = (STATIC / "investigation-graph.js").read_text()
    # The two addresses the page gives, and no other.
    # A node asked for and not answered yet is not asked for again.
    assert "state.expanded.has(id) || pending.has(id)" in page and "pending.delete(id)" in page
    assert re.findall(r"load\((.+?)\)[\n.]", page) == [
        'box.dataset.expandUrl + "?node=" + encodeURIComponent(id)',
        "box.dataset.graphUrl",
    ]
