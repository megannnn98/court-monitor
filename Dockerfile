# syntax=docker/dockerfile:1.7
#
# Single-service image for court-monitor (FastAPI + CLI).
# Uses the astral-sh uv image (Python + uv). Production runs against PostgreSQL;
# SQLite is also fine for local dev.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Copy dependency manifests + README (hatchling needs it for metadata) first.
COPY pyproject.toml uv.lock README.md ./

# Install third-party deps only — cached unless pyproject/uv.lock/README change.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra pg --no-install-project

# Application code + config + migrations.
COPY src ./src
COPY config ./config
COPY alembic.ini ./
COPY migrations ./migrations

# Install the project itself on top of the cached deps.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra pg

# Non-root user for the running process.
RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app
USER app

EXPOSE 8000

# Run migrations on container start, then launch the API. `alembic upgrade head`
# is idempotent, so this is safe across restarts.
CMD ["sh", "-c", "court-monitor migrate && uvicorn court_monitor.api.app:app --host 0.0.0.0 --port 8000"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); sys.exit(0)" || exit 1
