# Task: `@enbv2022` candidates — what is already done and what is left

Rewritten on 2026-09-20 against the code. The first version of this brief (2026-09-17)
was written before ADR 0017 landed and is superseded: it proposed adding `@enbv2022` to
`channels.csv`, pointed at `src/api.py` line numbers that no longer exist, and asked for
an audit that has since been done and acted on.

## Header

Repository: `/home/b/Documents/ebnv`. Branch `main`, tracking `origin/main`.

Allowed change scope: narrow changes under `src/`, `tests/`, `docs/wiki/`,
`src/sources/telegram/channels.csv`, audit artifacts under `var/enbv2022_audit/`.
Do not mutate the production DB without an explicit user `go`.

Checks used here:

```bash
uv run ruff check src tests evaluation
uv run ruff format --check src tests evaluation
uv run mypy --strict src tests
TEST_DATABASE_URL=postgresql+psycopg://court_monitor:court_monitor_test@127.0.0.1:5434/court_monitor_test uv run pytest
```

The test suite needs the `ebnv-pgvector-test` container (127.0.0.1:5434); without
`TEST_DATABASE_URL` every database test skips silently.

## Settled: the channel is output, not a source

`src/channel_feed/published.py:1-5` and ADR 0017 state it: the customer's channel
`@enbv2022` is the product's output. It is read only to keep already published people out
of the queue. Do not add it to `src/sources/telegram/channels.csv` — that would feed our
own posts back in as evidence for our own candidates.

## Settled: the audit of 2026-08-01 … 2026-09-17 and its fix

ADR 0017 (`docs/adr/0017-channel-feed-sources.md:8-25`) holds the audit the old brief
asked for: the channel named eight people, the pipeline had found one.

| Person | In our sources | Why not found |
|---|---|---|
| Светлана Савельева | 5 articles | found |
| Андрей Смирнов, Сергей Ярош, Дмитрий Коротков | none | old cases, or never in the news we read |
| Мамут Белялов | Mediazona's Telegram, unnamed | the name was only on kommersant.ru |
| Иван Любшин, Арсений Турбин, Евгений Поливко | yes | on the Rosfinmonitoring list; «Евгения Поливко» read as a woman |

What was built in response:

- two sources — `memopzk-figurants` (Memorial's figurant registry, 7 138 cards) and
  `kommersant` (`src/sources/source_registry.py:112-139`);
- the queue `/ui/channel` (`src/web/ui/channel.py`): politically persecuted people the
  channel has not published, any Rosfinmonitoring status, with a draft post each;
- name suggestions for unnamed news (`src/channel_feed/unnamed.py`).

## Code map (the old brief's line numbers are dead)

`src/api.py` is 30 lines now; the routes and the operator UI were split in `e7015c0`.

- candidate definition — `src/candidates/service.py:47` (`get_candidates`), default RF
  statuses `src/candidates/service.py:30-32` (only `NOT_MATCHED`; a missing match record
  is not a confirmed absence, `service.py:150-158`);
- the customer's view (period, administrative cases, order, news link) —
  `src/web/candidate_rows.py:163` (`_candidate_rows`);
- page and exports — `src/web/ui/candidates.py`;
- JSON API — `src/web/routers/candidates.py`;
- Telegram discovery — `src/sources/telegram/source_adapter.py` (window
  `TELEGRAM_HISTORY_DAYS`, default 30 days, now an environment variable).

View filters that hide a candidate before `limit` does, and that the old brief's
root-cause list did not mention:

- news period: default the last 45 days (`src/web/candidate_rows.py:63`, applied at
  `candidate_rows.py:194-199`) — a person whose latest news is older is not shown;
- `include_administrative=False`: a person whose every political article is from КоАП is
  dropped (`candidate_rows.py:157-161`);
- `limit` (default 100) truncates the page; the «Найдено» line counts all rows.

## Done on 2026-09-20

- `TELEGRAM_HISTORY_DAYS` is a real environment variable
  (`src/sources/telegram/source_adapter.py:24`), passed into every Telegram source
  (`src/sources/source_registry.py:146-168`), validated at process start and printed by
  `ApplicationSettings.redacted()` (`src/settings.py`). A one-time backfill no longer
  needs a code edit; documented in `docs/wiki/Ingestion.md:30`.
- CSV and PDF exports of `/ui/candidates` now read the page's filters (period,
  administrative cases) and its `limit`, so the three download buttons no longer give
  three different sets of people (`src/web/ui/candidates.py:112-140`). The Excel export
  keeps ignoring `limit` on purpose. Tests:
  `tests/app/test_candidates_xlsx_export.py` (CSV/PDF filters, page limit, links),
  `tests/sources/test_telegram_source.py` (wider window, environment parsing).

## Done on 2026-09-23

- Fixed candidate filtering to exclude administrative fines from long-term political
  prisoners. The issue: when the classifier marks someone political based on article
  text keywords (e.g. "признаки политического преследования"), but their actual events
  are administrative fines, they would still appear in `/ui/candidates` with default
  filters. Example: Антонина Зимина (ID 5939) — political prisoner since 2022, but
  recent news was about a fine for beating, not a criminal case.

  Changed `src/web/candidate_rows.py`:
  - `_is_administrative_only(reasons, event_type)` now checks if the most recent event
    is a `fine` (unless there's an explicit УК charge)
  - `_has_criminal_events(event_type, reasons)` replaces `_is_criminal_only()` — returns
    True if the most recent event is NOT a fine, or if there's a criminal charge
  - Updated `_candidate_rows()` to pass event_type to both filter functions

  The key insight: event type matters more than persecution reasons when the classifier
  relies on article text keywords. A `fine` event without a criminal charge is
  administrative, regardless of what the article says about "political persecution".

  Verified: Zimina (event_type='fine', no УК charges) is now correctly excluded with
  `criminal_only=True` or `include_administrative=False`. Real criminal cases
  (event_type='sentence' with УК charge) are correctly included.

## Left open (needs a user `go`)

Whether `memopzk-figurants` and `kommersant` actually close the seven people the audit
lists. This is a pipeline question, not a code question: it needs ingestion plus
extraction, resolution, Rosfinmonitoring matching and classification over the new
sources on the working database, then a fresh look at `/ui/candidates` and `/ui/channel`.

Command sequence for that run (do not run without `go`; `--limit 100` defaults are never
enough — `src/main.py:182-285`):

```bash
uv run python src/main.py discover-and-ingest --source memopzk-figurants --limit 8000
uv run python src/main.py discover-and-ingest --source kommersant --limit 500
uv run python src/main.py extract-entities --limit 10000
uv run python src/main.py resolve-people --limit 10000
uv run python src/main.py match-rosfinmonitoring --limit 10000
uv run python src/main.py classify-persecution --limit 10000
```

Then, for the report, compare the eight people of ADR 0017 against `/ui/candidates`
(`date_from=` empty, `include_administrative=1`) and `/ui/channel`, and write
`var/enbv2022_audit/report.md` with one root cause per person still missing.

## Stop conditions

- No production DB mutation, rebuild or backfill without an explicit `go`.
- Public Telegram preview only: no login, no private API.
- Do not widen the candidate definition (`political` plus RF `not_matched`) to make a
  source look better; `needs_review`, `ambiguous`, `insufficient_data` and «never checked»
  stay out of `/ui/candidates` by design. `/ui/channel` is the view that shows them.
- A person who is missing because the case may not be political is a product decision,
  not a pipeline bug: put them in the report, do not tune thresholds.
- Extraction or normalization behaviour changes need a version bump and a rebuild plan.
