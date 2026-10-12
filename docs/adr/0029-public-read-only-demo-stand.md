# ADR 0029: A public, read-only demo stand

## Status

Accepted, 2026-10-12. An exception to [ADR 0014](0014-production-deployment.md),
which keeps the API on localhost and lets it out only behind a proxy with
authentication; changes no domain decision.

## Context

The owner wants to show the site to a friend and put it on their own server. The
console has no login (`src/web/csrf.py`): whoever reaches the port is trusted, and it
can start pipeline stages, sync Airtable and edit review data. The production
deployment answers that by never leaving the host.

A demo has to look like the real thing, so it needs real data; and a friend should
open it by a link, with no password to pass on.

## Decision

A **demo stand** (`CONTEXT.md`) is a second deployment of the same repository, apart
from production:

- **Data.** A copy of the production database as it is, real names included, taken
  once by hand (`pg_dump` → `pg_restore`, read-only on production) and refreshed only
  by running that again. Nothing links the copy back to production.
- **Access.** Open to anyone with the link, no login. Search engines are told not to
  index it (`X-Robots-Tag: noindex, nofollow`, `robots.txt` `Disallow: /`).
- **Read-only.** `DEMO_READ_ONLY=1` makes the API refuse every request that is not a
  read, before its route runs, and the console say so above every page. Starting an
  investigation is refused too: the console has no page that starts one, and an
  address open to anyone would let strangers load the search on a shared server. The
  investigations already in the copy are there to read.
- **No outside calls.** No model key (OpenRouter, Anthropic, Together) is set, no
  Telegram channel is searched (`INVESTIGATION_TELEGRAM_CHANNELS=none`), and Dagster
  is not started, so nothing monitors, ingests or spends.
- **Where.** The production server, as its own compose project (`court-monitor-demo`)
  with its own volume, port and nginx site, on its own subdomain. Production
  containers are not touched.

## Considered and rejected

- **Synthetic or anonymised data.** Safer to publish; rejected by the owner, the demo
  is to show the real material.
- **A password on the proxy, or an address allow-list.** Rejected by the owner: the
  friend opens it by a link.
- **Leaving the console writable and restoring from the dump.** Any scanner could
  start heavy stages on a server shared with production.
- **A separate repository.** It would drift from `main` after the first commits and
  carry the read-only switch twice.

## Consequences

- Real names of people in criminal cases are public on the demo's address for as
  long as it runs. Taking it down removes the site, not what was already seen or
  cached; `noindex` keeps it out of search results, not out of reach.
- `DEMO_READ_ONLY` is a production code path that is off everywhere but the demo; its
  tests keep both states.
- The demo shares CPU, memory and disk with production, and its database is as large
  as production's.
