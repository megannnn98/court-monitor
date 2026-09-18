"""Wiki Markdown to HTML: the same rendering for the site and the PDF."""

from __future__ import annotations

import subprocess

import pytest

from web.wiki import _wiki_markdown_to_html


def test_inline_markup_is_rendered() -> None:
    html = _wiki_markdown_to_html("Вызов `discover()` — **только код**, _курсив_.")

    assert "<code>discover()</code>" in html
    assert "<strong>только код</strong>" in html
    assert "<em>курсив</em>" in html
    assert "`" not in html and "**" not in html


def test_a_paragraph_spans_its_lines() -> None:
    html = _wiki_markdown_to_html("первая строка\nвторая строка\n")

    assert html.count("<p>") == 1


def test_tables_and_ordered_lists_are_rendered() -> None:
    html = _wiki_markdown_to_html("| a | b |\n|---|---|\n| 1 | `x` |\n\n1. раз\n2. два\n")

    assert "<table>" in html and "<th>a</th>" in html and "<td><code>x</code></td>" in html
    assert "<ol>" in html and "<li>раз</li>" in html


def test_a_link_to_a_wiki_page_goes_to_the_site_page() -> None:
    html = _wiki_markdown_to_html("[Getting Started](Getting-Started.md#run) — запуск")

    assert '<a href="/ui/wiki/Getting-Started">Getting Started</a>' in html


def test_a_link_to_a_wiki_page_goes_to_its_pdf_section() -> None:
    html = _wiki_markdown_to_html("[Setup](Setup.md)", page_link_prefix="#wiki-")

    assert '<a href="#wiki-Setup">Setup</a>' in html


def test_a_link_outside_the_wiki_keeps_its_text_without_a_target() -> None:
    html = _wiki_markdown_to_html("[ADR 0002](../adr/0002-drop.md) и [сайт](https://ovd.info)")

    assert "<a>ADR 0002</a>" in html
    assert '<a href="https://ovd.info">сайт</a>' in html
    assert "../adr" not in html


def test_raw_html_is_shown_as_text() -> None:
    html = _wiki_markdown_to_html("<script>alert(1)</script>\n\nтекст <b>жирный</b>")

    assert "<script>" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html


def test_code_blocks_are_escaped_and_plantuml_becomes_svg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.wiki.shutil.which", lambda name: "/usr/bin/plantuml")
    monkeypatch.setattr(
        "web.wiki.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["plantuml"], returncode=0, stdout="<svg>diagram</svg>", stderr=""
        ),
    )

    html = _wiki_markdown_to_html(
        "```python\nif a < b:\n    pass\n```\n\n```plantuml\n@startuml\nA -> B\n@enduml\n```\n"
    )

    assert "<pre><code>if a &lt; b:\n    pass</code></pre>" in html
    assert '<figure class="wiki-diagram"><svg>diagram</svg></figure>' in html
