"""Rendering docs/wiki: Markdown to HTML, PlantUML to SVG, the whole wiki to PDF."""

import os
import shutil
import subprocess
from html import escape
from pathlib import Path

from fastapi import HTTPException


def _wiki_root() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "wiki"


def _wiki_page(slug: str) -> Path | None:
    """The page file of a slug, or None when it is not a page of the wiki."""
    root = _wiki_root()
    page = root / f"{slug}.md"
    return page if page.parent == root and page.is_file() else None


def _wiki_pages() -> list[Path]:
    return sorted(_wiki_root().glob("*.md"), key=lambda path: path.name.lower())


def _wiki_markdown_to_html(markdown: str) -> str:
    rendered: list[str] = []
    in_code = False
    code_lines: list[str] = []
    code_language = ""
    list_open = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith("```"):
            if in_code:
                code = chr(10).join(code_lines)
                rendered.append(
                    _render_plantuml(code)
                    if code_language == "plantuml"
                    else f"<pre><code>{escape(code)}</code></pre>"
                )
                code_lines = []
                code_language = ""
                in_code = False
            else:
                if list_open:
                    rendered.append("</ul>")
                    list_open = False
                in_code = True
                code_language = line[3:].strip().lower()
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line:
            if list_open:
                rendered.append("</ul>")
                list_open = False
            continue
        if line.startswith("#"):
            if list_open:
                rendered.append("</ul>")
                list_open = False
            level = min(len(line) - len(line.lstrip("#")), 4)
            rendered.append(f"<h{level}>{escape(line[level:].strip())}</h{level}>")
        elif line.startswith("- "):
            if not list_open:
                rendered.append("<ul>")
                list_open = True
            rendered.append(f"<li>{escape(line[2:])}</li>")
        else:
            if list_open:
                rendered.append("</ul>")
                list_open = False
            rendered.append(f"<p>{escape(line)}</p>")
    if in_code:
        code = chr(10).join(code_lines)
        rendered.append(
            _render_plantuml(code)
            if code_language == "plantuml"
            else f"<pre><code>{escape(code)}</code></pre>"
        )
    if list_open:
        rendered.append("</ul>")
    return "\n".join(rendered)


def _render_plantuml(source: str) -> str:
    if shutil.which("plantuml") is None:
        return (
            f"<pre><code>{escape(source)}</code></pre>"
            '<p class="warning">PlantUML не установлен в API-контейнере.</p>'
        )
    try:
        plantuml_env = dict(os.environ)
        plantuml_env.pop("DISPLAY", None)
        result = subprocess.run(
            ["plantuml", "-tsvg", "-pipe"],
            input=source,
            capture_output=True,
            check=True,
            text=True,
            timeout=15,
            env=plantuml_env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (
            f"<pre><code>{escape(source)}</code></pre>"
            f'<p class="warning">PlantUML не смог построить диаграмму: {escape(str(exc))}</p>'
        )
    if "<svg" not in result.stdout:
        return f'<pre><code>{escape(source)}</code></pre><p class="warning">PlantUML вернул пустой SVG.</p>'
    return f'<figure class="wiki-diagram">{result.stdout}</figure>'


_WIKI_PDF_CSS = """
@page { size: A4; margin: 18mm 16mm 20mm;
  @bottom-right { content: counter(page); font-size: 9pt; color: #57606a; } }
body { font: 10.5pt/1.45 "DejaVu Sans", sans-serif; color: #1f2328; }
.wiki-page { break-before: page; }
h1 { font-size: 20pt; } h2 { font-size: 14pt; } h3 { font-size: 12pt; }
pre { white-space: pre-wrap; font: 8.5pt/1.35 "DejaVu Sans Mono", monospace;
  background: #f6f8fa; padding: 6pt; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; }
th, td { border: 1px solid #d0d7de; padding: 3pt 5pt; vertical-align: top; }
figure.wiki-diagram { margin: 8pt 0; text-align: center; }
/* A diagram is scaled to fit one sheet: an SVG cannot be split across pages. */
figure.wiki-diagram svg { max-width: 100%; max-height: 220mm; width: auto; height: auto; }
.toc a { color: inherit; text-decoration: none; }
.toc a::after { content: leader(".") target-counter(attr(href), page); }
"""


def _wiki_pdf_html() -> str:
    """The whole wiki as one HTML document: a contents page, then every page from a new
    sheet, Home first. Diagrams are the same SVG the site shows."""
    pages = sorted(_wiki_pages(), key=lambda path: (path.stem != "Home", path.name.lower()))
    contents = "".join(
        f'<li><a href="#wiki-{escape(page.stem)}">{escape(page.stem)}</a></li>' for page in pages
    )
    body = "".join(
        f'<section class="wiki-page" id="wiki-{escape(page.stem)}">'
        f"{_wiki_markdown_to_html(page.read_text(encoding='utf-8'))}</section>"
        for page in pages
    )
    return (
        f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<style>{_WIKI_PDF_CSS}</style></head><body>"
        f'<h1>court-monitor — вики</h1><ol class="toc">{contents}</ol>{body}</body></html>'
    )


def _wiki_pdf() -> bytes:
    from weasyprint import HTML  # heavy; loaded only when a PDF is asked for

    pdf = HTML(string=_wiki_pdf_html()).write_pdf()
    # WeasyPrint ships no types: the check also narrows its result to bytes.
    if not isinstance(pdf, bytes):
        raise HTTPException(status_code=500, detail="PDF was not produced")
    return pdf
