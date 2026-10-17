"""Each workspace distribution, installed alone with what it declares, imports.

    uv run python scripts/check_package_isolation.py [court-ingestion ...]

For every member (or the ones named), a fresh environment gets that member and its
declared dependencies from `uv.lock` (`uv sync --package <member> --no-default-groups`),
and every module the member ships is imported there with `python -I` from an empty
directory: no PYTHONPATH, no working copy on the path, no other member installed by
accident. An import of something the member does not declare fails here, though it
passes in the development environment, where every member is installed.

A module may fail to import only for a library of one of the member's extras (the
optional `semantic` one): that is reported and skipped. Every member's wheel is built
first, so a module missing from `[tool.hatch.build.targets.wheel]` shows as well. A
member with tests of its own (`<member>/tests`) runs them there too, with the locked
pytest and nothing of the repository's `tests/`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]

# Imports every module of the given top-level packages; prints what failed and why.
_IMPORT_ALL = r"""
import importlib, json, pkgutil, sys
failed = {}
names = []
for top in sys.argv[1:]:
    module = importlib.import_module(top)
    names.append(top)
    if hasattr(module, "__path__"):
        names += [info.name for info in pkgutil.walk_packages(module.__path__, top + ".")]
for name in names:
    # `__main__` runs a command line; `main` is the application's script.
    if name.rsplit(".", 1)[-1] == "__main__" or name == "main":
        continue
    try:
        importlib.import_module(name)
    except ModuleNotFoundError as exc:
        failed[name] = ["missing", (exc.name or "").split(".")[0]]
    except Exception as exc:
        failed[name] = ["error", f"{type(exc).__name__}: {exc}"]
print(json.dumps({"imported": len(names), "failed": failed}))
"""

# Import name of a library an extra brings -> its distribution (as tests/test_package_graph.py).
_EXTRA_LIBRARIES = {
    "sentence_transformers": "sentence-transformers",
    "torch": "torch",
    "numpy": "numpy",
    "sklearn": "scikit-learn",
}


def _members() -> dict[str, Path]:
    root = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    return {
        tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))["project"][
            "name"
        ]: path
        for pattern in root["tool"]["uv"]["workspace"]["members"]
        for path in sorted(REPOSITORY.glob(pattern))
    }


def _project(path: Path) -> dict[str, Any]:
    return tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))


def _modules(path: Path) -> list[str]:
    packages = _project(path)["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    return sorted(Path(item).name.removesuffix(".py") for item in packages)


def _extras(path: Path) -> set[str]:
    optional = _project(path)["project"].get("optional-dependencies", {})
    return {
        requirement.split(">")[0].split("[")[0].split("=")[0].strip().lower()
        for requirements in optional.values()
        for requirement in requirements
    }


def _locked_version(distribution: str) -> str:
    lock = tomllib.loads((REPOSITORY / "uv.lock").read_text(encoding="utf-8"))
    return str(next(item["version"] for item in lock["package"] if item["name"] == distribution))


def _run_own_tests(name: str, path: Path, python: Path, cwd: Path) -> list[str]:
    tests = path / "tests"
    if not tests.is_dir():
        return []
    subprocess.run(
        ["uv", "pip", "install", "--quiet", "--python", str(python)]
        + [f"pytest=={_locked_version('pytest')}"],
        cwd=REPOSITORY,
        check=True,
    )
    # `-c /dev/null`: not the workspace's configuration, whose `pythonpath` is `tests/`.
    result = subprocess.run(
        [str(python), "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", os.devnull]
        + ["--rootdir", str(tests), str(tests)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    summary = (result.stdout.strip().splitlines() or ["(no output)"])[-1]
    print(f"    own tests: {summary}")
    return [] if result.returncode == 0 else [f"{name}: own tests failed: {summary}"]


def _check(name: str, path: Path, scratch: Path) -> list[str]:
    environment = scratch / name
    subprocess.run(
        ["uv", "sync", "--frozen", "--package", name, "--no-default-groups", "--quiet"],
        cwd=REPOSITORY,
        env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment)},
        check=True,
    )
    python = environment / "bin" / "python"
    empty = scratch / f"{name}-cwd"
    empty.mkdir()
    result = subprocess.run(
        [str(python), "-I", "-c", _IMPORT_ALL, *_modules(path)],
        cwd=empty,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return [f"{name}: {result.stderr.strip().splitlines()[-1]}"]
    report = json.loads(result.stdout)
    extras = _extras(path)
    problems = []
    skipped = []
    for module, (kind, detail) in sorted(report["failed"].items()):
        if kind == "missing" and _EXTRA_LIBRARIES.get(detail) in extras:
            skipped.append(f"{module} (needs {_EXTRA_LIBRARIES[detail]}, an extra)")
        else:
            problems.append(f"{name}: {module}: {kind} {detail}")
    print(f"{name}: {report['imported']} modules, {len(skipped)} skipped for an extra")
    for line in skipped:
        print(f"    skipped {line}")
    return problems + _run_own_tests(name, path, python, empty)


def main() -> int:
    members = _members()
    chosen = sys.argv[1:] or sorted(members)
    with tempfile.TemporaryDirectory(prefix="court-isolation-") as directory:
        scratch = Path(directory)
        subprocess.run(
            [
                "uv",
                "build",
                "--all-packages",
                "--wheel",
                "--quiet",
                "--out-dir",
                str(scratch / "dist"),
            ],
            cwd=REPOSITORY,
            check=True,
        )
        problems = [line for name in chosen for line in _check(name, members[name], scratch)]
    for line in problems:
        print(line, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
