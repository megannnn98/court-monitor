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

# -- transitional debts --------------------------------------------------------------------
# What is left to move behind an owner's API, as `file -> module` or `file -> table`.
# Each list must equal what the code does: an entry gone from the code is taken off, a
# new crossing fails. All of them are empty when the refactor is done (ADR 0031).
STORAGE_IMPORT_DEBT: frozenset[str] = frozenset(
    {
        "apps/court-monitor/src/web/candidate_rows.py -> analysis.storage",
        "apps/court-monitor/src/web/candidate_rows.py -> ingestion.storage",
        "apps/court-monitor/src/web/candidate_rows.py -> persons.storage",
        "apps/court-monitor/src/web/candidate_rows.py -> processing.storage",
        "apps/court-monitor/src/web/routers/entities.py -> processing.storage",
        "apps/court-monitor/src/web/ui/airtable.py -> analysis.storage",
        "apps/court-monitor/src/web/ui/airtable.py -> ingestion.storage",
        "apps/court-monitor/src/web/ui/airtable.py -> processing.storage",
        "apps/court-monitor/src/web/ui/base_unnamed.py -> analysis.storage",
        "apps/court-monitor/src/web/ui/dossier.py -> analysis.storage",
        "apps/court-monitor/src/web/ui/dossier.py -> processing.storage",
        "apps/court-monitor/src/web/ui/entities.py -> analysis.storage",
        "apps/court-monitor/src/web/ui/entities.py -> processing.storage",
        "apps/court-monitor/src/web/ui/management.py -> orchestrator.storage",
        "apps/court-monitor/src/web/ui/officials_list.py -> processing.storage",
        "apps/court-monitor/src/web/ui/overview.py -> analysis.storage",
        "apps/court-monitor/src/web/ui/people_export.py -> analysis.storage",
        "apps/court-monitor/src/web/ui/people_export.py -> processing.storage",
        "apps/court-monitor/src/web/ui/unnamed.py -> analysis.storage",
        "apps/court-monitor/src/web/ui/unnamed.py -> processing.storage",
        "apps/court-monitor/src/web/ui/work_cycle.py -> orchestrator.storage",
        "packages/court-analysis/src/analysis/pending.py -> persons.storage",
        "packages/court-analysis/src/analysis/persecution/classification_service.py -> ingestion.storage",
        "packages/court-analysis/src/analysis/persecution/classification_service.py -> persons.storage",
        "packages/court-analysis/src/analysis/persecution/classification_service.py -> processing.storage",
        "packages/court-analysis/src/analysis/rosfinmonitoring/rf_check.py -> processing.storage",
        "packages/court-orchestrator/src/orchestrator/repository.py -> results.storage",
        "tools/court-evaluation/src/evaluation/final/runner.py -> analysis.storage",
        "tools/court-evaluation/src/evaluation/final/runner.py -> ingestion.storage",
        "tools/court-evaluation/src/evaluation/final/runner.py -> persons.storage",
        "tools/court-evaluation/src/evaluation/final/runner.py -> processing.storage",
        "tools/court-evaluation/src/evaluation/final/runner.py -> results.storage",
        "tools/court-evaluation/src/evaluation/model_comparison/compare.py -> analysis.storage",
        "tools/court-evaluation/src/evaluation/model_comparison/compare.py -> processing.storage",
        "tools/court-evaluation/src/evaluation/person_resolution/corpus.py -> analysis.storage",
        "tools/court-evaluation/src/evaluation/person_resolution/corpus.py -> ingestion.storage",
        "tools/court-evaluation/src/evaluation/person_resolution/corpus.py -> persons.storage",
        "tools/court-evaluation/src/evaluation/person_resolution/corpus.py -> processing.storage",
        "tools/court-evaluation/src/evaluation/person_resolution/er_run.py -> persons.storage",
        "tools/court-evaluation/src/evaluation/real_world/db_state.py -> analysis.storage",
        "tools/court-evaluation/src/evaluation/real_world/db_state.py -> ingestion.storage",
        "tools/court-evaluation/src/evaluation/real_world/db_state.py -> persons.storage",
        "tools/court-evaluation/src/evaluation/real_world/db_state.py -> processing.storage",
        "tools/court-evaluation/src/evaluation/real_world/db_state.py -> results.storage",
        "tools/court-evaluation/src/evaluation/real_world/monitoring_simulation.py -> orchestrator.storage",
        "tools/court-evaluation/src/evaluation/real_world/monitoring_simulation.py -> results.storage",
    }
)
FOREIGN_WRITE_DEBT: frozenset[str] = frozenset()
# Reads in SQL of a table another package owns. What stays at the end is a read facade
# named in docs/architecture/data-ownership.md («Read facades»), with its reason.
FOREIGN_SQL_READS: frozenset[str] = frozenset(
    {
        "apps/court-monitor/src/web/ui/entities.py -> entity_groups",
        "apps/court-monitor/src/web/ui/funnel.py -> article_extraction_runs",
        "apps/court-monitor/src/web/ui/funnel.py -> entity_group_politics",
        "apps/court-monitor/src/web/ui/funnel.py -> entity_group_rf_matches",
        "apps/court-monitor/src/web/ui/funnel.py -> entity_group_roles",
        "apps/court-monitor/src/web/ui/funnel.py -> entity_groups",
        "apps/court-monitor/src/web/ui/funnel.py -> extracted_events",
        "apps/court-monitor/src/web/ui/funnel.py -> junk_screen_holds",
        "apps/court-monitor/src/web/ui/funnel.py -> parsed_articles",
        "apps/court-monitor/src/web/ui/funnel.py -> source_documents",
        "apps/court-monitor/src/web/ui/junk_holds.py -> article_extraction_runs",
        "apps/court-monitor/src/web/ui/junk_holds.py -> entity_mentions",
        "apps/court-monitor/src/web/ui/junk_holds.py -> extracted_events",
        "apps/court-monitor/src/web/ui/junk_holds.py -> junk_screen_holds",
        "apps/court-monitor/src/web/ui/junk_holds.py -> parsed_articles",
        "apps/court-monitor/src/web/ui/junk_holds.py -> source_documents",
        "apps/court-monitor/src/web/ui/junk_holds.py -> sources",
        "apps/court-monitor/src/web/ui/management.py -> monitoring_run_items",
        "apps/court-monitor/src/web/ui/management.py -> monitoring_runs",
        "apps/court-monitor/src/web/ui/overview.py -> entity_group_news",
        "apps/court-monitor/src/web/ui/overview.py -> entity_group_politics",
        "apps/court-monitor/src/web/ui/overview.py -> entity_groups",
        "apps/court-monitor/src/web/ui/overview.py -> unnamed_figurants",
        "apps/court-monitor/src/web/ui/overview.py -> unnamed_identity_resolutions",
        "apps/court-monitor/src/web/ui/unnamed.py -> unnamed_identity_resolutions",
        "packages/court-analysis/src/analysis/politics/classification.py -> entity_group_charges",
        "packages/court-analysis/src/analysis/politics/classification.py -> entity_group_roles",
        "packages/court-analysis/src/analysis/politics/classification.py -> entity_groups",
        "packages/court-analysis/src/analysis/unnamed/figurants.py -> article_extraction_runs",
        "packages/court-analysis/src/analysis/unnamed/figurants.py -> extracted_events",
        "packages/court-analysis/src/analysis/unnamed/figurants.py -> parsed_articles",
        "packages/court-analysis/src/analysis/unnamed/named_candidates.py -> article_extraction_runs",
        "packages/court-analysis/src/analysis/unnamed/named_candidates.py -> entity_group_charges",
        "packages/court-analysis/src/analysis/unnamed/named_candidates.py -> entity_group_mentions",
        "packages/court-analysis/src/analysis/unnamed/named_candidates.py -> entity_group_roles",
        "packages/court-analysis/src/analysis/unnamed/named_candidates.py -> entity_groups",
        "packages/court-analysis/src/analysis/unnamed/named_candidates.py -> entity_mentions",
        "packages/court-analysis/src/analysis/unnamed/named_candidates.py -> event_entity_mentions",
        "packages/court-analysis/src/analysis/unnamed/named_candidates.py -> extracted_events",
        "packages/court-analysis/src/analysis/unnamed/named_candidates.py -> parsed_articles",
        "packages/court-analysis/src/analysis/unnamed/named_candidates.py -> source_documents",
        "packages/court-analysis/src/analysis/unnamed/named_candidates.py -> sources",
        "packages/court-processing/src/processing/junk/purge.py -> parsed_articles",
        "tools/court-evaluation/src/evaluation/identity_comparison/annotation.py -> article_extraction_runs",
        "tools/court-evaluation/src/evaluation/identity_comparison/annotation.py -> entity_mentions",
        "tools/court-evaluation/src/evaluation/identity_comparison/annotation.py -> event_entity_mentions",
        "tools/court-evaluation/src/evaluation/identity_comparison/annotation.py -> extracted_events",
        "tools/court-evaluation/src/evaluation/identity_comparison/annotation.py -> parsed_articles",
        "tools/court-evaluation/src/evaluation/identity_comparison/annotation.py -> person_resolution_decisions",
        "tools/court-evaluation/src/evaluation/identity_comparison/annotation.py -> source_documents",
        "tools/court-evaluation/src/evaluation/identity_comparison/annotation.py -> sources",
        "tools/court-evaluation/src/evaluation/identity_comparison/production.py -> article_extraction_runs",
        "tools/court-evaluation/src/evaluation/identity_comparison/production.py -> entity_mentions",
        "tools/court-evaluation/src/evaluation/identity_comparison/production.py -> entity_name_normalizations",
        "tools/court-evaluation/src/evaluation/identity_comparison/production.py -> entity_name_overrides",
        "tools/court-evaluation/src/evaluation/identity_comparison/production.py -> entity_pair_decisions",
        "tools/court-evaluation/src/evaluation/identity_comparison/production.py -> entity_removals",
        "tools/court-evaluation/src/evaluation/identity_comparison/production.py -> parsed_articles",
        "tools/court-evaluation/src/evaluation/identity_comparison/production.py -> source_documents",
        "tools/court-evaluation/src/evaluation/identity_comparison/production.py -> sources",
        "tools/court-evaluation/src/evaluation/identity_comparison/real.py -> article_extraction_runs",
        "tools/court-evaluation/src/evaluation/identity_comparison/real.py -> entity_mentions",
        "tools/court-evaluation/src/evaluation/identity_comparison/real.py -> extracted_events",
        "tools/court-evaluation/src/evaluation/identity_comparison/real.py -> parsed_articles",
        "tools/court-evaluation/src/evaluation/identity_comparison/real.py -> person_resolution_decisions",
        "tools/court-evaluation/src/evaluation/identity_comparison/real.py -> persons",
        "tools/court-evaluation/src/evaluation/investigation/golden.py -> entity_group_charges",
        "tools/court-evaluation/src/evaluation/investigation/golden.py -> entity_group_news",
        "tools/court-evaluation/src/evaluation/investigation/golden.py -> entity_group_politics",
        "tools/court-evaluation/src/evaluation/investigation/golden.py -> entity_group_rf_matches",
        "tools/court-evaluation/src/evaluation/investigation/golden.py -> entity_group_roles",
        "tools/court-evaluation/src/evaluation/investigation/golden.py -> entity_groups",
        "tools/court-evaluation/src/evaluation/investigation/golden.py -> unnamed_figurants",
        "tools/court-evaluation/src/evaluation/investigation/golden.py -> unnamed_identity_resolutions",
        "tools/court-evaluation/src/evaluation/junk_screen/corpus.py -> parsed_articles",
        "tools/court-evaluation/src/evaluation/junk_screen/corpus.py -> source_documents",
        "tools/court-evaluation/src/evaluation/junk_screen/corpus.py -> sources",
        "tools/court-evaluation/src/evaluation/model_comparison/compare.py -> entity_group_charges",
        "tools/court-evaluation/src/evaluation/real_world/monitoring_simulation.py -> article_extraction_runs",
        "tools/court-evaluation/src/evaluation/real_world/monitoring_simulation.py -> entity_mentions",
        "tools/court-evaluation/src/evaluation/real_world/monitoring_simulation.py -> monitoring_findings",
        "tools/court-evaluation/src/evaluation/real_world/monitoring_simulation.py -> monitoring_run_items",
        "tools/court-evaluation/src/evaluation/real_world/monitoring_simulation.py -> persecution_classifications",
        "tools/court-evaluation/src/evaluation/real_world/monitoring_simulation.py -> person_resolution_decisions",
        "tools/court-evaluation/src/evaluation/real_world/monitoring_simulation.py -> persons",
        "tools/court-evaluation/src/evaluation/real_world/monitoring_simulation.py -> rosfin_matches",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> article_extraction_runs",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> entity_mentions",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> extracted_events",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> monitoring_findings",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> parsed_articles",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> persecution_classifications",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> person_aliases",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> person_event_links",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> person_resolution_decisions",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> persons",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> rosfin_matches",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> rosfinmonitoring_entries",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> rosfinmonitoring_snapshots",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> source_documents",
        "tools/court-evaluation/src/evaluation/real_world/state_snapshot.py -> sources",
    }
)


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
    assert sorted(_foreign_storage_imports()) == sorted(STORAGE_IMPORT_DEBT)


def test_only_the_owner_writes_its_tables() -> None:
    assert sorted(_foreign_sql(writes=True)) == sorted(FOREIGN_WRITE_DEBT)


def test_reads_of_another_package_s_tables_in_sql_are_the_listed_ones() -> None:
    assert sorted(_foreign_sql(writes=False)) == sorted(FOREIGN_SQL_READS)


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
