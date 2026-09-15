# ADR 0014: Production-like deployment

## Status

Accepted, 2026-09-15. Builds on ADR 0011 (Qdrant as derived index) and ADR 0013
(automated monitoring); changes no domain decision.

## Context

Every component runs locally (`uv run`), and monitoring already has a compose
profile, but there was no way to start the whole system — PostgreSQL, Qdrant,
API and Dagster — as one repeatable deployment. Three questions had to be
answered for that: who applies schema migrations, how an orchestrator tells a
dead process from a degraded dependency, and how the API is exposed given it has
no authentication.

## Decision

### One image, several roles

A single image (`court-monitor:local`, `Dockerfile`) runs the API (default
`CMD`: uvicorn), the Dagster webserver, daemon and run workers, and the
migration step. Deployment images install no optional uv groups: `semantic`
(sentence-transformers) is opt-in with `--build-arg INSTALL_SEMANTIC=1`, `mcp`
is never installed in the image (the MCP server is a local stdio process).

### Compose profiles

| Profile | Services |
|---|---|
| (none) | PostgreSQL — development and tests |
| `semantic` | + Qdrant |
| `monitoring` | + Dagster DB init, webserver, daemon |
| `api` | + FastAPI (uvicorn) |
| `production` | PostgreSQL, Qdrant, API, Dagster |
| `migrate` | one-shot `alembic upgrade head` |

Start order:

```bash
docker compose --profile production build
docker compose up -d postgres
docker compose run --rm migrate
docker compose --profile production up -d
```

### Migrations are an explicit step

Neither API workers nor Dagster run `alembic upgrade`. Several API workers or a
restarting container must not race on DDL, and a failed migration must stop the
rollout visibly instead of looping in a restart policy. The `migrate` service
is run by the operator before starting the new version. Readiness reports a
schema that is not at the head this code expects as `unavailable`, so an
unmigrated deployment never looks healthy.

### Liveness is not readiness

- `GET /health/live` — the process answers; checks nothing. The container
  healthcheck uses it, so a database outage never makes Docker restart a
  healthy API process (restarts would not fix the database and only drop
  in-flight requests).
- `GET /health/ready` — PostgreSQL reachable and schema at head are required
  (`unavailable`, HTTP 503 otherwise). Qdrant (when `QDRANT_URL` is set), Together
  AI configuration and stale monitoring runs are reported as `degraded`
  (HTTP 200): structured research still works without them. Details contain no
  connection strings or exception messages. `/health` stays as a compatibility
  alias of liveness.

Dagster webserver is checked with `/server_info`; Qdrant with `/readyz` (the
image has no curl, so the check uses bash `/dev/tcp`).

### Connection pool per process

`DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_TIMEOUT` and
`DATABASE_CONNECT_TIMEOUT` apply per process (one API worker or one Dagster run
worker), with `pool_pre_ping`. They are passed to both the API and the Dagster
containers. Total connections are roughly
`(pool_size + max_overflow) × processes`; keep it under PostgreSQL
`max_connections`.

### Graceful shutdown

Containers run with `init: true` so signals reach the Python process.
uvicorn gets `--timeout-graceful-shutdown 20` inside a 30 s stop grace period.
Dagster run workers get 60 s: on SIGTERM a monitoring run finishes as failed;
a run killed harder is aborted as stale by the next run (ADR 0013).

### Private deployment

All published ports are bound to `127.0.0.1` (PostgreSQL `5433`, Qdrant `6333`,
API `8001`, Dagster `3000`). The API has no authentication, authorization or
rate limiting; it must not be exposed beyond the host except through a reverse
proxy that adds them. This is a deployment constraint, not a solved problem.

## Consequences

- One build serves all roles; API and Dagster always run the same code version.
- A rollout is: build, `run --rm migrate`, `up -d`. Forgetting the migration
  shows as `/health/ready` 503, not as runtime SQL errors.
- Optional dependencies degrade instead of taking the API down.
- Not addressed: TLS, authentication, secrets management (values come from
  `.env`), backups, multi-host deployment, horizontal API scaling beyond one
  container, log shipping.
