"""`monitor_core` holds generic monitoring mechanics; Court Monitor builds on it, never
the other way round. Any import of an application package from the core fails here,
as does application code reaching past the core's public API or importing the
`ingestion.sources` shims the core replaced."""

import ast
from pathlib import Path

from support.workspace import REPOSITORY, module_file, source_files, top_level_modules

ROOT = REPOSITORY
_CORE = module_file("monitor_core")
assert _CORE is not None
CORE = _CORE
PUBLIC_CORE_PACKAGES = {
    "monitor_core.errors",
    "monitor_core.ingestion",
    "monitor_core.llm",
    "monitor_core.model",
    "monitor_core.ports",
    "monitor_core.retry",
}
# Re-exports left while the core was extracted, then deleted: their names live in the core.
REMOVED_SHIMS = {
    "ingestion.sources.ingestion_errors",
    "ingestion.sources.ingestion_pipeline",
    "ingestion.sources.persistence",
    "ingestion.sources.retrying_fetcher",
    "ingestion.sources.source_ingestion",
}


def _application_packages() -> set[str]:
    """Every top-level module of the workspace except the core itself: `ingestion`, `db`, ..."""
    return top_level_modules() - {"monitor_core"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def _imported_top_level_modules(path: Path) -> set[str]:
    return {module.split(".")[0] for module in _imported_modules(path)}


def _application_files() -> list[Path]:
    """Python outside the core: the application and the tests."""
    return sorted(
        path
        for path in [*(file for _, file in source_files()), *(ROOT / "tests").rglob("*.py")]
        if CORE not in path.parents
    )


def test_application_packages_are_found() -> None:
    # Guards the guard: an empty set would make the boundary test pass vacuously.
    assert {"ingestion", "db", "processing", "orchestrator", "persons", "settings"} <= (
        _application_packages()
    )


def test_monitor_core_imports_no_application_package() -> None:
    forbidden = _application_packages()
    core_files = sorted(CORE.rglob("*.py"))
    assert core_files, "monitor_core has no modules"

    violations = {
        str(path.relative_to(CORE.parent)): sorted(_imported_top_level_modules(path) & forbidden)
        for path in core_files
    }

    assert {path: names for path, names in violations.items() if names} == {}


def test_application_imports_the_core_only_through_its_public_packages() -> None:
    violations = {
        str(path.relative_to(ROOT)): sorted(
            module
            for module in _imported_modules(path)
            if module.split(".")[0] == "monitor_core" and module not in PUBLIC_CORE_PACKAGES
        )
        for path in _application_files()
    }

    assert {path: modules for path, modules in violations.items() if modules} == {}


def test_removed_ingestion_shims_stay_removed() -> None:
    still_there = [name for name in REMOVED_SHIMS if module_file(name) is not None]
    importers = {
        str(path.relative_to(ROOT)): sorted(_imported_modules(path) & REMOVED_SHIMS)
        for path in _application_files()
    }

    assert still_there == []
    assert {path: modules for path, modules in importers.items() if modules} == {}
