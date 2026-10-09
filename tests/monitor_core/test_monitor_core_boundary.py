"""`monitor_core` holds generic monitoring mechanics; Court Monitor builds on it, never
the other way round. Any import of an application package from the core fails here,
as does application code reaching past the core's public API or importing the
`sources` shims the core replaced."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
CORE = SRC / "monitor_core"
PUBLIC_CORE_PACKAGES = {
    "monitor_core.errors",
    "monitor_core.ingestion",
    "monitor_core.llm",
    "monitor_core.model",
    "monitor_core.ports",
}
# Re-exports left while the core was extracted, then deleted: their names live in the core.
REMOVED_SHIMS = {
    "sources.ingestion_errors",
    "sources.ingestion_pipeline",
    "sources.persistence",
    "sources.retrying_fetcher",
    "sources.source_ingestion",
}


def _application_packages() -> set[str]:
    """Every top-level module of `src` except the core itself: `sources`, `db`, ..."""
    names = {
        path.stem if path.is_file() else path.name
        for path in SRC.iterdir()
        if (path.is_file() and path.suffix == ".py") or (path / "__init__.py").is_file()
    }
    return names - {"monitor_core"}


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
        for path in [*SRC.rglob("*.py"), *(ROOT / "tests").rglob("*.py")]
        if CORE not in path.parents
    )


def test_application_packages_are_found() -> None:
    # Guards the guard: an empty set would make the boundary test pass vacuously.
    assert {"sources", "db", "extraction", "monitoring", "persons", "settings"} <= (
        _application_packages()
    )


def test_monitor_core_imports_no_application_package() -> None:
    forbidden = _application_packages()
    core_files = sorted(CORE.rglob("*.py"))
    assert core_files, "monitor_core has no modules"

    violations = {
        str(path.relative_to(SRC)): sorted(_imported_top_level_modules(path) & forbidden)
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
    still_there = [
        name for name in REMOVED_SHIMS if (SRC / f"{name.replace('.', '/')}.py").exists()
    ]
    importers = {
        str(path.relative_to(ROOT)): sorted(_imported_modules(path) & REMOVED_SHIMS)
        for path in _application_files()
    }

    assert still_there == []
    assert {path: modules for path, modules in importers.items() if modules} == {}
