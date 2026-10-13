"""The hand-annotated cases, asked two ways and reported fact by fact.

For every expected fact of the manual set: what the database already holds of it, whether
the run reported it, and — for each mode — whether some evidence holds the whole value in
a text about that person, and which evidence that was. The loose form of the same value is
counted beside the strict one, so a report can say how much the loose form would have
claimed.

The unnamed cases and the namesakes are asked too: the right answer for a figurant whose
name no source gives is «не установлено», and a namesake asked by surname alone should be
answered with candidates rather than with one person's facts.

Read-only; no run is stored. The web mode spends real credits and is asked for by name.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from evaluation.investigation.presence import presence
from evaluation.investigation.web_benchmark import (
    CREDITS_PER_SEARCH,
    CountingSearch,
    _holds_all,
    batch_texts,
    ceilings,
    service,
)
from investigation.models import InvestigationResult
from investigation.services.external import names


def load(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def _fact_rows(
    session: Session, case: Mapping[str, Any], result: InvestigationResult | None
) -> list[dict[str, Any]]:
    """One row per expected fact: what the database holds of it, and what the run did."""
    rows: list[dict[str, Any]] = []
    for entry in case.get("expected", []):
        if entry.get("kind"):
            rows.append(
                {
                    "fact": entry.get("kind"),
                    "value": entry.get("value"),
                    "in_db": "—",
                    "expected_answer": "не установлено / другой человек",
                }
            )
            continue
        state = presence(session, str(case["query"]), str(entry["fact"]), str(entry["value"]))
        parts = list(entry.get("parts") or [entry["value"]])
        row: dict[str, Any] = {
            "fact": entry["fact"],
            "value": entry["value"],
            "in_db": state["state"],
            "web_only": state["web_only"],
            "source": entry.get("source"),
        }
        if result is not None:
            found = [
                item
                for item in result.evidence
                if names(item.text, str(case["query"])) and _holds_all([item.text], parts)
            ]
            loose = [
                item
                for item in result.evidence
                if names(item.text, str(case["query"]))
                and _holds_all([item.text], [entry.get("common")])
            ]
            row.update(
                {
                    "found_strict": bool(found),
                    "found_common_form_only": bool(loose) and not bool(found),
                    "evidence": [
                        {"ref": item.ref, "source": item.source_id, "url": item.url}
                        for item in found
                    ][:3],
                }
            )
        rows.append(row)
    return rows


def _person_report(
    session: Session,
    session_factory: sessionmaker[Session],
    case: Mapping[str, Any],
    *,
    use_web: bool,
    with_model: bool,
) -> dict[str, Any]:
    built, counted = service(
        session_factory, use_web=use_web, use_telegram=True, with_model=with_model
    )
    started = time.perf_counter()
    result = asyncio.run(built.investigate(f"Что известно про {case['query']}?"))
    elapsed = (time.perf_counter() - started) * 1000
    web_items = [item for item in result.evidence if item.source_id == "web:tavily"]
    return {
        "case_id": case["case_id"],
        "group": case.get("group"),
        "status": result.status,
        "findings": [finding.statement for finding in result.findings],
        "missing": result.missing_information,
        "facts": _fact_rows(session, case, result),
        "evidence": len(result.evidence),
        "web_evidence": len(web_items),
        "web_leads": sum(1 for item in web_items if item.leads),
        "searches": result.usage.search_calls,
        "tavily_requests": counted[0].requests if counted else 0,
        "tavily_results": counted[0].results if counted else 0,
        "tavily_credits": (counted[0].requests if counted else 0) * CREDITS_PER_SEARCH,
        "llm_calls": result.usage.llm_calls,
        "cost_usd": round(result.usage.cost_usd, 6),
        "elapsed_ms": round(elapsed),
        "model_summary": result.model_summary,
        "warnings": result.warnings,
        "web_batches": [dict(batch) for batch in counted[0].batches] if counted else [],
        "local_shown": sorted(
            {
                str(item.url)
                for item in result.evidence
                if item.url and item.source_id != "web:tavily"
            }
        ),
    }


def _unnamed_report(
    session_factory: sessionmaker[Session],
    case: Mapping[str, Any],
    *,
    use_web: bool,
    with_model: bool = False,
) -> dict[str, Any]:
    built, counted = service(
        session_factory, use_web=use_web, use_telegram=True, with_model=with_model
    )
    started = time.perf_counter()
    result = asyncio.run(built.investigate(f"Установи фигуранта публикации {case['article_id']}"))
    return {
        "case_id": case["case_id"],
        "group": case.get("group"),
        "name_verified": case.get("name_verified"),
        "expected_answer": "не установлено" if not case.get("name_verified") else "имя",
        "status": result.status,
        "leading_announced": any(
            finding.fact == "probable_candidate" for finding in result.findings
        ),
        "candidates": len(result.candidates),
        "corroborations": sum(1 for item in result.evidence if "corroboration" in item.leads),
        "web_corroborations": sum(
            1
            for item in result.evidence
            if item.source_id == "web:tavily" and "corroboration" in item.leads
        ),
        "web_evidence": sum(1 for item in result.evidence if item.source_id == "web:tavily"),
        "tavily_requests": counted[0].requests if counted else 0,
        "tavily_credits": (counted[0].requests if counted else 0) * CREDITS_PER_SEARCH,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "warnings": result.warnings,
    }


def _namesake_report(
    session_factory: sessionmaker[Session],
    pair: Mapping[str, Any],
    *,
    use_web: bool,
) -> dict[str, Any]:
    """By surname alone the right answer is candidates; by full name, only that person's
    facts. A web item that names the other person is counted separately."""
    built, counted = service(session_factory, use_web=use_web, use_telegram=True, with_model=False)
    by_surname = asyncio.run(built.investigate(f"Что известно про {pair['query']}?"))
    others = [str(person["name"]) for person in pair.get("people", [])]
    per_person: list[dict[str, Any]] = []
    for person in pair.get("people", []):
        name = str(person["name"])
        others_here = [other for other in others if other != name]
        result = asyncio.run(built.investigate(f"Что известно про {name}?"))
        foreign = [
            item.url
            for item in result.evidence
            if item.source_id == "web:tavily"
            and any(names(item.text, other) and not names(item.text, name) for other in others_here)
        ]
        per_person.append(
            {
                "name": name,
                "in_db_charges": person.get("in_db_charges"),
                "status": result.status,
                "candidates": [
                    candidate.name
                    for candidate in (result.subject.candidates if result.subject else [])
                ],
                "web_evidence": sum(
                    1 for item in result.evidence if item.source_id == "web:tavily"
                ),
                "web_items_naming_another": foreign[:5],
            }
        )
    return {
        "case_id": pair["case_id"],
        "by_surname_status": by_surname.status,
        "by_surname_candidates": len(by_surname.subject.candidates if by_surname.subject else []),
        "by_surname_announced_one": by_surname.subject is not None
        and bool(by_surname.subject.entity_key),
        "people": per_person,
        "tavily_requests": counted[0].requests if counted else 0,
        "tavily_credits": (counted[0].requests if counted else 0) * CREDITS_PER_SEARCH,
    }


def run(
    session_factory: sessionmaker[Session],
    document: Mapping[str, Any],
    *,
    use_web: bool,
    with_model: bool = False,
    kinds: Sequence[str] = ("persons", "unnamed", "namesakes"),
) -> dict[str, Any]:
    """The manual set, one mode: every fact, every case."""
    persons: list[dict[str, Any]] = []
    unnamed: list[dict[str, Any]] = []
    namesakes: list[dict[str, Any]] = []
    if "persons" in kinds:
        with session_factory() as session:
            for case in document.get("cases", []):
                if case.get("group") == "noise":
                    continue
                persons.append(
                    _person_report(
                        session, session_factory, case, use_web=use_web, with_model=with_model
                    )
                )
    if "unnamed" in kinds:
        for case in document.get("unnamed_cases", []):
            unnamed.append(_unnamed_report(session_factory, case, use_web=use_web))
    if "namesakes" in kinds:
        for pair in document.get("namesake_pairs", []):
            namesakes.append(_namesake_report(session_factory, pair, use_web=use_web))
    return {
        "mode": "local+telegram+web" if use_web else "local+telegram",
        "with_llm": with_model,
        "persons": persons,
        "unnamed": unnamed,
        "namesakes": namesakes,
        "credits": sum(
            report.get("tavily_credits", 0) for report in [*persons, *unnamed, *namesakes]
        ),
        "cost_usd": round(sum(float(report.get("cost_usd") or 0) for report in persons), 6),
    }


def displacement(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Local pages a reader saw without the web and does not see with it."""
    left = {report["case_id"]: report for report in before.get("persons", [])}
    right = {report["case_id"]: report for report in after.get("persons", [])}
    rows: list[dict[str, Any]] = []
    for case_id, one in left.items():
        two = right.get(case_id)
        if two is None:
            continue
        lost = sorted(set(one["local_shown"]) - set(two["local_shown"]))
        if lost:
            rows.append({"case_id": case_id, "lost": len(lost), "urls": lost[:6]})
    return rows


def fact_table(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per expected fact of every manual case: in the database, found in A, in B,
    and by which evidence."""
    left = {report["case_id"]: report for report in before.get("persons", [])}
    right = {report["case_id"]: report for report in after.get("persons", [])}
    rows: list[dict[str, Any]] = []
    for case_id, one in left.items():
        two = right.get(case_id, {})
        for row_one, row_two in zip(one["facts"], two.get("facts", []), strict=False):
            rows.append(
                {
                    "case_id": case_id,
                    "group": one.get("group"),
                    "fact": row_one["fact"],
                    "value": row_one["value"],
                    "in_db": row_one.get("in_db"),
                    "found_a": row_one.get("found_strict"),
                    "found_b": row_two.get("found_strict"),
                    "common_form_only_b": row_two.get("found_common_form_only"),
                    "evidence_b": row_two.get("evidence"),
                    "source": row_one.get("source"),
                }
            )
    return rows


def summary(reports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What one mode cost and gave, over the manual set."""
    rows: list[dict[str, Any]] = []
    for report in reports:
        persons = report.get("persons", [])
        rows.append(
            {
                "mode": report.get("mode"),
                "with_llm": report.get("with_llm"),
                "persons": len(persons),
                "facts_expected": sum(
                    1 for p in persons for row in p["facts"] if row.get("in_db") != "—"
                ),
                "facts_found": sum(
                    1 for p in persons for row in p["facts"] if row.get("found_strict")
                ),
                "facts_found_web": sum(
                    1
                    for p in persons
                    for row in p["facts"]
                    if row.get("found_strict")
                    and any(item["source"] == "web:tavily" for item in (row.get("evidence") or []))
                ),
                "facts_common_form_only": sum(
                    1 for p in persons for row in p["facts"] if row.get("found_common_form_only")
                ),
                "web_evidence": sum(p.get("web_evidence", 0) for p in persons),
                "web_leads": sum(p.get("web_leads", 0) for p in persons),
                "unnamed_announced": sum(
                    1 for u in report.get("unnamed", []) if u.get("leading_announced")
                ),
                "unnamed_cases": len(report.get("unnamed", [])),
                "namesake_picked_one_alone": sum(
                    1 for n in report.get("namesakes", []) if n.get("by_surname_announced_one")
                ),
                "namesake_pairs": len(report.get("namesakes", [])),
                "web_items_naming_another": sum(
                    len(p.get("web_items_naming_another", []))
                    for n in report.get("namesakes", [])
                    for p in n.get("people", [])
                ),
                "tavily_requests": sum(p.get("tavily_requests", 0) for p in persons)
                + sum(u.get("tavily_requests", 0) for u in report.get("unnamed", []))
                + sum(n.get("tavily_requests", 0) for n in report.get("namesakes", [])),
                "credits": report.get("credits", 0),
                "llm_calls": sum(p.get("llm_calls", 0) for p in persons),
                "cost_usd": report.get("cost_usd", 0),
                "elapsed_ms_mean": round(
                    sum(p.get("elapsed_ms", 0) for p in persons) / len(persons), 1
                )
                if persons
                else 0,
            }
        )
    return rows


__all__ = [
    "CountingSearch",
    "batch_texts",
    "ceilings",
    "displacement",
    "fact_table",
    "load",
    "run",
    "summary",
]
