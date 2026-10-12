#!/usr/bin/env bash
# The demo stand's copy of the production database (ADR 0029), taken again from scratch.
#
# Run on the server, in the demo's checkout, with `.env.demo` filled in:
#   deploy/demo/snapshot.sh
#
# Production is only read: `pg_dump` in a read-only transaction. The demo's database is
# replaced whole, then migrated to this checkout's schema; its site is stopped meanwhile.
set -euo pipefail
cd "$(dirname "$0")/../.."

PROD_PG=${PROD_PG:-court-monitor-postgres-1}
ENV_FILE=${ENV_FILE:-.env.demo}
demo() { docker compose -p court-monitor-demo -f compose.demo.yaml --env-file "$ENV_FILE" "$@"; }

[ -f "$ENV_FILE" ] || { echo "STOP: no $ENV_FILE (template: deploy/demo/env.example)"; exit 1; }
docker inspect "$PROD_PG" >/dev/null 2>&1 || { echo "STOP: no container $PROD_PG"; exit 1; }

echo "== 1. the demo's database up, its site down"
demo up -d --wait postgres </dev/null
demo stop api </dev/null 2>/dev/null || true

echo "== 2. production -> demo (pg_dump read-only | pg_restore --clean)"
docker exec -e PGOPTIONS="-c default_transaction_read_only=on" "$PROD_PG" \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl' \
  | demo exec -T postgres sh -c \
    'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-acl --exit-on-error'

echo "== 3. migrations of this checkout"
demo run --rm migrate </dev/null

echo "== 4. the site up"
demo up -d --wait api </dev/null
demo exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select count(*) from parsed_articles"' \
  | sed 's/^/publications in the demo: /'
