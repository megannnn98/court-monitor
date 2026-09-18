"""Operator console: the wiki pages and their PDF export."""

from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from web.dependencies import get_db
from web.ui.layout import _page
from web.wiki import _wiki_markdown_to_html, _wiki_page, _wiki_pages, _wiki_pdf

router = APIRouter()


_WIKI_PDF_BUTTON = '<p><a class="secondary" href="/ui/wiki/export.pdf">Скачать вики в PDF</a></p>'


@router.get("/ui/wiki/export.pdf")
def ui_wiki_export_pdf() -> Response:
    return Response(
        content=_wiki_pdf(),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="court-monitor-wiki.pdf"'},
    )


@router.get("/ui/wiki")
def ui_wiki_index(db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    pages = "".join(
        f'<li><a href="/ui/wiki/{quote(page.stem)}">{escape(page.stem)}</a></li>'
        for page in _wiki_pages()
    )
    return _page(
        "Wiki",
        f'{_WIKI_PDF_BUTTON}<ul class="wiki-index">{pages}</ul>',
        active="wiki",
        instruction="Wiki — справочник по проекту, pipeline и операторской консоли.",
        next_action="Откройте страницу, которая нужна для текущей операции.",
        db=db,
    )


@router.get("/ui/wiki/{slug}")
def ui_wiki_page(slug: str, db: Session = Depends(get_db)) -> HTMLResponse:  # noqa: B008
    page = _wiki_page(slug)
    if page is None:
        raise HTTPException(status_code=404, detail="Wiki page not found")
    button = _WIKI_PDF_BUTTON if page.stem == "Home" else ""
    return _page(
        page.stem,
        button + _wiki_markdown_to_html(page.read_text(encoding="utf-8")),
        active="wiki",
        instruction="Wiki — справочная страница проекта.",
        next_action="Вернитесь в Operator console через навигацию слева.",
        db=db,
    )
