# ADR 0030: The modules as distributions of one uv workspace

## Status

Accepted, 2026-10-17. Builds on [ADR 0025](0025-modular-monolith-and-pipeline-orchestrator.md):
the same modules and the same rules, made physical. The map is
[docs/architecture/package-map.md](../architecture/package-map.md).

## Context

After ADR 0025 the code was split into ingestion, processing, analysis, results, the
investigation and the orchestrator, but all of it lived in one `src/` with one
`pyproject.toml`. The boundaries existed only in `tests/test_module_boundaries.py`:
every module could import every library, and nothing showed what a module needs to run
on its own. Several couplings were invisible because everything was on one path:

- `llm/cli_reviewer.py` and `llm/entity_reviewer.py` (shared infrastructure by
  location) implement and import a contract of `persons`;
- `persons/resolution/cli.py`, an entry point inside the domain, imported the evaluation
  tooling, and the production command `resolve-person` took `pipeline_identity` from it;
- the application's command line carried the evaluations (`evaluate-final`,
  `evaluate-er`, the real-world commands, `compare-entity-models`);
- `orchestrator/dagster/resources.py` and `definitions.py` built the application graph
  from inside the orchestrator;
- `orchestrator/operations.py` found `src/main.py` from its own file: the orchestrator
  knew where the application is installed.

## Decision

The repository is one uv workspace. Each module is a distribution with its own
`pyproject.toml`, the third-party libraries it imports and the workspace distributions it
depends on: `court-core`, `court-platform`, `court-ingestion`, `court-processing`,
`court-analysis`, `court-results`, `court-investigation`, `court-orchestrator` under
`packages/`; the application `court-monitor` (composition root, CLI, API, Dagster's code
location) under `apps/`; the evaluation `court-evaluation` under `tools/`. Import names
are unchanged. The modules are installed packages; nothing is found through a path.

Each coupling above was fixed by its owner, not by moving it into shared code:

- the ER v2 reviewers moved to `persons/resolution/` (the boundary test already counted
  them as processing);
- `resolve-person` and the review commands moved to the application
  (`cli/person_resolution.py`), `evaluate-er` to the evaluation, `pipeline_identity` to
  `persons/resolution/surface.py`;
- the evaluations have their own command line, `court-evaluation`;
- Dagster's code location moved to the application (`dagster_location/`); assets, jobs
  and schedules stay in the orchestrator;
- the application gives `OperationRegistry` a `CommandLine` (the program and its working
  directory).

`tests/test_package_graph.py` holds the distributions to the layering of ADR 0025 and
every import to a declared dependency; `scripts/check_package_isolation.py` installs each
distribution alone and imports all of it.

## Consequences

- A package is not a service: one process, one database, typed Python calls. No table,
  migration or schema changed; ORM definitions stay central in `court-platform` (who
  owns which table is a later task).
- The production image installs `court-monitor` and what it declares, not the
  evaluation or the dev tools. The `semantic` libraries are an extra of
  `court-processing` (and of the application and the evaluation through it).
- The evaluation commands moved from `python src/main.py` to `court-evaluation` with the
  same arguments. Every other command is unchanged; `court-monitor` is the new name of
  `python src/main.py`, which still works through a launcher left in `src/`.
- Tests that need nothing of `tests/` live next to their distribution
  (`packages/*/tests`); the rest stay where they were.
- The application runs from the checkout (it reads `docs/wiki`, `migrations` and its
  `static/` from there), so it is installed editable, in the image too.
