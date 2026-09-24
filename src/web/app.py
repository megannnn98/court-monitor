"""The FastAPI application: configuration check, request context, static files, routers.

Creating the application opens no connection and runs no migration: the database is
reached per request through `web.dependencies.get_db`, and migrations are a separate
deployment step (ADR 0014).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from observability import configure_logging
from settings import ApplicationConfigurationError, ApplicationSettings
from web.middleware import request_context
from web.routers import (
    articles,
    candidates,
    health,
    monitoring,
    operations,
    persons,
    research,
    reviews,
    rosfinmonitoring,
    search,
)
from web.ui import candidates as ui_candidates
from web.ui import persons as ui_persons
from web.ui import wiki as ui_wiki

logger = logging.getLogger("api")


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
# proxy (ADR 0014); browsers on other origins are not a supported client.
app.middleware("http")(request_context)

# In the order the routes were declared before the split: a request matches the first
# route that fits, so the order keeps every URL resolving as it did.
for module in (
    persons,
    articles,
    search,
    candidates,
    research,
    rosfinmonitoring,
    reviews,
    ui_persons,
    ui_wiki,
    ui_candidates,
    operations,
    monitoring,
    health,
):
    app.include_router(module.router)
