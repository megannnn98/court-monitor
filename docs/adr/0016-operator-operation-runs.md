# ADR 0016: Operator operation runs

## Status

Accepted, 2026-09-16.

## Decision

Mutating and long-running Operator console actions run as operation runs, not as
work held inside the confirming HTTP request. Confirming an operation creates a
run, then the UI follows a run detail page with status, parameters, timing,
summary and errors.

## Consequences

- Pipeline work can outlive a browser refresh or client disconnect.
- The UI can show progress and failure context consistently across operation
  types.
- Operations that are not safe to overlap must reject a second concurrent run of
  the same type.
- The first implementation can be local and in-process, but the web contract
  stays run-oriented so it can later move to a stronger worker.

## Update, 2026-09-18: runs in PostgreSQL

Runs moved from process memory to `operator_operation_runs` (see
[Data-Model](../wiki/Data-Model.md)): a restart keeps them, every API process sees the
same runs, and a partial unique index on `operation_name` for `pending`/`running`
rejects a second live run of an operation at the database level. Execution is still a
local thread with a subprocess, now with a heartbeat; a run whose heartbeat stops for
5 minutes becomes `interrupted`, and its late result cannot overwrite that.
