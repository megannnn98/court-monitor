# Application image: API (uvicorn), Dagster webserver/daemon/run workers, migrations.

# The React console (`frontend/`), built once and served by the API (`web.spa`). The
# generated API client is committed, so the build needs no Python.
FROM node:24-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    DAGSTER_HOME=/opt/dagster/dagster_home

WORKDIR /app

# No compiler or libpq: every Python dependency installs from a wheel (psycopg and
# psycopg2-binary bundle libpq). DejaVu is the Cyrillic font of the PDF exports.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core plantuml \
    && apt-get clean

# The published Rosfinmonitoring list is served under a certificate from a Russian
# authority that is in nobody's default trust store, so `download_rf_list()` cannot
# verify it. The root and the intermediate it needs are pinned in `docker/certs` with
# their fingerprints and expiry — see the README there. TLS verification stays on: this
# list decides who the system reports as being on the published перечень, and accepting
# it unchecked would mean accepting anything served in its place.
COPY docker/certs/ /usr/local/share/ca-certificates/
RUN update-ca-certificates

# Semantic indexing needs sentence-transformers (large); opt in with
# `docker compose build --build-arg INSTALL_SEMANTIC=1`. compose.gpu.yaml sets it.
ARG INSTALL_SEMANTIC=0
COPY pyproject.toml uv.lock ./
# Keep downloaded wheels in BuildKit's cache, outside the image layer. This avoids
# repeating large GPU package downloads when dependency installation runs again.
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    groups=""; \
    if [ "$INSTALL_SEMANTIC" = "1" ]; then groups="$groups --group semantic"; fi; \
    uv sync --frozen --no-default-groups --no-install-project $groups

# PlantUML draws every diagram but a sequence one with Graphviz, which the package only
# recommends; WeasyPrint prints the wiki to PDF with Pango. Their own layer, so adding
# them does not rebuild the dependency layer above.
RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz libpango-1.0-0 libpangoft2-1.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY src ./src
COPY --from=frontend /frontend/dist ./frontend/dist
ENV FRONTEND_DIST=/app/frontend/dist
COPY migrations ./migrations
COPY docs/wiki ./docs/wiki
COPY alembic.ini ./
COPY docker/dagster/dagster.yaml docker/dagster/workspace.yaml /opt/dagster/dagster_home/

# What this image was built from, for «О системе» (web.build_info). The image carries no
# .git, so the commit is stamped at build time: `docker build --build-arg
# BUILD_COMMIT=$(git rev-parse --short HEAD) .`. Without the args the page says
# «неизвестно», which is the honest answer for a working copy.
ARG BUILD_COMMIT=""
ARG BUILD_TIME=""
# `git describe --tags --always`: the release tag the commit stands on, or after.
ARG BUILD_TAG=""
ENV BUILD_COMMIT=$BUILD_COMMIT \
    BUILD_TIME=$BUILD_TIME \
    BUILD_TAG=$BUILD_TAG

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
