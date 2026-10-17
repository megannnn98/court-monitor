"""«Должностные лица» are a reference list, not a page of their own.

The list of people who must never become a target figurant — officials, defence
lawyers, witnesses — is maintained by hand and synced like any other reference list, so
it lives on «Справочники» next to the sources and the Rosfinmonitoring list. The page
that used to gather the officials found among the articles is gone; a person is still
marked or unmarked on their own card, which is where that decision is made.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()

# Bookmarks and links to the old page keep working.
REFERENCE_URL = "/ui/airtable"


@router.get("/ui/officials", response_class=RedirectResponse)
def officials() -> RedirectResponse:
    """Where the officials list is kept now."""
    return RedirectResponse(REFERENCE_URL, status_code=301)
