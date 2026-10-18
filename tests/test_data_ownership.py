"""Who owns which table (ADR 0031, docs/architecture/data-ownership.md).

One PostgreSQL, one table one owner. The owner's package keeps the ORM class in its
`storage` and is the only one to write the table; any other package reads it through the
owner's public API, which returns DTOs, not ORM records. The migrations compose every
model for Alembic (`migrations/model_registry.py`).

The rules read the code: imports of a `storage` module, and SQL strings that name a
table. They are not a SQL parser; they catch the obvious, in CI.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from support.workspace import REPOSITORY, SOURCE_ROOTS

# The owner of every table, by distribution. A new table is added here, in its owner's
# `storage`, and in `migrations/model_registry.py`.
OWNER_OF_TABLE: dict[str, str] = {
    **dict.fromkeys(("sources", "source_documents", "parsed_articles"), "court-ingestion"),
    **dict.fromkeys(
        (
            "article_extraction_runs",
            "entity_mentions",
            "extracted_events",
            "event_entity_mentions",
            "entity_groups",
            "entity_group_mentions",
            "entity_group_unnamed_mentions",
            "entity_group_charges",
            "entity_group_roles",
            "entity_role_answers",
            "entity_role_decisions",
            "entity_name_overrides",
            "entity_name_normalizations",
            "entity_pair_decisions",
            "entity_removals",
            "entity_official_marks",
            "article_sentences",
            "article_sentence_readings",
            "article_digest_answers",
            "junk_screen_holds",
            # The officials step 4 keeps off the figurants (ADR 0031: not the analysis).
            "excluded_persons",
            "persons",
            "person_aliases",
            "person_merges",
            "person_event_links",
            "person_resolution_decisions",
            "person_resolution_ai_reviews",
            "review_records",
        ),
        "court-processing",
    ),
    **dict.fromkeys(
        (
            "entity_group_politics",
            "entity_politics_answers",
            "entity_politics_decisions",
            "entity_group_news",
            "entity_news_answers",
            "entity_group_rf_matches",
            "rosfinmonitoring_snapshots",
            "rosfinmonitoring_entries",
            "rosfin_matches",
            "rfm_operator_entries",
            "unnamed_figurants",
            "unnamed_screened",
            "unnamed_keeps",
            "unnamed_answers",
            "unnamed_decisions",
            "unnamed_identity_resolutions",
            "persecution_classifications",
            "airtable_known_persons",
            "criminal_articles",
        ),
        "court-analysis",
    ),
    **dict.fromkeys(
        ("monitoring_findings", "entity_done_marks", "chat_questions"), "court-results"
    ),
    **dict.fromkeys(
        (
            "monitoring_runs",
            "monitoring_run_items",
            "source_monitoring_state",
            "operator_operation_runs",
        ),
        "court-orchestrator",
    ),
    "investigation_runs": "court-investigation",
    **dict.fromkeys(("app_settings", "entity_model_spend"), "court-platform"),
}

# Top-level module -> distribution (the wheels' `packages`, tests/test_package_graph.py).
DISTRIBUTION_OF_MODULE: dict[str, str] = {
    "monitor_core": "court-core",
    "db": "court-platform",
    "llm": "court-platform",
    "observability": "court-platform",
    "ingestion": "court-ingestion",
    "processing": "court-processing",
    "persons": "court-processing",
    "analysis": "court-analysis",
    "airtable": "court-analysis",
    "results": "court-results",
    "investigation": "court-investigation",
    "orchestrator": "court-orchestrator",
    "evaluation": "court-evaluation",
}
APPLICATION = "court-monitor"
# The one place that imports every owner's models: the composition of the migrations.
MODEL_REGISTRY = "migrations/model_registry.py"

# -- database probes -----------------------------------------------------------------------
# The evaluation's modules that read the database below every owner's API, because the
# database itself is what they measure (ADR 0031, «Evaluation»). They may read any
# table in SQL; they write none of another package's, and import no `storage`.
EVALUATION = "tools/court-evaluation/src/evaluation/"
DATABASE_PROBES: dict[str, str] = {
    # A read-only, restored snapshot of the working database, of whatever schema version
    # it was dumped at (tables are looked for with `to_regclass`): no owner's API, which
    # is bound to today's models, reads it.
    f"{EVALUATION}identity_comparison/annotation.py": "a restored snapshot",
    f"{EVALUATION}identity_comparison/production.py": "a restored snapshot",
    f"{EVALUATION}identity_comparison/real.py": "a restored snapshot",
    # The logical digest of the whole database and the invariants across its tables,
    # compared between two runs of the pipeline.
    f"{EVALUATION}real_world/state_snapshot.py": "the whole database's digest",
    # The rows a repeated run must not add, and faults injected into the SQL itself.
    f"{EVALUATION}real_world/monitoring_simulation.py": "row counts and SQL faults",
}


def _source_files() -> Iterator[tuple[Path, str, str]]:
    """(file, path for messages, distribution) of every production file and the registry."""
    for root in SOURCE_ROOTS:
        distribution = _distribution_of_root(root)
        for file in sorted(root.rglob("*.py")):
            yield file, file.relative_to(REPOSITORY).as_posix(), distribution
    registry = REPOSITORY / MODEL_REGISTRY
    yield registry, MODEL_REGISTRY, "migrations"


def _distribution_of_root(root: Path) -> str:
    member = root.parent.name
    return member


def _storage_owner(module: str) -> str | None:
    """The distribution whose storage `module` is; None when it is no storage module."""
    parts = module.split(".")
    if parts[0] == "db" and parts[1:2] == ["models"]:
        return "court-platform"
    if len(parts) > 1 and parts[1] == "storage":
        return DISTRIBUTION_OF_MODULE.get(parts[0])
    return None


def _imports(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def _sql_strings(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|TRUNCATE|FROM|JOIN)\b", node.value)
        ):
            yield node.value


_TABLE = re.compile(r"\b(" + "|".join(sorted(OWNER_OF_TABLE, key=len, reverse=True)) + r")\b")
_WRITE = r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(?:[\w, ]*?\b)?{}\b"


def _foreign_storage_imports() -> set[str]:
    found = set()
    for file, path, distribution in _source_files():
        if path == MODEL_REGISTRY:
            continue
        for module in _imports(ast.parse(file.read_text(encoding="utf-8"))):
            owner = _storage_owner(module)
            if owner is not None and owner != distribution:
                if module.startswith("db.models.base"):
                    # The one `Base` every model registers on: shared infrastructure.
                    continue
                found.add(f"{path} -> {module.split('.storage')[0]}.storage")
    return found


def _foreign_sql(*, writes: bool) -> set[str]:
    found = set()
    for file, path, distribution in _source_files():
        for sql in _sql_strings(ast.parse(file.read_text(encoding="utf-8"))):
            for table in set(_TABLE.findall(sql)):
                if OWNER_OF_TABLE[table] == distribution:
                    continue
                written = re.search(_WRITE.format(table), sql, re.IGNORECASE)
                if bool(written) is writes:
                    found.add(f"{path} -> {table}")
    return found


def _registry() -> ModuleType:
    """`migrations/model_registry.py`, loaded from its file: the migrations are not a
    package on the path."""
    spec = importlib.util.spec_from_file_location("model_registry", REPOSITORY / MODEL_REGISTRY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_table_has_exactly_one_owner_and_lives_in_its_storage() -> None:
    registry = _registry()
    owners: dict[str, Any] = registry.OWNERS
    metadata = registry.metadata

    assert sorted(metadata.tables) == sorted(OWNER_OF_TABLE)
    misplaced = sorted(
        f"{table.name}: {OWNER_OF_TABLE[table.name]}, defined in {module.__name__}"
        for module in owners.values()
        for table in metadata.tables.values()
        if any(
            getattr(getattr(module, name), "__tablename__", None) == table.name
            for name in dir(module)
        )
        and DISTRIBUTION_OF_MODULE[module.__name__.split(".")[0]] != OWNER_OF_TABLE[table.name]
    )
    assert misplaced == []


def test_the_platform_holds_no_domain_model() -> None:
    models = REPOSITORY / "packages/court-platform/src/db/models"

    assert sorted(path.name for path in models.glob("*.py")) == [
        "__init__.py",
        "base.py",
        "settings.py",
    ]


def test_no_aggregate_of_every_model_is_left() -> None:
    offenders = sorted(
        path
        for file, path, _ in _source_files()
        if any(
            module.startswith("db.orm_models") for module in _imports(ast.parse(file.read_text()))
        )
    )

    assert not (REPOSITORY / "packages/court-platform/src/db/orm_models.py").exists()
    assert offenders == []


def test_only_the_owner_imports_its_storage() -> None:
    assert sorted(_foreign_storage_imports()) == []


def test_only_the_owner_writes_its_tables() -> None:
    assert sorted(_foreign_sql(writes=True)) == []


def test_only_a_database_probe_reads_another_package_s_tables_in_sql() -> None:
    reads = _foreign_sql(writes=False)
    probing = {read.split(" -> ")[0] for read in reads}

    assert sorted(read for read in reads if read.split(" -> ")[0] not in DATABASE_PROBES) == []
    # A probe that no longer reads below the owners' APIs is taken off the list.
    assert sorted(set(DATABASE_PROBES) - probing) == []


def _is_testing_api(module: str) -> bool:
    """An owner's explicit testing API: `<package>.testing`, or a name imported from it."""
    parts = module.split(".")
    return len(parts) > 1 and parts[1] == "testing" and parts[0] in DISTRIBUTION_OF_MODULE


def test_no_production_code_reads_through_a_testing_api() -> None:
    # `<package>.testing` reads an owner's storage for the evaluation and the tests only:
    # nothing a page or a step runs may stand on it.
    offenders = sorted(
        f"{path} -> {module}"
        for file, path, _ in _source_files()
        if not path.startswith("tools/")
        for module in _imports(ast.parse(file.read_text(encoding="utf-8")))
        if _is_testing_api(module) and not path.endswith(f"/{module.split('.')[0]}/testing.py")
    )

    assert offenders == []


def test_the_registry_is_the_one_place_that_imports_every_owner() -> None:
    registry = ast.parse((REPOSITORY / MODEL_REGISTRY).read_text())
    owners = {
        _storage_owner(module)
        for module in _imports(registry)
        if _storage_owner(module) is not None
    }

    assert owners == set(OWNER_OF_TABLE.values())


@pytest.mark.parametrize(
    ("sql", "table", "written"),
    [
        ("DELETE FROM entity_group_politics WHERE group_id = :id", "entity_group_politics", True),
        ("UPDATE sources SET active = false", "sources", True),
        ("TRUNCATE TABLE a, parsed_articles RESTART IDENTITY", "parsed_articles", True),
        ("SELECT * FROM parsed_articles a JOIN sources s", "parsed_articles", False),
        ("INSERT INTO chat_questions (q) SELECT q FROM parsed_articles", "parsed_articles", False),
    ],
)
def test_a_write_is_told_from_a_read(sql: str, table: str, written: bool) -> None:
    assert bool(re.search(_WRITE.format(table), sql, re.IGNORECASE)) is written
