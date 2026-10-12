"""The import rules between the modules (ADR 0025, `docs/architecture/modularization-plan.md`).

Every file under `src/` belongs to one module (`MODULE_OF`). Three kinds of rule:

- the layering: an earlier stage never imports a later one, a domain module never
  imports an orchestrator, tooling or an entry point (`FORBIDDEN`);
- the orchestrator reaches a domain module only through its public API
  (`ORCHESTRATOR_MAY_IMPORT`);
- the console and the pipeline commands never import what a module keeps to itself
  (`INTERNAL`).

`KNOWN_VIOLATIONS` is empty and stays empty: a new import that breaks a rule is fixed,
not listed.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

SRC = Path(__file__).parents[1] / "src"

INGESTION = "ingestion"
PROCESSING = "processing"
ANALYSIS = "analysis"
RESULTS = "results"
ORCHESTRATOR = "orchestrator"
INVESTIGATION = "investigation"
SHARED = "shared"
TOOLING = "tooling"
ENTRY_POINT = "entry_point"

# Path prefix under `src/` -> module. The first match wins, so files come before their
# package.
MODULE_OF: tuple[tuple[str, str], ...] = (
    ("ingestion/", INGESTION),
    ("processing/", PROCESSING),
    # A command-line entry: it runs the evaluation tooling of the resolver.
    ("persons/resolution/cli.py", ENTRY_POINT),
    # Person resolution (ER v2): the optional path of ADR 0024, beside processing.
    ("persons/", PROCESSING),
    ("llm/cli_reviewer.py", PROCESSING),
    ("llm/entity_reviewer.py", PROCESSING),
    ("analysis/", ANALYSIS),
    # The operator's reference lists (her Rosfinmonitoring table, her base of known
    # people, officials): data the verdicts are checked against, keyed like mentions.
    ("airtable/", ANALYSIS),
    ("results/", RESULTS),
    # Dagster's code location and its resource build the application: wiring.
    ("orchestrator/dagster/definitions.py", ENTRY_POINT),
    ("orchestrator/dagster/resources.py", ENTRY_POINT),
    ("orchestrator/", ORCHESTRATOR),
    ("investigation/", INVESTIGATION),
    ("db/", SHARED),
    ("monitor_core/", SHARED),
    ("llm/", SHARED),
    ("observability.py", SHARED),
    ("evaluation/", TOOLING),
)

_OUTSIDE_THE_DOMAIN = {TOOLING, ENTRY_POINT}
_ORCHESTRATORS = {ORCHESTRATOR, INVESTIGATION}
# Importing module -> the modules it must not import. Entry points and tooling wire and
# measure everything, so they have no row.
FORBIDDEN: dict[str, set[str]] = {
    INGESTION: {PROCESSING, ANALYSIS, RESULTS} | _ORCHESTRATORS | _OUTSIDE_THE_DOMAIN,
    PROCESSING: {ANALYSIS, RESULTS} | _ORCHESTRATORS | _OUTSIDE_THE_DOMAIN,
    ANALYSIS: {RESULTS} | _ORCHESTRATORS | _OUTSIDE_THE_DOMAIN,
    RESULTS: _ORCHESTRATORS | _OUTSIDE_THE_DOMAIN,
    # Two orchestrators that never call each other: Dagster and the pipeline run the
    # scheduled work, the investigation graph answers one question.
    ORCHESTRATOR: {INVESTIGATION} | _OUTSIDE_THE_DOMAIN,
    INVESTIGATION: {ORCHESTRATOR} | _OUTSIDE_THE_DOMAIN,
    SHARED: {INGESTION, PROCESSING, ANALYSIS, RESULTS} | _ORCHESTRATORS | _OUTSIDE_THE_DOMAIN,
}

KNOWN_VIOLATIONS: frozenset[str] = frozenset()

_DOMAIN_PACKAGES = ("ingestion", "processing", "persons", "analysis", "airtable", "results")

# What the pipeline orchestrator may import of the domain: each module's public API,
# and the registry of sources (their names and kinds).
ORCHESTRATOR_MAY_IMPORT = frozenset(
    {
        "ingestion.service",
        "ingestion.sources.source_registry",
        "processing.service",
        "processing.identity.service",
        "persons.resolution.stage",
        "analysis.service",
        "results.service",
    }
)

# What the investigation graph may import of the domain: the results API and nothing
# else. It reads no table itself and never person resolution: the identity an
# investigation works on is the entity group (ADR 0024).
INVESTIGATION_MAY_IMPORT = frozenset({"results.service"})

# What a module keeps to itself. The console (`web/`, `api.py`) and the pipeline
# commands reach these only through a service; the composition root builds them.
INTERNAL = frozenset(
    {
        # identity: the rules, the rebuild, the model's naming, a person's decisions
        "processing.identity.collector",
        "processing.identity.grouping",
        "processing.identity.normalizer",
        "processing.identity.disputes",
        "processing.identity.overrides",
        "processing.identity.removals",
        # extraction: the pipeline and its storage
        "processing.extraction.pipeline",
        "processing.extraction.persistence",
        "processing.extraction.documents",
        "processing.extraction.extractors",
        "processing.extraction.events",
        "processing.extraction.normalizers",
        # which work is pending: each module's own queries
        "processing.extraction.pending",
        "persons.resolution.pending",
        "analysis.pending",
        "ingestion.stored",
        # storage and matching implementations
        "ingestion.sources.sqlalchemy_persistence",
        "persons.persistence",
        "analysis.rosfinmonitoring.snapshot_lookup",
        "analysis.rosfinmonitoring.matcher",
        "analysis.rosfinmonitoring.matcher_persistence",
        "analysis.rosfinmonitoring.persistence",
        "analysis.persecution.classification_service",
        "results.findings",
        "results.dossier",
        "orchestrator.repository",
        "orchestrator.locks",
    }
)
# Where `INTERNAL` is closed: the console, the API and the commands of the pipeline. The
# older single-module commands (`cli/ingestion.py`, `cli/extraction.py`, `cli_batches.py`
# and the like) still build their module's parts themselves; they are listed in the plan
# as what is left.
CLOSED_TO_INTERNALS = (
    "web/",
    "api.py",
    "main.py",
    "cli/monitoring.py",
    "cli/cycle.py",
    "cli/investigation.py",
)


def _module_of(path: str) -> str:
    for prefix, module in MODULE_OF:
        if path.startswith(prefix):
            return module
    # Everything else at the top of `src/` wires the application.
    return ENTRY_POINT


def _path_of(imported: str) -> str | None:
    """The file or package under `src/` an absolute import names; None for a library."""
    parts = imported.split(".")
    for length in range(len(parts), 0, -1):
        base = "/".join(parts[:length])
        if (SRC / f"{base}.py").is_file():
            return f"{base}.py"
        if (SRC / base).is_dir():
            return f"{base}/"
    return None


def _dotted(target: str) -> str:
    return ".".join(target.removesuffix(".py").strip("/").split("/"))


def _imports(tree: ast.AST, path: str) -> Iterator[str]:
    """Every module the file at `path` imports, by its absolute name: a relative import
    is resolved against the file's own package."""
    package = path.split("/")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            if module:
                yield module
            # `from package import module` names the module only in the alias.
            yield from (f"{module}.{alias.name}" if module else alias.name for alias in node.names)


def _files(*prefixes: str) -> Iterator[tuple[str, ast.AST]]:
    for file in sorted(SRC.rglob("*.py")):
        path = file.relative_to(SRC).as_posix()
        if not prefixes or path.startswith(prefixes):
            yield path, ast.parse(file.read_text(encoding="utf-8"))


def _imported_modules(tree: ast.AST, path: str) -> set[str]:
    """The module files of `src/` a file imports, as dotted names."""
    return {
        _dotted(target)
        for imported in _imports(tree, path)
        if (target := _path_of(imported)) is not None and target.endswith(".py")
    }


def _violations() -> set[str]:
    found: set[str] = set()
    for path, tree in _files():
        forbidden = FORBIDDEN.get(_module_of(path), set())
        for imported in _imports(tree, path):
            target = _path_of(imported)
            if target is not None and _module_of(target) in forbidden:
                # The package of a `from package import name` is reported once, as the name.
                found.add(f"{path} -> {_dotted(target)}")
    return found


def _domain_imports_outside(allowed: frozenset[str], prefix: str) -> list[str]:
    return sorted(
        f"{path} -> {module}"
        for path, tree in _files(prefix)
        if _module_of(path) != ENTRY_POINT
        for module in _imported_modules(tree, path)
        if module.split(".")[0] in _DOMAIN_PACKAGES and module not in allowed
    )


def test_no_import_crosses_a_module_boundary() -> None:
    assert sorted(_violations() - KNOWN_VIOLATIONS) == []


def test_no_violation_is_tolerated() -> None:
    assert KNOWN_VIOLATIONS == frozenset()


def test_every_module_of_the_plan_has_files() -> None:
    # A rule over a module nobody is assigned to would hold of nothing.
    assigned = {_module_of(path) for path, _ in _files()}

    assert {
        INGESTION,
        PROCESSING,
        ANALYSIS,
        RESULTS,
        ORCHESTRATOR,
        INVESTIGATION,
        SHARED,
    } <= assigned


def test_the_orchestrator_reaches_the_domain_only_through_public_apis() -> None:
    assert _domain_imports_outside(ORCHESTRATOR_MAY_IMPORT, "orchestrator/") == []


def test_an_investigation_reaches_the_domain_only_through_the_results_api() -> None:
    assert _domain_imports_outside(INVESTIGATION_MAY_IMPORT, "investigation/") == []


def test_an_investigation_runs_no_sql_of_its_own() -> None:
    offenders = sorted(
        f"{path} -> {imported}"
        for path, tree in _files("investigation/")
        for imported in _imports(tree, path)
        if imported.split(".")[0] in ("sqlalchemy", "db", "psycopg")
    )

    assert offenders == []


def test_dagster_only_schedules_the_orchestrator() -> None:
    # An asset calls one stage of `MonitoringService`; no domain stage lives in Dagster.
    offenders = sorted(
        f"{path} -> {module}"
        for path, tree in _files("orchestrator/dagster/")
        for module in _imported_modules(tree, path)
        if module.split(".")[0] not in ("orchestrator", "application", "settings")
    )

    assert offenders == []


def test_the_console_and_the_pipeline_commands_use_no_module_internals() -> None:
    offenders = sorted(
        f"{path} -> {module}"
        for path, tree in _files(*CLOSED_TO_INTERNALS)
        for module in _imported_modules(tree, path)
        if module in INTERNAL
    )

    assert offenders == []


def test_every_named_module_exists() -> None:
    # A renamed module would leave its rule guarding nothing.
    missing = sorted(
        module
        for module in INTERNAL | ORCHESTRATOR_MAY_IMPORT | INVESTIGATION_MAY_IMPORT
        if not (SRC / f"{module.replace('.', '/')}.py").is_file()
    )

    assert missing == []


def test_a_relative_import_is_read_as_the_module_it_names() -> None:
    source = (
        "from ..identity.service import IdentityService\n"
        "from . import pending\n"
        "from .models import ExtractionRunStatus\n"
        "from ... import analysis\n"
    )

    found = set(_imports(ast.parse(source), "processing/extraction/pipeline.py"))

    assert {
        "processing.identity.service",
        "processing.extraction.pending",
        "processing.extraction.models",
        "analysis",
    } <= found


def test_extraction_knows_neither_persons_nor_entity_groups() -> None:
    # Extraction is what person resolution and entity groups both read: it must not
    # depend on what either makes of it.
    downstream = ("persons", "processing.identity", "processing.roles", "processing.evidence")
    offenders = sorted(
        f"{path} -> {imported}"
        for path, tree in _files("processing/extraction/")
        for imported in _imports(tree, path)
        if imported.startswith(downstream)
    )

    assert offenders == []


def test_identity_knows_nothing_of_person_resolution() -> None:
    # Entity groups are the canonical identity; ER v2 is not built under them (ADR 0024).
    offenders = sorted(
        f"{path} -> {imported}"
        for path, tree in _files("processing/identity/")
        for imported in _imports(tree, path)
        if imported.split(".")[0] == "persons"
    )

    assert offenders == []
