"""Guarded helpers for disposable databases (tests, retrieval evaluation)."""

from __future__ import annotations

from sqlalchemy import Engine, text

# Every table research, extraction and semantic retrieval write to.
DISPOSABLE_TABLES = (
    "entity_name_normalizations",
    "entity_role_answers",
    "entity_group_roles",
    "entity_group_rf_matches",
    "entity_group_charges",
    "entity_group_mentions",
    "entity_groups",
    "operator_operation_runs",
    "monitoring_findings",
    "monitoring_run_items",
    "source_monitoring_state",
    "monitoring_runs",
    "semantic_index_state",
    "semantic_vectors",
    "semantic_vector_collections",
    "semantic_documents",
    "rosfin_matches",
    "rosfinmonitoring_entries",
    "rosfinmonitoring_snapshots",
    "persecution_classifications",
    "person_resolution_decisions",
    "person_event_links",
    "review_records",
    "person_merges",
    "person_aliases",
    "persons",
    "event_entity_mentions",
    "extracted_events",
    "entity_mentions",
    "article_extraction_runs",
    "parsed_articles",
    "source_documents",
    "sources",
)

DISPOSABLE_DATABASE_SUFFIXES = ("_test", "_eval")


class NotDisposableDatabaseError(RuntimeError):
    pass


def require_disposable_database(engine: Engine) -> None:
    name = engine.url.database or ""
    if not name.endswith(DISPOSABLE_DATABASE_SUFFIXES):
        raise NotDisposableDatabaseError(
            f"Refusing to wipe database {name!r}: only databases ending with "
            f"{' or '.join(DISPOSABLE_DATABASE_SUFFIXES)} may be truncated"
        )


def truncate_disposable_tables(engine: Engine) -> None:
    require_disposable_database(engine)
    with engine.begin() as connection:
        connection.execute(
            text(f"TRUNCATE TABLE {', '.join(DISPOSABLE_TABLES)} RESTART IDENTITY CASCADE")
        )
