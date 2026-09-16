# Application image: API (uvicorn), Dagster webserver/daemon/run workers, migrations.
FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    DAGSTER_HOME=/opt/dagster/dagster_home

WORKDIR /app

# psycopg2 (a project dependency) builds from source.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev libpq-dev fonts-dejavu-core \
    && apt-get clean

# Semantic indexing needs sentence-transformers (large); opt in with
# `docker compose build --build-arg INSTALL_SEMANTIC=1`.
ARG INSTALL_SEMANTIC=0
COPY pyproject.toml uv.lock ./
RUN if [ "$INSTALL_SEMANTIC" = "1" ]; then \
        uv sync --frozen --no-default-groups --no-install-project --group semantic; \
    else \
        uv sync --frozen --no-default-groups --no-install-project; \
    fi

COPY src ./src
COPY migrations ./migrations
COPY docs/wiki ./docs/wiki
COPY alembic.ini ./
COPY docker/dagster/dagster.yaml docker/dagster/workspace.yaml /opt/dagster/dagster_home/

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
