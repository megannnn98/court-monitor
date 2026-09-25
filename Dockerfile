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

# No compiler or libpq: every Python dependency installs from a wheel (psycopg and
# psycopg2-binary bundle libpq). DejaVu is the Cyrillic font of the PDF exports.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core plantuml \
    && apt-get clean

# Semantic indexing needs sentence-transformers (large); opt in with
# `docker compose build --build-arg INSTALL_SEMANTIC=1`. The person recognizer (GLiNER,
# torch with CUDA) likewise with INSTALL_NER=1; compose.gpu.yaml sets both.
ARG INSTALL_SEMANTIC=0
ARG INSTALL_NER=0
COPY pyproject.toml uv.lock ./
# Keep downloaded wheels in BuildKit's cache, outside the image layer. This avoids
# repeating large GPU package downloads when dependency installation runs again.
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    groups=""; \
    if [ "$INSTALL_SEMANTIC" = "1" ]; then groups="$groups --group semantic"; fi; \
    if [ "$INSTALL_NER" = "1" ]; then groups="$groups --group ner"; fi; \
    uv sync --frozen --no-default-groups --no-install-project $groups

# PlantUML draws every diagram but a sequence one with Graphviz, which the package only
# recommends; WeasyPrint prints the wiki to PDF with Pango. Their own layer, so adding
# them does not rebuild the dependency layer above.
RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz libpango-1.0-0 libpangoft2-1.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY src ./src
COPY migrations ./migrations
COPY docs/wiki ./docs/wiki
COPY alembic.ini ./
COPY docker/dagster/dagster.yaml docker/dagster/workspace.yaml /opt/dagster/dagster_home/

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
