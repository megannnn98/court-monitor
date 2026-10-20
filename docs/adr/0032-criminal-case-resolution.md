# ADR 0032: Criminal cases are formed from events by conservative rules

## Status

Accepted, 2026-10-20. Builds on [ADR 0024](0024-entity-groups-are-the-canonical-identity.md)
(the identity is the entity group) and [ADR 0031](0031-data-ownership-and-package-apis.md)
(one owner per table).

## Context

The database knew events (`extracted_events`) and people (`entity_groups`), and one
grouping of reports of a sentence (`results.queries.sentence_cases`), but not the criminal
case itself: a detention of 2024 and a sentence of 2026 against one person were two
unrelated rows. A case needs an id that does not change, and the events that belong to it,
whatever their dates and publications.

Two constraints decide the design. The ids of events and of groups change on every rebuild,
so nothing may be anchored on them. And a wrong merge of two prosecutions is worse than two
cases left apart, so every doubt resolves to «apart».

## Decision

**A new package, `court-cases` (import `cases`)**, above processing and ingestion: it reads them through `processing.case_events` and `ingestion.read`, owns its three
tables and is read by the application only. It adds no extraction and no identity: events are
the extractor's, people are `entity_groups`.

**Tables** (`cases.storage.models`, one migration):

- `criminal_cases`: a UUID that is never reassigned, `preliminary`, `case_number`, a short
  description, the algorithm version.
- `case_event_links`: one row per event, keyed by `evidence_key`, the sha256 of the article,
  the step and the words of the event (not by the event's id, which a re-extraction changes).
  `event_id` is `ON DELETE SET NULL`; the row keeps what it was scored on (`evidence`), the
  score, the features that tied it (`reasons`) and the cases it matched but was not joined to
  (`candidates`).
- `case_participants`: the people an event names, by `entity_groups.key` (stable across
  rebuilds), with the role `figurant` only when the roles step says so.

**Matching** (`cases.matching`, pure): an event joins a case when one of its members matches
by rule and nothing contradicts. A missing value counts neither for nor against.

- Number: the same case number and a shared court, body or place (a bare number never).
- One step reported twice: the same person and the same words, or the same day and one more
  piece of evidence, or two pieces of evidence for undated reports.
- Another step: the same person, the exact article part, and a shared court, body or place.

A name with an article is never enough. A contradiction forbids the tie: two case numbers, a
number the scopes contradict, two dates of one step, a verdict dated before an arrest. Two
cases that both match are an ambiguity: the event opens a case of its own, the candidates are
recorded, no case is changed. Cases are never merged.

People who are only mentioned (judges, lawyers, officials marked by the operator) are never
targets, so they join nothing. An event without a person never joins a case.

**The run** (`cases.service.resolve`, CLI `resolve-cases [--batch-size N] [--dry-run]`) takes
the events in id order by batches, skips those with an active link, sorts a batch by its
events' own date, holds one advisory lock per batch and commits per batch. The first run
forms the cases of everything accumulated; later runs take what is new; both are idempotent.
A re-extracted event is found again by its key and tied back to its case; one whose people
changed so that it shares none with its case leaves for a case of its own. After a whole pass
a link whose event was not found again is deactivated and kept as history. `--dry-run` does
the same work in one transaction and rolls it back.

**API**: `cases.read` (DTOs) and `GET /api/v1/cases`, `GET /api/v1/cases/{case_id}`: the list,
a person's cases, and for one case its events with the reasons, its participants and its
publications. No UI.

## Consequences

- **Order of arrival matters between batches.** Inside a batch events are processed in the
  order of their dates, so the same events give the same cases. If event B ties A and C that
  do not tie each other, and B arrives in a later batch than A and C, B is an ambiguity and the
  three stay apart; with the three in one batch they are one case. No automatic repair of
  that: it would be a merge.
- **Misses are the price.** The extractor reads no case numbers and no circumstances of a
  case, so the features are the quote, the article part, the court, the body and the place. On
  28 hand-built pairs (not a corpus sample) there are 0 wrong ties and 5 of 13 true pairs
  missed: those with thin evidence (a place only, a charge that changed between steps).
- **A group key that a rebuild changes** (a renamed person) cuts the participant from the
  person until the next run re-reads the event.
- **«Region»** is the places the event names (`location` mentions), not a federal subject.
- A link never moves to another case because a better match appeared later; only a person
  change moves it out. Correcting a case by hand is out of scope.
- `resolve-cases` is a separate command, not a step of the operator cycle.
