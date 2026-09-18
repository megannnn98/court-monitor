"""Entry point of the API: `uvicorn api:app`.

The application lives in the `web` package (`web.app`). The names below are the ones
tests and deployments reach through `api`: the application and the dependencies they
override.
"""

from web.app import app
from web.dependencies import (
    _get_research_graph,
    _get_session_factory,
    get_db,
    get_operation_registry,
    get_published_name_keys,
    get_readiness_checker,
    get_research_query_graph,
    get_research_service,
)

__all__ = [
    "_get_research_graph",
    "_get_session_factory",
    "app",
    "get_db",
    "get_operation_registry",
    "get_published_name_keys",
    "get_readiness_checker",
    "get_research_query_graph",
    "get_research_service",
]
