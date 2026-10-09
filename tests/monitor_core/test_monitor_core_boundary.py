"""`monitor_core` holds generic monitoring mechanics; Court Monitor builds on it, never
the other way round. Any import of an application package from the core fails here."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
CORE = SRC / "monitor_core"


def _application_packages() -> set[str]:
    """Every top-level module of `src` except the core itself: `sources`, `db`, ..."""
    names = {
        path.stem if path.is_file() else path.name
        for path in SRC.iterdir()
        if (path.is_file() and path.suffix == ".py") or (path / "__init__.py").is_file()
    }
    return names - {"monitor_core"}


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


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
