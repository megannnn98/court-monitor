"""The FastAPI application: configuration check, request context, static files, routers.

Creating the application opens no connection and runs no migration: the database is
reached per request through `web.dependencies.get_db`, and migrations are a separate
deployment step (ADR 0014).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from observability import configure_logging
from settings import ApplicationConfigurationError, ApplicationSettings
from web.csrf import same_origin_only
from web.middleware import request_context
from web.routers import (
    about,
    airtable,
    articles,
    candidates,
    dossier,
    entities,
    health,
    investigations,
    monitoring,
    operations,
    persons,
    political,
    publications,
    review,
    reviews,
    rosfinmonitoring,
    sentences,
    status,
)
from web.ui import about as ui_about
from web.ui import airtable as ui_airtable
from web.ui import ask as ui_ask
from web.ui import base_unnamed as ui_base_unnamed
from web.ui import candidates as ui_candidates
from web.ui import cycle as ui_cycle
from web.ui import disputes as ui_disputes
from web.ui import dossier as ui_dossier
from web.ui import entities as ui_entities
from web.ui import junk_holds as ui_junk_holds
from web.ui import logs as ui_logs
from web.ui import management as ui_management
from web.ui import officials as ui_officials
from web.ui import officials_list as ui_officials_list
from web.ui import overview as ui_overview
from web.ui import people_export as ui_people_export
from web.ui import persons as ui_persons
from web.ui import political as ui_political
from web.ui import publications as ui_publications
from web.ui import queue as ui_queue
from web.ui import rfm_list as ui_rfm_list
from web.ui import sentences as ui_sentences
from web.ui import unnamed as ui_unnamed
from web.ui import wiki as ui_wiki

logger = logging.getLogger("api")


def _operation_id(route: APIRoute) -> str:
    """Keep generated client names stable while versioned routes are added gradually."""
    return f"{route.name}_v1"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Refuse to start with an invalid configuration (every problem listed).

    Dependencies are not required here: a database that is still starting makes
    `/health/ready` report unavailable instead of crashing the process.
    """
    configure_logging()
    try:
        ApplicationSettings.from_env()
    except ApplicationConfigurationError as exc:
        logger.error("event=config_invalid problems=%s", exc.problems)
        raise
    logger.info("event=api_started")
    yield
    logger.info("event=api_stopped")


app = FastAPI(
    title="Court Monitor API",
    description="Read-only API for court-monitor data",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory="src/static"), name="static")
# No CORS middleware: the API is meant for private deployment behind a reverse
# proxy (ADR 0014); browsers on other origins are not a supported client. Missing CORS
# does not stop a cross-site form POST, so an unsafe request must come from the
# console's own origin (ADR 0022). Registered first, so it runs inside the request
# context and its refusal carries the request id.
app.middleware("http")(same_origin_only)
app.middleware("http")(request_context)

# In the order the routes were declared before the split: a request matches the first
# route that fits, so the order keeps every URL resolving as it did.
for module in (
    persons,
    articles,
    candidates,
    rosfinmonitoring,
    reviews,
    ui_persons,
    ui_wiki,
    ui_candidates,
    ui_cycle,
    ui_management,
    ui_logs,
    ui_overview,
    ui_dossier,
    ui_entities,
    ui_people_export,
    ui_disputes,
    ui_queue,
    ui_publications,
    ui_unnamed,
    ui_base_unnamed,
    ui_junk_holds,
    ui_ask,
    ui_sentences,
    ui_political,
    ui_rfm_list,
    ui_officials,
    ui_about,
    ui_airtable,
    ui_officials_list,
    airtable,
    operations,
    monitoring,
    health,
    investigations,
):
    app.include_router(module.router)

# The React migration consumes a versioned, read-only API.  Keep the original routes
# untouched for existing automation while the frontend moves page by page.  Routers
# with mutations deliberately stay out until the production authentication and CSRF
# contract is agreed.
for module in (
    persons,
    articles,
    candidates,
    rosfinmonitoring,
    monitoring,
    operations,
    health,
    # Only under /api/v1: the legacy pages render the same data themselves.
    about,
    status,
    entities,
    publications,
    political,
    dossier,
    sentences,
    review,
):
    app.include_router(module.router, prefix="/api/v1", generate_unique_id_function=_operation_id)
