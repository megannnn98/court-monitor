# ADR 0015: Operator console safety

## Status

Accepted, 2026-09-16.

## Decision

The local web UI is an Operator console, not a dashboard and not a full browser
clone of every CLI command. It should use a dense, work-focused layout for
scanning review queues, checking evidence and launching routine pipeline work.
Safe read-only views open directly. Every mutating or long-running operation
starts from a preview page and requires explicit confirmation before it changes
the live database or launches pipeline work.

## Consequences

- Search, Person cards, article evidence, candidates, monitoring status,
  findings and configuration checks can run directly.
- Ingestion, extraction, entity resolution, persecution classification,
  Rosfinmonitoring import/matching and derived monitoring runs need a confirm
  step.
- Developer and evaluation tools can stay CLI-only unless they become routine
  operator workflows.
- The visual design should prioritize compact tables, filters, clear action
  placement, evidence links and short page instructions over marketing-style
  presentation.
- Every page should include a short contextual instruction: what this page is
  for, the next action the operator should take, and a warning only when the
  page can mutate data or freshness matters. Longer help belongs in wiki docs.
