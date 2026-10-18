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

from analysis import testing as analysis_testing
from analysis.service import GroupFollowers
from processing import testing as processing_testing
from processing.evidence.publications import person_evidence

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
    for group_id, key, name, variants in processing_testing.golden_people(session, limit=people):
        relevant = sorted(
            {
                item.article_id
                for item in person_evidence(
                    session, GroupFollowers(), groups=[group_id], quotes=False
                )
            }
        )
        # The order of the facts as one query of both owners gave them before.
        processing_facts = dict(processing_testing.group_facts(session, group_id))
        analysis_facts = dict(analysis_testing.group_facts(session, group_id))
        facts = {
            fact: value
            for fact in ("role", "politics", "charges", "latest_news", "rf_list")
            if (value := processing_facts.get(fact) or analysis_facts.get(fact))
        }
        given, surname = name.split()
        forms: dict[Form, str | None] = {
            "nominative": name,
            "declined": _declined(name, variants),
            "reversed": f"{surname} {given}",
            "initial": f"{given[0]}. {surname}",
        }
        for form, query in forms.items():
            if query:
                golden.persons.append(
                    PersonCase(
                        case_id=f"{key}|{form}",
                        query=query,
                        form=form,
                        entity_key=key,
                        name=name,
                        relevant_articles=relevant,
                        facts=facts,
                    )
                )
    for surname, keys in processing_testing.namesake_surnames(session, limit=namesakes):
        golden.namesakes.append(
            NamesakeCase(
                case_id=f"namesake|{surname}", query=surname.capitalize(), entity_keys=list(keys)
            )
        )
    for article_id, resolution in analysis_testing.unnamed_articles(session, limit=unnamed):
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
