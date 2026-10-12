"""The import rules between the modules of `docs/architecture/modularization-plan.md`.

Every file under `src/` belongs to one module of the plan (`MODULE_OF`). An import from
one module into another that the plan forbids fails here, unless it is one of the
imports that existed when the rules were written (`KNOWN_VIOLATIONS`). That list only
shrinks: an entry whose import is gone must be deleted, and a new one is never added
without changing the plan.
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
# The operator cycle on entity groups (`entities/`): mapped, not yet assigned to a module.
OPERATOR_CYCLE = "operator_cycle"
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
    ("persons/", PROCESSING),
    ("llm/cli_reviewer.py", PROCESSING),
    ("llm/entity_reviewer.py", PROCESSING),
    ("persecution/", ANALYSIS),
    ("rosfinmonitoring/", ANALYSIS),
    ("candidates/", RESULTS),
    ("monitoring/findings.py", RESULTS),
    ("monitoring/cli.py", ENTRY_POINT),
    ("monitoring/dagster/definitions.py", ENTRY_POINT),
    ("monitoring/dagster/resources.py", ENTRY_POINT),
    ("monitoring/", ORCHESTRATOR),
    ("entities/", OPERATOR_CYCLE),
    ("db/", SHARED),
    ("monitor_core/", SHARED),
    ("llm/", SHARED),
    ("airtable/", SHARED),
    ("observability.py", SHARED),
    ("health.py", SHARED),
    ("evaluation/", TOOLING),
)

_OUTSIDE_THE_DOMAIN = {TOOLING, ENTRY_POINT}
# Importing module -> the modules it must not import. Entry points and tooling wire and
# measure everything, so they have no row.
FORBIDDEN: dict[str, set[str]] = {
    INGESTION: {PROCESSING, ANALYSIS, RESULTS, ORCHESTRATOR, OPERATOR_CYCLE} | _OUTSIDE_THE_DOMAIN,
    PROCESSING: {ANALYSIS, RESULTS, ORCHESTRATOR, OPERATOR_CYCLE} | _OUTSIDE_THE_DOMAIN,
    ANALYSIS: {RESULTS, ORCHESTRATOR, OPERATOR_CYCLE} | _OUTSIDE_THE_DOMAIN,
    RESULTS: {ORCHESTRATOR, OPERATOR_CYCLE} | _OUTSIDE_THE_DOMAIN,
    ORCHESTRATOR: {OPERATOR_CYCLE} | _OUTSIDE_THE_DOMAIN,
    OPERATOR_CYCLE: {ORCHESTRATOR} | _OUTSIDE_THE_DOMAIN,
    SHARED: {INGESTION, PROCESSING, ANALYSIS, RESULTS, ORCHESTRATOR, OPERATOR_CYCLE}
    | _OUTSIDE_THE_DOMAIN,
}

# "importing file -> imported module", as they stood on 2026-10-11. See "Dangerous
# dependencies" in the plan for what each one waits for.
KNOWN_VIOLATIONS: frozenset[str] = frozenset(
    {
        "airtable/repository.py -> processing.extraction.normalizers",
        "health.py -> monitoring.models",
        "monitoring/findings.py -> monitoring.models",
    }
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


def _imports(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module
            # `from package import module` names the module only in the alias.
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def _violations() -> set[str]:
    found: set[str] = set()
    for file in sorted(SRC.rglob("*.py")):
        path = file.relative_to(SRC).as_posix()
        forbidden = FORBIDDEN.get(_module_of(path), set())
        for imported in _imports(ast.parse(file.read_text(encoding="utf-8"))):
            target = _path_of(imported)
            if target is not None and _module_of(target) in forbidden:
                # The package of a `from package import name` is reported once, as the name.
                found.add(f"{path} -> {'.'.join(target.removesuffix('.py').strip('/').split('/'))}")
    return found


def test_no_new_import_crosses_a_module_boundary() -> None:
    assert sorted(_violations() - KNOWN_VIOLATIONS) == []


def test_every_known_violation_still_exists() -> None:
    assert sorted(KNOWN_VIOLATIONS - _violations()) == []


def test_extraction_knows_neither_persons_nor_entity_groups() -> None:
    # Extraction is what both pipelines share: it must not depend on what either makes of it.
    downstream = ("persons", "entities")
    offenders = sorted(
        f"{file.relative_to(SRC).as_posix()} -> {imported}"
        for file in (SRC / "processing" / "extraction").rglob("*.py")
        for imported in _imports(ast.parse(file.read_text(encoding="utf-8")))
        if imported.split(".")[0] in downstream
    )

    assert offenders == []
