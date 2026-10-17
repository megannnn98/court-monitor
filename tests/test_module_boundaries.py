"""The import rules between the modules (ADR 0025, `docs/architecture/modularization-plan.md`).

Every file of the workspace's code (each distribution's `src/`, named relative to it:
`tests/support/workspace.py`) belongs to one module (`MODULE_OF`). Three kinds of rule:

- the layering: an earlier stage never imports a later one, a domain module never
  imports an orchestrator, tooling or an entry point (`FORBIDDEN`);
- the orchestrator reaches a domain module only through its public API
  (`ORCHESTRATOR_MAY_IMPORT`);
- the console and the pipeline commands never import what a module keeps to itself
  (`INTERNAL`).

`KNOWN_VIOLATIONS` is empty and stays empty: a new import that breaks a rule is fixed,
not listed. The distributions are held to the same layering by
`tests/test_package_graph.py`.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

import pytest
from support.workspace import SOURCE_ROOTS, module_file, source_files

INGESTION = "ingestion"
PROCESSING = "processing"
ANALYSIS = "analysis"
RESULTS = "results"
ORCHESTRATOR = "orchestrator"
INVESTIGATION = "investigation"
SHARED = "shared"
TOOLING = "tooling"
ENTRY_POINT = "entry_point"

# Path prefix under a distribution's `src/` -> module. The first match wins, so files come before their
# package.
MODULE_OF: tuple[tuple[str, str], ...] = (
    ("ingestion/", INGESTION),
    ("processing/", PROCESSING),
    # Person resolution (ER v2): the optional path of ADR 0024, beside processing.
    ("persons/", PROCESSING),
    ("persons/resolution/cli_reviewer.py", PROCESSING),
    ("persons/resolution/entity_reviewer.py", PROCESSING),
    ("analysis/", ANALYSIS),
    # The operator's reference lists (her Rosfinmonitoring table, her base of known
    # people, officials): data the verdicts are checked against, keyed like mentions.
    ("airtable/", ANALYSIS),
    ("results/", RESULTS),
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

# What the investigation graph may import of the domain: the results API, and the
# read-only search of configured sources (ADR 0027) — not the source adapters, not the
# rest of ingestion. It reads no table itself and never person resolution: the identity
# an investigation works on is the entity group (ADR 0024).
INVESTIGATION_MAY_IMPORT = frozenset({"results.service", "ingestion.search"})

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
        "results.search",
        "results.figurant",
        "orchestrator.repository",
        "orchestrator.locks",
    }
)
# Where `INTERNAL` is closed: the console, the API and the commands of the pipeline. The
# older single-module commands build their module's parts themselves and are held to
# their own module instead (`MODULE_TOOLS`).
CLOSED_TO_INTERNALS = (
    "web/",
    "api.py",
    "main.py",
    "cli/monitoring.py",
    "cli/cycle.py",
    "cli/investigation.py",
)


# What the console (`web/`, `api.py`) reads of the domain besides the services: each
# module's public read contract — its vocabulary (labels, constants), its read models
# (a row as a page shows it) and a person's decisions on it. They are named here, so a
# page that starts to read anything else fails the test below; a module keeps the right
# to change what is not listed. One service per page was not built: it would copy each
# file's names (ADR 0025).
CONSOLE_READ_MODELS = frozenset(
    {
        # the services
        "analysis.service",
        "processing.identity.service",
        # «Тест модели» of «Модель извлечения»: one call of the names step's extractor
        "processing.identity.model_probe",
        "results.candidates.service",
        "airtable.service",
        # vocabularies and read models
        "airtable.config",
        "airtable.files",
        "airtable.models",
        "analysis.news.case_news",
        "analysis.operator_base.base_candidates",
        "analysis.operator_base.base_unnamed",
        "analysis.operator_base.jurisdiction",
        "analysis.operator_base.known_base",
        "analysis.operator_base.known_nameless",
        "analysis.persecution.classifier",
        "analysis.persecution.queries",
        "analysis.politics.classification",
        "analysis.rosfinmonitoring.inclusion_dates",
        "analysis.rosfinmonitoring.rf_articles",
        "analysis.rosfinmonitoring.rf_check",
        "analysis.rosfinmonitoring.rf_entry",
        "analysis.unnamed.matching",
        "analysis.unnamed.named_candidates",
        "ingestion.sources.source_registry",
        "processing.evidence.publications",
        "processing.evidence.regions",
        "processing.evidence.unnamed",
        "processing.extraction.name_frequency",
        "processing.junk.holds",
        "processing.junk.screen",
        "processing.roles.officials",
        "processing.roles.roles",
        "results.ask",
        "results.candidates.models",
        "results.done_marks",
        "results.models",
        "results.queries.sentence_cases",
        "results.queries.unnamed_cases",
        # a person's decisions, and the actions of the junk-hold station
        "analysis.unnamed.figurants",
        "processing.junk.hold_reader",
        "processing.junk.purge",
        # the frozen ER v2 path's review (ADR 0024): the person endpoints' compatibility
        "persons.resolution.review",
    }
)

# The older single-module commands: maintenance tools of one module each, which build
# that module's parts themselves. Each may reach into the modules named here and no
# other; the ER v2 ones are marked «[legacy: ER v2]» in their help (ADR 0024).
MODULE_TOOLS: dict[str, frozenset[str]] = {
    "cli/ingestion.py": frozenset({"ingestion"}),
    "cli/extraction.py": frozenset({"processing.extraction", "ingestion.sources.source_registry"}),
    "cli/persons.py": frozenset({"persons", "processing.extraction"}),
    "cli/person_resolution.py": frozenset({"persons"}),
    "cli/persecution.py": frozenset({"analysis.persecution", "persons"}),
    "cli/rosfinmonitoring.py": frozenset({"analysis.rosfinmonitoring"}),
    "cli/candidates.py": frozenset({"results.candidates"}),
    "cli_batches.py": frozenset({"processing.extraction", "analysis.rosfinmonitoring"}),
}


def _module_of(path: str) -> str:
    for prefix, module in MODULE_OF:
        if path.startswith(prefix):
            return module
    # Everything else at the top of a `src/` wires the application.
    return ENTRY_POINT


def _path_of(imported: str) -> str | None:
    """The file or package of the workspace an absolute import names; None for a library."""
    parts = imported.split(".")
    for length in range(len(parts), 0, -1):
        base = "/".join(parts[:length])
        found = module_file(".".join(parts[:length]))
        if found is not None:
            return f"{base}/" if found.is_dir() else f"{base}.py"
    return None


def _dotted(target: str) -> str:
    return ".".join(target.removesuffix(".py").strip("/").split("/"))


def _absolute(node: ast.ImportFrom, path: str) -> str:
    """The module a `from … import` names, by its absolute name. A relative import is
    resolved against the file's own package; one that climbs above the top package
    cannot run and is refused, not read as some other module."""
    if not node.level:
        return node.module or ""
    package = path.split("/")[:-1]
    if node.level > len(package):
        raise ValueError(f"{path}: relative import above the top package (level {node.level})")
    base = package[: len(package) - (node.level - 1)]
    return ".".join([*base, *([node.module] if node.module else [])])


def _imports(tree: ast.AST, path: str) -> Iterator[str]:
    """Every module the file at `path` imports, by its absolute name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _absolute(node, path)
            if module:
                yield module
            # `from package import module` names the module only in the alias.
            yield from (f"{module}.{alias.name}" if module else alias.name for alias in node.names)


def _files(*prefixes: str) -> Iterator[tuple[str, ast.AST]]:
    for path, file in source_files():
        if not prefixes or path.startswith(prefixes):
            yield path, ast.parse(file.read_text(encoding="utf-8"))


def _exists(dotted: str) -> bool:
    return module_file(dotted) is not None


def _provider(dotted: str) -> str | None:
    """The module or package of the workspace that a dotted name lives in; None for a library."""
    parts = dotted.split(".")
    for length in range(len(parts), 0, -1):
        if _exists(".".join(parts[:length])):
            return ".".join(parts[:length])
    return None


def _imported_modules(tree: ast.AST, path: str) -> set[str]:
    """What of the workspace a file imports, each as the module or the package it is: a
    package imported whole is named as the package, so it cannot pass for the one
    public module inside it."""
    found: set[str | None] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(_provider(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _absolute(node, path)
            for alias in node.names:
                named = f"{module}.{alias.name}" if module else alias.name
                # A submodule is itself; any other name is its module's.
                found.add(named if _exists(named) else _provider(module))
    return {module for module in found if module is not None}


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


def _is_storage(module: str) -> bool:
    """A package's ORM models: who may import them is `tests/test_data_ownership.py`'s
    rule (only their owner), not this file's."""
    return ".storage" in f".{module}" and module.split(".")[1:2] == ["storage"]


def _domain_imports_outside(allowed: frozenset[str], prefix: str) -> list[str]:
    return sorted(
        f"{path} -> {module}"
        for path, tree in _files(prefix)
        if _module_of(path) != ENTRY_POINT
        for module in _imported_modules(tree, path)
        if module.split(".")[0] in _DOMAIN_PACKAGES
        and module not in allowed
        and not _is_storage(module)
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


def test_an_investigation_reaches_the_domain_only_through_its_public_read_apis() -> None:
    assert _domain_imports_outside(INVESTIGATION_MAY_IMPORT, "investigation/") == []


def test_an_investigation_never_depends_on_er_v2() -> None:
    offenders = sorted(
        f"{path} -> {imported}"
        for path, tree in _files("investigation/")
        for imported in _imports(tree, path)
        if imported.split(".")[0] == "persons"
    )

    assert offenders == []


def test_a_source_search_fetches_nothing_but_its_own_source() -> None:
    # The read-only search is not a way into the pipeline: it stores, extracts and
    # groups nothing, so it imports no storage and no later stage.
    offenders = sorted(
        f"ingestion/search.py -> {imported}"
        for path, tree in _files("ingestion/search.py")
        for imported in _imports(tree, path)
        if imported.split(".")[0] in ("db", "sqlalchemy", "processing", "analysis", "results")
        or imported == "ingestion.sources.sqlalchemy_persistence"
    )

    assert offenders == []


# The investigation's own table (ADR 0031): its model and the store of its runs. Nothing
# else of the investigation runs SQL; it reads the domain through the results API.
INVESTIGATION_STORAGE = ("investigation/storage/", "investigation/run_store.py")


def test_an_investigation_runs_no_sql_of_its_own() -> None:
    offenders = sorted(
        f"{path} -> {imported}"
        for path, tree in _files("investigation/")
        if not path.startswith(INVESTIGATION_STORAGE)
        for imported in _imports(tree, path)
        if imported.split(".")[0] in ("sqlalchemy", "db", "psycopg")
    )

    assert offenders == []


def test_dagster_only_schedules_the_orchestrator() -> None:
    # An asset calls one stage of `MonitoringService`; no domain stage lives in Dagster,
    # and nothing of Dagster's in the orchestrator builds the application.
    offenders = sorted(
        f"{path} -> {module}"
        for path, tree in _files("orchestrator/dagster/")
        for module in _imported_modules(tree, path)
        if module.split(".")[0] != "orchestrator"
    )

    assert offenders == []


def test_the_dagster_code_location_only_wires_the_orchestrator() -> None:
    # The code location builds the application and hands its `MonitoringService` to the
    # orchestrator's assets: it reaches no domain module itself.
    offenders = sorted(
        f"{path} -> {module}"
        for path, tree in _files("dagster_location/")
        for module in _imported_modules(tree, path)
        if module.split(".")[0]
        not in ("orchestrator", "application", "settings", "dagster_location")
    )

    assert offenders == []


def test_the_console_reads_the_domain_only_through_its_public_contracts() -> None:
    offenders = sorted(
        f"{path} -> {module}"
        for path, tree in _files("web/", "api.py")
        for module in _imported_modules(tree, path)
        if module.split(".")[0] in _DOMAIN_PACKAGES
        and module not in CONSOLE_READ_MODELS
        and not _is_storage(module)
    )

    assert offenders == []


def test_every_listed_console_contract_is_still_read() -> None:
    # A contract nobody reads any more is taken off the list, not kept as a licence.
    read = {
        module
        for path, tree in _files("web/", "api.py")
        for module in _imported_modules(tree, path)
    }

    assert sorted(CONSOLE_READ_MODELS - read) == []


def test_a_single_module_command_reaches_only_its_own_module() -> None:
    offenders = sorted(
        f"{path} -> {module}"
        for path, allowed in MODULE_TOOLS.items()
        for _, tree in _files(path)
        for module in _imported_modules(tree, path)
        if module.split(".")[0] in _DOMAIN_PACKAGES
        and not any(module == prefix or module.startswith(f"{prefix}.") for prefix in allowed)
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
        if not any((root / f"{module.replace('.', '/')}.py").is_file() for root in SOURCE_ROOTS)
    )

    assert missing == []


def test_a_relative_import_is_read_as_the_module_it_names() -> None:
    source = (
        "from ..identity.service import IdentityService\n"
        "from . import pending\n"
        "from .models import ExtractionRunStatus\n"
        "from .. import identity\n"
    )

    found = set(_imports(ast.parse(source), "processing/extraction/pipeline.py"))

    assert {
        "processing.identity.service",
        "processing.extraction.pending",
        "processing.extraction.models",
        "processing.identity",
    } <= found
    # In a package's `__init__` one dot is the package itself.
    assert "processing.identity.service" in set(
        _imports(ast.parse("from . import service\n"), "processing/identity/__init__.py")
    )


@pytest.mark.parametrize("dots", ["...", "....", "......"])
def test_a_relative_import_above_the_top_package_is_refused(dots: str) -> None:
    # `src/` is not a package: such an import cannot run, and must not be read as
    # some other module.
    tree = ast.parse(f"from {dots} import analysis\n")

    with pytest.raises(ValueError, match="processing/extraction/pipeline.py"):
        list(_imports(tree, "processing/extraction/pipeline.py"))


@pytest.mark.parametrize(
    ("source", "module"),
    [
        ("import processing.identity\n", "processing.identity"),
        ("from processing import identity\n", "processing.identity"),
        ("from processing.identity import disputes\n", "processing.identity.disputes"),
        ("import processing.identity.grouping as rules\n", "processing.identity.grouping"),
        ("from processing.junk.purge import JunkPurge\n", "processing.junk.purge"),
    ],
)
def test_a_package_or_a_module_named_any_way_is_what_the_whitelist_sees(
    source: str, module: str
) -> None:
    assert module in _imported_modules(ast.parse(source), "orchestrator/cycle.py")
    assert module not in ORCHESTRATOR_MAY_IMPORT


def test_a_name_imported_from_a_public_module_is_that_module_and_nothing_else() -> None:
    source = "from processing.identity.service import IdentityService, CollectResult\n"

    assert _imported_modules(ast.parse(source), "orchestrator/cycle.py") == {
        "processing.identity.service"
    }


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


# -- imports by a computed name ---------------------------------------------------------------

# The calls that load a module by a name given as a value. Every rule above reads the
# `import` statements of a file; a module loaded this way would pass them all unseen.
_DYNAMIC_IMPORTS = frozenset({"import_module", "__import__", "run_module", "run_path"})


def _dynamic_imports(tree: ast.AST) -> list[int]:
    """The lines of a file that load a module by a computed name, or take hold of what
    does: the function named in any way — called, imported under another name, kept in a
    variable — is the same function."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from importlib import import_module as load`: `load(name)` names nothing.
            if any(alias.name in _DYNAMIC_IMPORTS for alias in node.names):
                lines.append(node.lineno)
        elif (isinstance(node, ast.Attribute) and node.attr in _DYNAMIC_IMPORTS) or (
            isinstance(node, ast.Name) and node.id in _DYNAMIC_IMPORTS
        ):
            # `importlib.import_module` itself, called or handed on.
            lines.append(node.lineno)
    return lines


def test_no_module_is_loaded_by_a_computed_name() -> None:
    """The boundary rules see `import` statements only. Rather than guess what a string
    names, no file of the workspace loads a module by one."""
    found = sorted(f"{path}:{line}" for path, tree in _files() for line in _dynamic_imports(tree))

    assert found == []


@pytest.mark.parametrize(
    ("source", "dynamic"),
    [
        ("import importlib\nimportlib.import_module('analysis.unnamed.figurants')", True),
        ("from importlib import import_module\nimport_module(name)", True),
        ("module = __import__('persons')", True),
        ("import runpy\nrunpy.run_module('cli.persons')", True),
        # Under another name, and handed on in a variable.
        ("from importlib import import_module as load\nload(name)", True),
        ("import importlib\nload = importlib.import_module\nload(name)", True),
        ("import importlib as lib\nlib.import_module(name)", True),
        ("load = __import__\nload('persons')", True),
        # An ordinary import inside a function is an `import` statement: the other rules
        # see it. Asking whether a module exists loads nothing.
        ("def late():\n    from analysis import service\n    return service", False),
        ("import importlib.util\nimportlib.util.find_spec('sentence_transformers')", False),
    ],
)
def test_a_module_loaded_by_a_computed_name_is_seen(source: str, dynamic: bool) -> None:
    assert bool(_dynamic_imports(ast.parse(source))) is dynamic
