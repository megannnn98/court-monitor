"""Where the Python of the workspace is: one `src/` per distribution (`packages/*`,
`apps/*`, `tools/*`). A file is named relative to its own root, so `ingestion/service.py`
is the same file wherever the distribution that holds it lives."""

from __future__ import annotations

from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]

SOURCE_ROOTS: tuple[Path, ...] = tuple(
    sorted(
        root
        for pattern in ("src", "packages/*/src", "apps/*/src", "tools/*/src")
        for root in REPOSITORY.glob(pattern)
        if root.is_dir()
    )
)


def source_files() -> list[tuple[str, Path]]:
    """Every Python file of the workspace's code, as (path under its root, file)."""
    return sorted(
        (file.relative_to(root).as_posix(), file)
        for root in SOURCE_ROOTS
        for file in root.rglob("*.py")
    )


def top_level_modules() -> set[str]:
    """Every top-level module or package of the workspace's code: `ingestion`, `db`, ..."""
    return {
        path.stem if path.is_file() else path.name
        for root in SOURCE_ROOTS
        for path in root.iterdir()
        if (path.is_file() and path.suffix == ".py") or (path / "__init__.py").is_file()
    }


def module_file(dotted: str) -> Path | None:
    """The file or package directory a dotted name is, in whichever root holds it."""
    relative = dotted.replace(".", "/")
    for root in SOURCE_ROOTS:
        if (root / f"{relative}.py").is_file():
            return root / f"{relative}.py"
        if (root / relative).is_dir():
            return root / relative
    return None


# The application's own `src/`: `main.py`, `static/`, the console.
APP_SRC = REPOSITORY / "apps" / "court-monitor" / "src"
