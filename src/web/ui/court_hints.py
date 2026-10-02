"""«Где судят»: the courts the base has seen sentence under this article from this place,
as one line of a card."""

from __future__ import annotations

from html import escape

from entities.jurisdiction import CourtHints
from web.ui.layout import external_url


def courts_html(hints: CourtHints) -> str:
    """«Где судят по этой статье (по базе Airtable): суд — 37 дел (сайт суда); …» — empty
    where the base has no sentence from the place under the article."""
    if not hints.shown:
        return ""
    parts = []
    for hint in hints.shown:
        site = external_url(hint.site)
        link = f' (<a href="{escape(site, quote=True)}">сайт суда</a>)' if site else ""
        parts.append(f"{escape(hint.court)} — {hint.cases}{link}")
    rest = hints.total - sum(hint.cases for hint in hints.shown)
    more = f"; другие суды — {rest}" if rest else ""
    return (
        '<p class="muted">Где судят по этой статье из этого места (приговоры в базе Airtable, '
        f"всего {hints.total}): {'; '.join(parts)}{more}.</p>"
    )
