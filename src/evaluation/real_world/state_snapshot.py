"""Deterministic logical snapshot of the domain state, and database invariants.

Two runs are compared by what they mean, not by surrogate ids or timestamps:
a person is its canonical name plus the article spans of its mentions, an
event is its article span, and so on. Monitoring audit rows (runs, items,
checkpoints) are not part of the logical state.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping

from pydantic import BaseModel
from sqlalchemy import Engine, text

from evaluation.real_world.db_state import source_name_for

LOGICAL_TABLES = (
    "source_documents",
    "persons",
    "aliases",
    "mentions",
    "resolution_decisions",
    "events",
    "classifications",
    "rf_results",
    "findings",
    "semantic_documents",
)


class StateDiff(BaseModel):
    table: str
    only_in_first: int
    only_in_second: int
    examples: list[str]


def _rows(engine: Engine, sql: str) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(sql)).all()]


def logical_snapshot(engine: Engine) -> dict[str, list[str]]:
    articles = {
        article_id: f"{source_name_for(str(base_url))}:{external_id}"
        for article_id, base_url, external_id in _rows(
            engine,
            "SELECT pa.id, s.base_url, sd.external_id FROM parsed_articles pa "
            "JOIN source_documents sd ON sd.id = pa.document_id JOIN sources s ON s.id = sd.source_id",
        )
    }
    mention_rows = _rows(
        engine,
        "SELECT m.id, r.article_id, m.entity_type, m.start_offset, m.end_offset, m.surface_text, "
        "m.normalized_text, m.person_id FROM entity_mentions m "
        "JOIN article_extraction_runs r ON r.id = m.extraction_run_id",
    )
    mention_key = {
        row[0]: f"{articles.get(row[1], row[1])}@{row[3]}-{row[4]}:{row[2]}" for row in mention_rows
    }
    person_mentions: dict[object, list[str]] = defaultdict(list)
    for row in mention_rows:
        if row[7] is not None:
            person_mentions[row[7]].append(mention_key[row[0]])
    person_names = {
        row[0]: row[1] for row in _rows(engine, "SELECT id, canonical_name FROM persons")
    }

    def person_key(person_id: object) -> str:
        if person_id is None:
            return "-"
        refs = sorted(person_mentions.get(person_id, []))
        anchor = refs[0] if refs else "no-mentions"
        return f"{person_names.get(person_id, '?')}[{anchor}]"

    snapshot: dict[str, list[str]] = {}
    snapshot["source_documents"] = [
        f"{source_name_for(str(base_url))}:{external_id}"
        for base_url, external_id in _rows(
            engine,
            "SELECT s.base_url, sd.external_id FROM source_documents sd "
            "JOIN sources s ON s.id = sd.source_id",
        )
    ]
    snapshot["persons"] = [
        f"{person_key(pid)}|{status}|merged_into={person_key(merged)}|mentions={len(person_mentions.get(pid, []))}"
        for pid, status, merged in _rows(engine, "SELECT id, status, merged_into_id FROM persons")
    ]
    snapshot["aliases"] = [
        f"{person_key(pid)}|{surface}|{origin}"
        for pid, surface, origin in _rows(
            engine, "SELECT person_id, surface_text, origin FROM person_aliases"
        )
    ]
    snapshot["mentions"] = [
        f"{mention_key[row[0]]}|{row[5]}|{row[6]}|{person_key(row[7])}" for row in mention_rows
    ]
    snapshot["resolution_decisions"] = [
        f"{mention_key.get(mid, mid)}|{action}|{status}|{person_key(selected)}"
        for mid, action, status, selected in _rows(
            engine,
            "SELECT mention_id, action, status, selected_person_id FROM person_resolution_decisions",
        )
    ]
    links: dict[object, list[str]] = defaultdict(list)
    for event_id, person_id, role in _rows(
        engine, "SELECT event_id, person_id, role FROM person_event_links"
    ):
        links[event_id].append(f"{person_key(person_id)}:{role}")
    snapshot["events"] = [
        f"{articles.get(article_id, article_id)}@{start}-{end}:{event_type}|{sorted(links.get(eid, []))}"
        for eid, article_id, event_type, start, end in _rows(
            engine,
            "SELECT e.id, r.article_id, e.event_type, e.start_offset, e.end_offset "
            "FROM extracted_events e JOIN article_extraction_runs r ON r.id = e.extraction_run_id",
        )
    ]
    snapshot["classifications"] = [
        f"{person_key(pid)}|{status}|{round(float(str(confidence)), 4)}|{classifier}@{version}|{reasons}"
        for pid, status, confidence, classifier, version, reasons in _rows(
            engine,
            "SELECT person_id, status, confidence, classifier_name, classifier_version, reasons "
            "FROM persecution_classifications",
        )
    ]
    snapshot["rf_results"] = [
        f"{person_key(pid)}|{source_url}|{status}|{entry}|{version}"
        for pid, source_url, status, entry, version in _rows(
            engine,
            "SELECT m.person_id, s.source_url, m.status, m.matched_entry_name, m.matcher_version "
            "FROM rosfin_matches m JOIN rosfinmonitoring_snapshots s ON s.id = m.snapshot_id",
        )
    ]
    snapshot["findings"] = [
        f"{person_key(pid)}|{finding_type}|{criteria}|{status}|active={active}"
        for pid, finding_type, criteria, status, active in _rows(
            engine,
            "SELECT person_id, finding_type, criteria_version, status, active FROM monitoring_findings",
        )
    ]
    event_articles = {
        eid: f"{articles.get(article_id, article_id)}@{start}-{end}"
        for eid, article_id, start, end in _rows(
            engine,
            "SELECT e.id, r.article_id, e.start_offset, e.end_offset FROM extracted_events e "
            "JOIN article_extraction_runs r ON r.id = e.extraction_run_id",
        )
    }
    snapshot["semantic_documents"] = [
        f"{entity_type}|{person_key(entity_id) if entity_type == 'person' else event_articles.get(entity_id, entity_id)}"
        f"|{version}|{content_hash}|indexed={indexed is not None}"
        for entity_type, entity_id, version, content_hash, indexed in _rows(
            engine,
            "SELECT entity_type, entity_id, representation_version, content_hash, indexed_at "
            "FROM semantic_documents",
        )
    ]
    return {table: sorted(rows) for table, rows in snapshot.items()}


def compare_snapshots(
    first: Mapping[str, list[str]],
    second: Mapping[str, list[str]],
    *,
    ignore: frozenset[str] = frozenset(),
) -> list[StateDiff]:
    diffs = []
    for table in LOGICAL_TABLES:
        if table in ignore:
            continue
        a, b = Counter(first.get(table, [])), Counter(second.get(table, []))
        only_first, only_second = a - b, b - a
        if only_first or only_second:
            examples = [f"- {row}" for row in sorted(only_first.elements())[:3]] + [
                f"+ {row}" for row in sorted(only_second.elements())[:3]
            ]
            diffs.append(
                StateDiff(
                    table=table,
                    only_in_first=sum(only_first.values()),
                    only_in_second=sum(only_second.values()),
                    examples=examples,
                )
            )
    return diffs


def duplicate_counts(snapshot: Mapping[str, list[str]]) -> dict[str, int]:
    """Rows that appear more than once under their logical key."""
    return {
        table: sum(count - 1 for count in Counter(snapshot.get(table, [])).values() if count > 1)
        for table in LOGICAL_TABLES
    }


def table_counts(engine: Engine) -> dict[str, int]:
    tables = {
        "source_documents": "source_documents",
        "extraction_runs": "article_extraction_runs",
        "mentions": "entity_mentions",
        "persons": "persons",
        "aliases": "person_aliases",
        "events": "extracted_events",
        "person_event_links": "person_event_links",
        "resolution_decisions": "person_resolution_decisions",
        "classifications": "persecution_classifications",
        "rf_results": "rosfin_matches",
        "findings": "monitoring_findings",
        "semantic_documents": "semantic_documents",
    }
    counts: dict[str, int] = {}
    with engine.connect() as connection:
        for name, table in tables.items():
            counts[name] = int(
                connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            )
        counts["pending_person_reviews"] = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM person_resolution_decisions WHERE status = 'pending_review'"
                )
            ).scalar_one()
        )
        counts["semantic_indexed"] = int(
            connection.execute(
                text("SELECT count(*) FROM semantic_documents WHERE indexed_at IS NOT NULL")
            ).scalar_one()
        )
        counts["active_findings"] = int(
            connection.execute(
                text("SELECT count(*) FROM monitoring_findings WHERE active")
            ).scalar_one()
        )
    return counts


INVARIANTS: dict[str, str] = {
    # A person mention with no person and no resolution decision was never resolved.
    "orphan_person_mentions": """
        SELECT count(*) FROM entity_mentions m
        JOIN article_extraction_runs r ON r.id = m.extraction_run_id AND r.status = 'succeeded'
        WHERE m.entity_type = 'person' AND m.person_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM person_resolution_decisions d WHERE d.mention_id = m.id)
    """,
    "broken_mention_offsets": """
        SELECT count(*) FROM entity_mentions m
        JOIN article_extraction_runs r ON r.id = m.extraction_run_id
        JOIN parsed_articles a ON a.id = r.article_id
        WHERE m.end_offset > char_length(a.text) OR m.start_offset >= m.end_offset
           OR substr(a.text, m.start_offset + 1, m.end_offset - m.start_offset) <> m.surface_text
    """,
    "broken_event_offsets": """
        SELECT count(*) FROM extracted_events e
        JOIN article_extraction_runs r ON r.id = e.extraction_run_id
        JOIN parsed_articles a ON a.id = r.article_id
        WHERE e.end_offset > char_length(a.text) OR e.start_offset >= e.end_offset
    """,
    "event_links_to_inactive_persons": """
        SELECT count(*) FROM person_event_links l JOIN persons p ON p.id = l.person_id
        WHERE p.status <> 'active'
    """,
    "mentions_linked_to_inactive_persons": """
        SELECT count(*) FROM entity_mentions m JOIN persons p ON p.id = m.person_id
        WHERE p.status <> 'active'
    """,
    "invalid_merge_references": """
        SELECT count(*) FROM persons p LEFT JOIN persons t ON t.id = p.merged_into_id
        WHERE (p.status = 'merged' AND (t.id IS NULL OR t.status <> 'active' OR t.id = p.id))
           OR (p.status <> 'merged' AND p.merged_into_id IS NOT NULL)
    """,
    "duplicate_active_findings": """
        SELECT coalesce(sum(n - 1), 0) FROM (
            SELECT count(*) AS n FROM monitoring_findings WHERE active
            GROUP BY person_id, finding_type, criteria_version HAVING count(*) > 1) d
    """,
    "duplicate_extraction_identities": """
        SELECT coalesce(sum(n - 1), 0) FROM (
            SELECT count(*) AS n FROM article_extraction_runs WHERE status = 'succeeded'
            GROUP BY article_id, article_content_hash, extractor_name, extractor_version,
                     normalizer_version HAVING count(*) > 1) d
    """,
    "invalid_rf_snapshot_references": """
        SELECT count(*) FROM rosfin_matches m
        LEFT JOIN rosfinmonitoring_entries e ON e.id = m.matched_entry_id
        WHERE m.matched_entry_id IS NOT NULL AND (e.id IS NULL OR e.snapshot_id <> m.snapshot_id)
    """,
    "findings_with_mismatched_snapshot": """
        SELECT count(*) FROM monitoring_findings f
        JOIN rosfin_matches m ON m.id = f.rosfin_match_id
        WHERE f.snapshot_id IS DISTINCT FROM m.snapshot_id
    """,
    "active_findings_without_not_matched": """
        SELECT count(*) FROM monitoring_findings f
        LEFT JOIN rosfin_matches m ON m.id = f.rosfin_match_id
        WHERE f.active AND (m.id IS NULL OR m.status <> 'not_matched')
    """,
    "active_findings_without_political": """
        SELECT count(*) FROM monitoring_findings f
        LEFT JOIN persecution_classifications c ON c.id = f.persecution_classification_id
        WHERE f.active AND (c.id IS NULL OR c.status <> 'political')
    """,
}


def check_invariants(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            name: int(connection.execute(text(sql)).scalar_one())
            for name, sql in INVARIANTS.items()
        }
