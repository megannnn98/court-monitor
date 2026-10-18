"""Processing's explicit testing API: reads of its tables for the evaluation and tests.

Not for production code (`tests/test_data_ownership.py` holds every production module
to it): what is here samples or measures processing's storage itself, which no page or
step needs (ADR 0031, «Evaluation»).
"""

from __future__ import annotations

from typing import Any, NamedTuple

from sqlalchemy import text
from sqlalchemy.orm import Session

_GOLDEN_PEOPLE = text(
    """
    SELECT g.id, g.key, g.name, g.variants
    FROM entity_groups g
    JOIN entity_group_roles r ON r.group_id = g.id
    WHERE r.role IN ('figurant', 'possible') AND g.article_count >= 2
      AND array_length(regexp_split_to_array(g.name, '\\s+'), 1) = 2
    ORDER BY g.mention_count DESC, g.key
    LIMIT :limit
    """
)
_GROUP_FACTS = text(
    """
    SELECT 'role' AS fact, r.role AS value FROM entity_group_roles r WHERE r.group_id = :id
    UNION ALL
    SELECT 'charges', string_agg(DISTINCT c.article, ', ' ORDER BY c.article)
      FROM entity_group_charges c WHERE c.group_id = :id HAVING count(*) > 0
    """
)
_NAMESAKE_SURNAMES = text(
    """
    SELECT split_part(g.key, ' ', 2) AS surname, array_agg(g.key ORDER BY g.mention_count DESC)
    FROM entity_groups g
    WHERE array_length(regexp_split_to_array(g.key, '\\s+'), 1) = 2
    GROUP BY 1 HAVING count(*) >= 2 AND length(split_part(g.key, ' ', 2)) >= 5
    ORDER BY count(*) DESC, 1
    LIMIT :limit
    """
)


def golden_people(session: Session, *, limit: int) -> list[tuple[int, str, str, list[Any]]]:
    """(id, key, name, variants) of the figurants (or possible ones) of two words and two
    articles at least, the most mentioned first."""
    return [
        (row.id, row.key, row.name, row.variants)
        for row in session.execute(_GOLDEN_PEOPLE, {"limit": limit})
    ]


def group_facts(session: Session, group_id: int) -> list[tuple[str, str | None]]:
    """(fact, value) of a group: its role, its charges' articles «, »-joined."""
    return [(fact, value) for fact, value in session.execute(_GROUP_FACTS, {"id": group_id})]


def namesake_surnames(session: Session, *, limit: int) -> list[tuple[str, list[str]]]:
    """Surnames of two-word keys that two groups at least share, with their keys (the most
    mentioned first); the most shared surname first."""
    return [
        (surname, list(keys))
        for surname, keys in session.execute(_NAMESAKE_SURNAMES, {"limit": limit})
    ]


class ModelRole(NamedTuple):
    id: int
    name: str
    role: str


def model_roles(session: Session) -> list[ModelRole]:
    """The groups whose role a model gave (step 4), with that role."""
    return [
        ModelRole(*row)
        for row in session.execute(
            text(
                "SELECT g.id, g.name, r.role FROM entity_groups g "
                "JOIN entity_group_roles r ON r.group_id = g.id WHERE r.method = 'model'"
            )
        )
    ]


class ModelName(NamedTuple):
    name: str
    variants: list[Any]


def model_names(session: Session) -> list[ModelName]:
    """The groups whose name a model gave (step 3), with the forms the news wrote."""
    return [
        ModelName(*row)
        for row in session.execute(
            text("SELECT name, variants FROM entity_groups WHERE name_source = 'model'")
        )
    ]


def sorted_charge_articles(session: Session, ids: list[int]) -> dict[int, list[str]]:
    """Per group, its charges' articles, each once, in the database's collation."""
    articles: dict[int, list[str]] = {}
    for group_id, article in session.execute(
        text(
            "SELECT DISTINCT group_id, article FROM entity_group_charges "
            "WHERE group_id = ANY(:groups) ORDER BY group_id, article"
        ),
        {"groups": ids},
    ).all():
        articles.setdefault(group_id, []).append(article)
    return articles


def person_mentions(session: Session) -> list[tuple[int, str, int, int | None]]:
    """(article, surface, mention id, ER v2 person) of every person mention, by id."""
    return [
        (article_id, surface, mention_id, person_id)
        for article_id, surface, mention_id, person_id in session.execute(
            text(
                "SELECT r.article_id, m.surface_text, m.id, m.person_id FROM entity_mentions m "
                "JOIN article_extraction_runs r ON r.id = m.extraction_run_id "
                "WHERE m.entity_type = 'person' ORDER BY m.id"
            )
        )
    ]


def resolution_decisions(session: Session) -> dict[int, tuple[str, str]]:
    """Per mention, the ER v2 decision's action and status."""
    return {
        mention_id: (action, status)
        for mention_id, action, status in session.execute(
            text("SELECT mention_id, action, status FROM person_resolution_decisions")
        )
    }
