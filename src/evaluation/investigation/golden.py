"""A golden set of investigations, read from a database of real Court Monitor data.

Read only: the builder runs SELECTs in a read-only transaction. The set names real
people from the news, so it is written under `var/` (ignored), never committed.

Cases:
- person: one entity group asked about by its name as the news wrote it — the
  nominative, a declined form, the surname first, an initial. The relevant documents
  are the publications whose mentions made the group; the facts are what the
  pipeline decided of it.
- namesake: a surname two or more groups share, asked about alone: the right answer
  is to name the candidates and send the question to a person.
- unnamed: a publication with an unnamed figurant; its operator resolution, if any.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from processing.evidence.publications import person_evidence_cte

Form = Literal["nominative", "declined", "reversed", "initial"]


@dataclass
class PersonCase:
    case_id: str
    query: str
    form: Form
    entity_key: str
    name: str
    relevant_articles: list[int]
    facts: dict[str, str]
    kind: Literal["person"] = "person"


@dataclass
class NamesakeCase:
    case_id: str
    query: str
    entity_keys: list[str]
    kind: Literal["namesake"] = "namesake"


@dataclass
class UnnamedCase:
    case_id: str
    article_id: int
    resolution: str | None
    kind: Literal["unnamed"] = "unnamed"


@dataclass
class GoldenSet:
    persons: list[PersonCase] = field(default_factory=list)
    namesakes: list[NamesakeCase] = field(default_factory=list)
    unnamed: list[UnnamedCase] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> GoldenSet:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            persons=[PersonCase(**item) for item in data["persons"]],
            namesakes=[NamesakeCase(**item) for item in data["namesakes"]],
            unnamed=[UnnamedCase(**item) for item in data["unnamed"]],
        )


_PEOPLE = text(
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
_ARTICLES = text(
    f"""
    WITH {person_evidence_cte()}
    SELECT DISTINCT e.article_id FROM person_evidence e
    WHERE e.group_id = :group_id
    """
)
_FACTS = text(
    """
    SELECT 'role' AS fact, r.role AS value FROM entity_group_roles r WHERE r.group_id = :id
    UNION ALL
    SELECT 'politics', p.verdict FROM entity_group_politics p WHERE p.group_id = :id
    UNION ALL
    SELECT 'charges', string_agg(DISTINCT c.article, ', ' ORDER BY c.article)
      FROM entity_group_charges c WHERE c.group_id = :id HAVING count(*) > 0
    UNION ALL
    SELECT 'latest_news', n.kind FROM entity_group_news n WHERE n.group_id = :id
    UNION ALL
    SELECT 'rf_list', string_agg(DISTINCT m.level, ', ' ORDER BY m.level)
      FROM entity_group_rf_matches m WHERE m.group_id = :id HAVING count(*) > 0
    """
)
_SURNAMES = text(
    """
    SELECT split_part(g.key, ' ', 2) AS surname, array_agg(g.key ORDER BY g.mention_count DESC)
    FROM entity_groups g
    WHERE array_length(regexp_split_to_array(g.key, '\\s+'), 1) = 2
    GROUP BY 1 HAVING count(*) >= 2 AND length(split_part(g.key, ' ', 2)) >= 5
    ORDER BY count(*) DESC, 1
    LIMIT :limit
    """
)
_UNNAMED = text(
    """
    SELECT DISTINCT ON (u.article_id) u.article_id, ir.resolution
    FROM unnamed_figurants u
    LEFT JOIN unnamed_identity_resolutions ir ON ir.figurant_key = u.key
    ORDER BY u.article_id, ir.resolution NULLS LAST
    LIMIT :limit
    """
)


def _declined(name: str, variants: list[Any]) -> str | None:
    """The most frequent form the news wrote that is not the nominative."""
    for form, _count in variants or []:
        if isinstance(form, str) and form != name and len(form.split()) == 2:
            return form
    return None


def build(
    session: Session, *, people: int = 25, namesakes: int = 6, unnamed: int = 10
) -> GoldenSet:
    session.execute(text("SET TRANSACTION READ ONLY"))
    golden = GoldenSet()
    for row in session.execute(_PEOPLE, {"limit": people}):
        relevant = sorted(session.scalars(_ARTICLES, {"group_id": row.id, "context": 160}))
        facts = {fact: value for fact, value in session.execute(_FACTS, {"id": row.id}) if value}
        given, surname = row.name.split()
        forms: dict[Form, str | None] = {
            "nominative": row.name,
            "declined": _declined(row.name, row.variants),
            "reversed": f"{surname} {given}",
            "initial": f"{given[0]}. {surname}",
        }
        for form, query in forms.items():
            if query:
                golden.persons.append(
                    PersonCase(
                        case_id=f"{row.key}|{form}",
                        query=query,
                        form=form,
                        entity_key=row.key,
                        name=row.name,
                        relevant_articles=relevant,
                        facts=facts,
                    )
                )
    for surname, keys in session.execute(_SURNAMES, {"limit": namesakes}):
        golden.namesakes.append(
            NamesakeCase(
                case_id=f"namesake|{surname}", query=surname.capitalize(), entity_keys=list(keys)
            )
        )
    for article_id, resolution in session.execute(_UNNAMED, {"limit": unnamed}):
        golden.unnamed.append(
            UnnamedCase(
                case_id=f"unnamed|{article_id}", article_id=article_id, resolution=resolution
            )
        )
    return golden


def summary(golden: GoldenSet) -> dict[str, Any]:
    forms: dict[str, int] = defaultdict(int)
    for case in golden.persons:
        forms[case.form] += 1
    return {
        "people": len({case.entity_key for case in golden.persons}),
        "person_cases": len(golden.persons),
        "forms": dict(forms),
        "namesakes": len(golden.namesakes),
        "unnamed": len(golden.unnamed),
    }
