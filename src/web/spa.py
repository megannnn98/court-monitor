"""The React console, served by the API itself.

The image builds `frontend/` and names the build in `FRONTEND_DIST` (Dockerfile). With it,
the site is the React console: an address no route answers returns `index.html` (the
router picks the page in the browser) or the build's file, and a legacy page that has
moved to React sends the browser to the React page, query kept. The legacy pages not moved
yet, the Excel files, the forms (any method but GET) and the API stay where they are.

Without `FRONTEND_DIST` — the tests, a working copy run with uvicorn — nothing changes:
the legacy pages answer as before.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, RedirectResponse, Response

# The legacy page → its React page (`frontend/src/lib/navigation.ts`).
MOVED = {
    "/": "/work",
    "/ui": "/work",
    "/ui/cycle": "/work",
    "/ui/runs": "/runs",
    "/ui/management": "/runs",
    "/ui/pairs": "/review/pairs",
    "/ui/roles": "/review/roles",
    "/ui/politics-review": "/review/politics",
    "/ui/political": "/political",
    "/ui/investigations": "/investigations",
    "/ui/entities": "/entities",
    "/ui/publications": "/publications",
    "/ui/sentences": "/sentences",
    "/ui/rfm": "/rfm",
    "/ui/candidates": "/candidates",
    "/ui/logs": "/logs",
    "/ui/about": "/about",
}
# A page of one thing: /ui/<kind>/<id> → /<kind>/<id>. The dossier, a publication, a
# person (`/ui/entities/<key>` already leads to the dossier).
ONE_OF = {
    "/ui/investigations/": "/investigations/",
    "/ui/articles/": "/articles/",
    "/ui/persons/": "/persons/",
}
# Never the console's: a missing API route is a 404, not a page.
_NOT_PAGES = ("/api/", "/ui/", "/static/", "/health", "/docs", "/redoc", "/openapi.json")


def dist() -> Path | None:
    """The React build, or None when there is none to serve."""
    named = os.environ.get("FRONTEND_DIST", "")
    root = Path(named) if named else None
    return root if root is not None and (root / "index.html").is_file() else None


def react_address(path: str) -> str | None:
    """Where a legacy page now lives in React; None for a page not moved."""
    if path in MOVED:
        return MOVED[path]
    for legacy, react in ONE_OF.items():
        rest = path[len(legacy) :]
        if path.startswith(legacy) and rest and "/" not in rest:
            return react + rest
    return None


def _console(root: Path, path: str) -> FileResponse:
    """A file of the build, or `index.html` for any other address of the console."""
    file = (root / path.lstrip("/")).resolve()
    if file.is_file() and file.is_relative_to(root.resolve()):
        return FileResponse(file)
    return FileResponse(root / "index.html")


async def react_console(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """With the build: a moved legacy page opens in React, and an address no route answers
    is the console's. Only GET: a form keeps its route, a wrong method its 405."""
    root = dist()
    if root is None or request.method != "GET":
        return await call_next(request)
    path = request.url.path
    target = react_address(path)
    if target is not None:
        query = request.url.query
        return RedirectResponse(f"{target}?{query}" if query else target, status_code=302)
    response = await call_next(request)
    if response.status_code == 404 and not path.startswith(_NOT_PAGES):
        return _console(root, path)
    return response
