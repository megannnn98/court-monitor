"""The workspace's distributions and the graph between them
(docs/architecture/package-map.md).

`tests/test_module_boundaries.py` holds the imports between modules to the layering of
ADR 0025. This file holds the distributions to it as well: what each `pyproject.toml`
declares, and that what the code imports is declared — a module must not import another
only because the application happens to install both.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest
from support.workspace import REPOSITORY

CORE = "court-core"
PLATFORM = "court-platform"
INGESTION = "court-ingestion"
PROCESSING = "court-processing"
ANALYSIS = "court-analysis"
RESULTS = "court-results"
INVESTIGATION = "court-investigation"
ORCHESTRATOR = "court-orchestrator"
APP = "court-monitor"
EVALUATION = "court-evaluation"

PRODUCTION = frozenset(
    {CORE, PLATFORM, INGESTION, PROCESSING, ANALYSIS, RESULTS, INVESTIGATION, ORCHESTRATOR, APP}
)
DOMAIN = frozenset({INGESTION, PROCESSING, ANALYSIS, RESULTS, INVESTIGATION})

# The workspace distributions each one may depend on: an earlier stage never on a later
# one, the shared infrastructure on no module, the investigation and the orchestrator not
# on each other, nothing in production on the evaluation. Only the application wires
# everything, and only the tooling measures everything.
MAY_DEPEND_ON: dict[str, frozenset[str]] = {
    CORE: frozenset(),
    PLATFORM: frozenset({CORE}),
    INGESTION: frozenset({CORE, PLATFORM}),
    PROCESSING: frozenset({CORE, PLATFORM, INGESTION}),
    ANALYSIS: frozenset({CORE, PLATFORM, INGESTION, PROCESSING}),
    RESULTS: frozenset({CORE, PLATFORM, INGESTION, PROCESSING, ANALYSIS}),
    # The results API and the read-only search of sources (ADR 0027), nothing else.
    INVESTIGATION: frozenset({CORE, PLATFORM, INGESTION, RESULTS}),
    ORCHESTRATOR: frozenset({CORE, PLATFORM, INGESTION, PROCESSING, ANALYSIS, RESULTS}),
    APP: PRODUCTION - {APP},
    EVALUATION: PRODUCTION,
}

# The distribution of each third-party module the code imports. A new library is added
# here and to the `pyproject.toml` of the distribution that imports it.
THIRD_PARTY: dict[str, str] = {
    "alembic": "alembic",
    "anthropic": "anthropic",
    "curl_cffi": "curl-cffi",
    "dagster": "dagster",
    "fastapi": "fastapi",
    "httpx": "httpx",
    "langchain_core": "langchain-core",
    "langgraph": "langgraph",
    "markdown_it": "markdown-it-py",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "pyarrow": "pyarrow",
    "pydantic": "pydantic",
    "pymorphy3": "pymorphy3",
    "rapidfuzz": "rapidfuzz",
    "selectolax": "selectolax",
    "sentence_transformers": "sentence-transformers",
    "sklearn": "scikit-learn",
    "sqlalchemy": "sqlalchemy",
    "torch": "torch",
    "weasyprint": "weasyprint",
}


@dataclass(frozen=True)
class Member:
    name: str
    root: Path
    # Required and optional (extras) dependencies, by distribution name.
    dependencies: frozenset[str]
    extras: frozenset[str]
    workspace_sources: frozenset[str]
    # The top-level modules its wheel ships, as `[tool.hatch.build.targets.wheel]` lists.
    modules: frozenset[str]

    @property
    def src(self) -> Path:
        return self.root / "src"


def _name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9._-]+", requirement)
    assert match is not None, requirement
    return match.group(0).lower()


@cache
def members() -> dict[str, Member]:
    root = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    found: dict[str, Member] = {}
    for pattern in root["tool"]["uv"]["workspace"]["members"]:
        for directory in sorted(REPOSITORY.glob(pattern)):
            project = tomllib.loads((directory / "pyproject.toml").read_text(encoding="utf-8"))
            meta = project["project"]
            extras = meta.get("optional-dependencies", {})
            wheel = project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
            found[meta["name"]] = Member(
                name=meta["name"],
                root=directory,
                dependencies=frozenset(_name(item) for item in meta.get("dependencies", [])),
                extras=frozenset(_name(item) for items in extras.values() for item in items),
                workspace_sources=frozenset(
                    name
                    for name, source in project.get("tool", {})
                    .get("uv", {})
                    .get("sources", {})
                    .items()
                    if source.get("workspace") is True
                ),
                modules=frozenset(Path(path).name.removesuffix(".py") for path in wheel),
            )
    return found


def _workspace(names: frozenset[str]) -> frozenset[str]:
    return names & set(members())


def _owner() -> dict[str, str]:
    return {module: member.name for member in members().values() for module in member.modules}


def _top_level_imports(member: Member) -> Iterator[tuple[str, str]]:
    """(file, top-level module) for every absolute import of the member's code."""
    for file in sorted(member.src.rglob("*.py")):
        relative = file.relative_to(REPOSITORY).as_posix()
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield relative, alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                yield relative, node.module.split(".")[0]


def test_every_distribution_of_the_map_is_a_member() -> None:
    assert set(members()) == set(MAY_DEPEND_ON)


def test_every_member_names_its_workspace_dependencies_as_workspace_sources() -> None:
    # Without the source uv would look for `court-*` on the package index.
    mismatched = {
        member.name: sorted(
            _workspace(member.dependencies | member.extras) ^ member.workspace_sources
        )
        for member in members().values()
        if _workspace(member.dependencies | member.extras) != member.workspace_sources
    }

    assert mismatched == {}


@pytest.mark.parametrize("name", sorted(MAY_DEPEND_ON))
def test_a_distribution_depends_only_on_what_the_layering_allows(name: str) -> None:
    member = members()[name]
    declared = _workspace(member.dependencies | member.extras) - {name}

    assert sorted(declared - MAY_DEPEND_ON[name]) == []


def test_the_dependency_graph_has_no_cycle() -> None:
    graph = {member.name: _workspace(member.dependencies) for member in members().values()}
    done: set[str] = set()

    def visit(name: str, path: tuple[str, ...]) -> None:
        assert name not in path, " -> ".join((*path, name))
        if name in done:
            return
        for dependency in sorted(graph[name]):
            visit(dependency, (*path, name))
        done.add(name)

    for name in sorted(graph):
        visit(name, ())


@pytest.mark.parametrize(
    ("dependent", "dependency"),
    [
        *((name, EVALUATION) for name in sorted(PRODUCTION)),
        *((name, APP) for name in sorted(PRODUCTION - {APP})),
        *((name, ORCHESTRATOR) for name in sorted(DOMAIN)),
        (INVESTIGATION, ORCHESTRATOR),
        (ORCHESTRATOR, INVESTIGATION),
        (INGESTION, PROCESSING),
        (INGESTION, ANALYSIS),
        (INGESTION, RESULTS),
        (PROCESSING, ANALYSIS),
        (PROCESSING, RESULTS),
        (ANALYSIS, RESULTS),
        (CORE, PLATFORM),
    ],
)
def test_a_forbidden_dependency_is_not_declared_even_transitively(
    dependent: str, dependency: str
) -> None:
    graph = {
        member.name: _workspace(member.dependencies | member.extras)
        for member in members().values()
    }
    reached: set[str] = set()
    pending = [dependent]
    while pending:
        for name in graph[pending.pop()] - reached:
            reached.add(name)
            pending.append(name)

    assert dependency not in reached


@pytest.mark.parametrize("name", sorted(MAY_DEPEND_ON))
def test_the_wheel_ships_every_module_of_the_member(name: str) -> None:
    member = members()[name]
    on_disk = {
        path.stem if path.is_file() else path.name
        for path in member.src.iterdir()
        if (path.is_file() and path.suffix == ".py") or (path / "__init__.py").is_file()
    }

    assert member.modules == on_disk


def test_no_module_is_shipped_by_two_distributions() -> None:
    shipped = [module for member in members().values() for module in member.modules]

    assert sorted(module for module in set(shipped) if shipped.count(module) > 1) == []


@pytest.mark.parametrize("name", sorted(MAY_DEPEND_ON))
def test_a_member_imports_only_the_workspace_distributions_it_declares(name: str) -> None:
    member = members()[name]
    owner = _owner()
    undeclared = sorted(
        f"{path} -> {module} ({owner[module]})"
        for path, module in _top_level_imports(member)
        if module in owner
        and owner[module] != name
        and owner[module] not in member.dependencies | member.extras
    )

    assert undeclared == []


@pytest.mark.parametrize("name", sorted(MAY_DEPEND_ON))
def test_a_member_imports_only_the_libraries_it_declares(name: str) -> None:
    member = members()[name]
    owner = _owner()
    stdlib = sys.stdlib_module_names | {"__future__"}
    unknown = sorted(
        f"{path} -> {module}"
        for path, module in _top_level_imports(member)
        if module not in owner and module not in stdlib and module not in THIRD_PARTY
    )
    undeclared = sorted(
        f"{path} -> {module} ({THIRD_PARTY[module]})"
        for path, module in _top_level_imports(member)
        if module in THIRD_PARTY and THIRD_PARTY[module] not in member.dependencies | member.extras
    )

    assert unknown == []
    assert undeclared == []


def test_the_semantic_libraries_are_optional_everywhere() -> None:
    # sentence-transformers and torch come only with a `semantic` extra (ADR 0028).
    required = sorted(
        member.name
        for member in members().values()
        if member.dependencies & {"sentence-transformers", "torch", "numpy", "scikit-learn"}
    )

    assert required == []


def test_no_production_code_puts_a_path_on_sys_path() -> None:
    # The modules are found because they are installed, not because a path was added.
    offenders = sorted(
        f"{file.relative_to(REPOSITORY)}:{node.lineno}"
        for name in sorted(PRODUCTION)
        for file in members()[name].src.rglob("*.py")
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )

    assert offenders == []


@pytest.mark.parametrize(
    "path",
    [
        "Dockerfile",
        "compose.yaml",
        "compose.gpu.yaml",
        ".github/workflows/ci.yml",
        "frontend/package.json",
        "docker/dagster/workspace.yaml",
        "pyproject.toml",
    ],
)
def test_nothing_runs_the_code_from_a_path(path: str) -> None:
    text = (REPOSITORY / path).read_text(encoding="utf-8")

    assert "PYTHONPATH" not in text
    assert "working_directory" not in text


def test_every_module_is_imported_from_its_installed_distribution(tmp_path: Path) -> None:
    """`python -I` from an empty directory: no PYTHONPATH, no working copy on the path.
    Each top-level module is found where its own distribution installed it."""
    owner = _owner()
    code = (
        "import importlib, json, sys\n"
        "print(json.dumps({name: importlib.import_module(name).__file__ for name in sys.argv[1:]}))\n"
    )
    # `main` would run the command line; its import is the `cli` it dispatches to.
    modules = sorted(module for module in owner if module != "main")
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, *modules],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    files = json.loads(result.stdout)
    misplaced = sorted(
        f"{module}: {file}"
        for module, file in files.items()
        if not Path(file).resolve().is_relative_to(members()[owner[module]].src.resolve())
    )
    assert misplaced == []
