"""What the database already holds of an expected fact, asked of the database.

The manual set is annotated from open sources; this says, per fact, whether the
pipeline already knows it (`in_db_fact`), whether a publication of that very person
already says it in its own text (`in_db_text`), and whether the person is in the database
at all (`in_db_person`). The web is credited only with what the database does not hold.

Read only: SELECTs in a read-only transaction.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

_PERSON = text(
    """
    SELECT g.article_count
    FROM entity_groups g WHERE lower(g.name) = lower(:name)
    """
)


# The database keeps an article as its number alone («291.1»), while the annotation writes
# it as it is written in the law («ч. 4 ст. 291.1 УК»). Matching the whole phrase called
# that fact «not in the database» and credited the web with what the pipeline already had.
_ARTICLE_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _article_numbers(value: str) -> list[str]:
    return _ARTICLE_NUMBER.findall(value or "")


_HAS_FACT = {
    "charges": "SELECT 1 FROM entity_group_charges c JOIN entity_groups g ON g.id = c.group_id"
    " WHERE lower(g.name) = lower(:name) AND position(:value in c.article) > 0 LIMIT 1",
    "latest_news": "SELECT 1 FROM entity_group_news n JOIN entity_groups g ON g.id = n.group_id"
    " WHERE lower(g.name) = lower(:name) AND lower(n.kind) = lower(:value) LIMIT 1",
    "role": "SELECT 1 FROM entity_group_roles r JOIN entity_groups g ON g.id = r.group_id"
    " WHERE lower(g.name) = lower(:name) AND lower(r.role) = lower(:value) LIMIT 1",
    "politics": "SELECT 1 FROM entity_group_politics p JOIN entity_groups g ON g.id = p.group_id"
    " WHERE lower(g.name) = lower(:name) AND lower(p.verdict) = lower(:value) LIMIT 1",
}

_VALUE_IN_TEXT = text(
    """
    SELECT 1 FROM parsed_articles a
    JOIN entity_group_mentions m ON m.mention_id = a.id
    JOIN entity_groups g ON g.id = m.group_id
    WHERE lower(g.name) = lower(:name) AND position(lower(:value) in lower(a.text)) > 0
    LIMIT 1
    """
)
_PERSON_HAS_TEXT = text(
    """
    SELECT 1 FROM parsed_articles a
    JOIN entity_group_mentions m ON m.mention_id = a.id
    JOIN entity_groups g ON g.id = m.group_id
    WHERE lower(g.name) = lower(:name)
    LIMIT 1
    """
)

YES, NO, PARTLY = "yes", "no", "partly"


def presence(session: Session, name: str, fact: str, value: str) -> dict[str, Any]:
    """What the database holds of one expected fact: the person, the fact, the value in a
    publication of that person. `web_only` is what the web may be credited with."""
    person = session.execute(_PERSON, {"name": name}).first()
    in_db_person = person is not None
    articles = int(person[0]) if person and person[0] is not None else 0
    in_db_fact = False
    if fact == "charges":
        # The number is what both sides write: the pipeline's column and the annotation's
        # phrase carry the same number in a different dress.
        for number in _article_numbers(value):
            row = session.execute(
                text(_HAS_FACT["charges"]), {"name": name, "value": number}
            ).first()
            if row is not None:
                in_db_fact = True
                break
    else:
        query = _HAS_FACT.get(fact)
        if query is not None:
            in_db_fact = (
                session.execute(text(query), {"name": name, "value": value}).first() is not None
            )
    has_text = session.execute(_PERSON_HAS_TEXT, {"name": name}).first() is not None
    in_db_text = (
        session.execute(_VALUE_IN_TEXT, {"name": name, "value": value}).first() is not None
        if has_text
        else False
    )
    if in_db_fact:
        label = YES
    elif in_db_text:
        label = PARTLY
    else:
        label = NO
    return {
        "person": name,
        "fact": fact,
        "value": value,
        "in_db_person": in_db_person,
        "in_db_articles": articles,
        "in_db_fact": in_db_fact,
        "in_db_text": in_db_text,
        "web_only": label == NO,
        "state": label,
    }
