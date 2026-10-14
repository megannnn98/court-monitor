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
    """One row per expected fact: what the database holds of it, and what the run did.

    Two questions, never one. `evidence_contains_expected_value` asks whether some piece
    of evidence held the whole value in a text about that person — retrieval. It is not a
    finding and is never reported as one. `confirmed_finding` asks whether the value came
    back as a `Finding` citing evidence — what a reader of the result is told. A retrieval
    hit that never became a finding is a gap in the extractor, and the row says so.
    """
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
            "parts": parts,
            "common": entry.get("common"),
        }
        if result is not None:
            about = [item for item in result.evidence if names(item.text, str(case["query"]))]
            found = [item for item in about if _holds_all([item.text], parts)]
            loose = [item for item in about if _holds_all([item.text], [entry.get("common")])]
            confirmed = [
                finding
                for finding in result.findings
                if finding.fact == entry["fact"] and _reported_value(finding) == str(entry["value"])
            ]
            row.update(
                {
                    # Retrieval: a page of the open web holds it.
                    "evidence_contains_expected_value": bool(found),
                    # The product: the run says it.
                    "confirmed_finding": bool(confirmed),
                    "confirmed_by": [
                        {
                            "ref": ref,
                            "source": next(
                                (item.source_id for item in result.evidence if item.ref == ref),
                                "—",
                            ),
                            "url": next(
                                (item.url for item in result.evidence if item.ref == ref), None
                            ),
                        }
                        for finding in confirmed
                        for ref in finding.evidence
                    ],
                    "evidence_contains_common_form_only": bool(loose) and not bool(found),
                    "evidence": [
                        {"ref": item.ref, "source": item.source_id, "url": item.url}
                        for item in found
                    ][:3],
                }
            )
        rows.append(row)
    return rows


def _reported_value(finding: Any) -> str:
    """The value a finding states, as the scenario wrote it: «статьи обвинения: <value>»."""
    statement: str = finding.statement
    if ": " in statement:
        statement = statement.split(": ", 1)[1]
    # `person_dossier` writes the charges the way the law writes them and nothing else.
    return statement.split(" (", 1)[0].strip()


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
    rows = _fact_rows(session, case, result)
    expected = [row for row in rows if row.get("in_db") != "—"]
    # A namesake's dossier has no expected facts of its own: what it must not do is report
    # somebody else's. Counted apart, because a web mode that looks better only because of
    # noise is worse, not better.
    false_findings = [
        {"fact": finding.fact, "statement": finding.statement}
        for finding in result.findings
        if not any(
            finding.fact == row["fact"] and _reported_value(finding) == row["value"]
            for row in expected
        )
    ]
    return {
        "case_id": case["case_id"],
        "group": case.get("group"),
        "status": result.status,
        "findings": [finding.statement for finding in result.findings],
        "missing": result.missing_information,
        "facts": rows,
        "facts_expected": len(expected),
        "evidence_contains_expected_value": sum(
            1 for row in expected if row.get("evidence_contains_expected_value")
        ),
        "confirmed_finding": sum(1 for row in expected if row.get("confirmed_finding")),
        "retrieval_hit_without_a_finding": sum(
            1
            for row in expected
            if row.get("evidence_contains_expected_value") and not row.get("confirmed_finding")
        ),
        "false_findings": false_findings,
        "evidence": len(result.evidence),
        "web_evidence": len(web_items),
        "web_leads": sum(1 for item in web_items if item.leads),
        "verified_web_facts": sum(
            1 for item in web_items if item.fact is not None and item.source_id.startswith("web:")
        ),
        "searches": result.usage.search_calls,
        "tavily_requests": counted[0].requests if counted else 0,
        "tavily_results": counted[0].results if counted else 0,
        "tavily_credits": counted[0].requests * counted[0].credits if counted else 0,
        "llm_calls": result.usage.llm_calls,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
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
        "tavily_results": counted[0].results if counted else 0,
        "tavily_credits": counted[0].requests * counted[0].credits if counted else 0,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "warnings": result.warnings,
    }


def _namesake_report(
    session_factory: sessionmaker[Session],
    pair: Mapping[str, Any],
    *,
    use_web: bool,
    with_model: bool = False,
) -> dict[str, Any]:
    """By surname alone the right answer is candidates; by full name, only that person's
    facts. A web item that names the other person is counted separately."""
    built, counted = service(
        session_factory, use_web=use_web, use_telegram=True, with_model=with_model
    )
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
                "model_summary": result.model_summary,
            }
        )
    return {
        "case_id": pair["case_id"],
        "by_surname_status": by_surname.status,
        "by_surname_candidates": len(by_surname.subject.candidates if by_surname.subject else []),
        "by_surname_announced_one": by_surname.subject is not None
        and bool(by_surname.subject.entity_key),
        "by_surname_summary": by_surname.model_summary,
        "people": per_person,
        "tavily_requests": counted[0].requests if counted else 0,
        "tavily_credits": counted[0].requests * counted[0].credits if counted else 0,
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
                # The noisy names are asked too: a person the web cannot place must
                # produce no fact at all, and that is worth showing in the same table.
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
            namesakes.append(
                _namesake_report(session_factory, pair, use_web=use_web, with_model=with_model)
            )
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
    """One row per expected fact of every manual case: in the database, and — for each
    mode — whether a piece of evidence held it and whether the run reported it.

    The two columns are separate on purpose. `evidence_a` / `evidence_b` is retrieval;
    `finding_a` / `finding_b` is what a reader was told. A mode that retrieves more and
    confirms the same has improved its search, not its answer.
    """
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
                    "evidence_a": row_one.get("evidence_contains_expected_value"),
                    "evidence_b": row_two.get("evidence_contains_expected_value"),
                    "finding_a": row_one.get("confirmed_finding"),
                    "finding_b": row_two.get("confirmed_finding"),
                    "confirmed_by": row_two.get("confirmed_by"),
                    "common_form_only_b": row_two.get("evidence_contains_common_form_only"),
                    "evidence_b_refs": row_two.get("evidence"),
                    "source": row_one.get("source"),
                }
            )
    return rows


def gained(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The facts the second mode confirms that the first did not — and the ones it lost.

    This is the one row of the report that says what the web bought, as opposed to what it
    retrieved. A fact already confirmed without the web is not the web's to credit.
    """
    rows = fact_table(before, after)
    return [
        {
            "case_id": row["case_id"],
            "fact": row["fact"],
            "value": row["value"],
            "gained": bool(row["finding_b"] and not row["finding_a"]),
            "lost": bool(row["finding_a"] and not row["finding_b"]),
            "evidence_only": bool(row["evidence_b"] and not row["finding_b"]),
            "confirmed_by": row["confirmed_by"],
            "source": row["source"],
        }
        for row in rows
        if row["finding_a"] != row["finding_b"] or (row["evidence_b"] and not row["finding_b"])
    ]


def summary(reports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What one mode cost and gave, over the manual set.

    Four numbers are kept apart on purpose and are never added to each other: how many
    expected facts some evidence held (retrieval), how many the run reported as findings
    (the product), how many were retrieved but never confirmed (a gap in the extractor),
    and how many findings nobody expected (precision).
    """
    rows: list[dict[str, Any]] = []
    for report in reports:
        persons = report.get("persons", [])
        expected = sum(p.get("facts_expected", 0) for p in persons)
        rows.append(
            {
                "mode": report.get("mode"),
                "with_llm": report.get("with_llm"),
                "persons": len(persons),
                "facts_expected": expected,
                # Retrieval.
                "evidence_contains_expected_value": sum(
                    p.get("evidence_contains_expected_value", 0) for p in persons
                ),
                "evidence_recall": _share(
                    sum(p.get("evidence_contains_expected_value", 0) for p in persons), expected
                ),
                # The product.
                "confirmed_finding": sum(p.get("confirmed_finding", 0) for p in persons),
                "finding_recall": _share(
                    sum(p.get("confirmed_finding", 0) for p in persons), expected
                ),
                "confirmed_by_web": sum(
                    1
                    for p in persons
                    for row in p["facts"]
                    if row.get("confirmed_finding")
                    and any(
                        item["source"].startswith("web:")
                        for item in (row.get("confirmed_by") or [])
                    )
                ),
                "retrieval_hit_without_a_finding": sum(
                    p.get("retrieval_hit_without_a_finding", 0) for p in persons
                ),
                # Precision: what the run said that nobody expected.
                "false_findings": sum(len(p.get("false_findings", [])) for p in persons),
                "cases_with_false_findings": sum(1 for p in persons if p.get("false_findings")),
                "verified_web_facts": sum(p.get("verified_web_facts", 0) for p in persons),
                "web_evidence": sum(p.get("web_evidence", 0) for p in persons),
                "web_leads": sum(p.get("web_leads", 0) for p in persons),
                "unnamed_announced": sum(
                    1 for u in report.get("unnamed", []) if u.get("leading_announced")
                ),
                "unnamed_cases": len(report.get("unnamed", [])),
                "unnamed_positive": sum(
                    1 for u in report.get("unnamed", []) if u.get("name_verified")
                ),
                "unnamed_positive_announced": sum(
                    1
                    for u in report.get("unnamed", [])
                    if u.get("name_verified") and u.get("leading_announced")
                ),
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
                "tavily_results": sum(p.get("tavily_results", 0) for p in persons)
                + sum(u.get("tavily_results", 0) for u in report.get("unnamed", []))
                + sum(n.get("tavily_results", 0) for n in report.get("namesakes", [])),
                "credits": report.get("credits", 0),
                "llm_calls": sum(p.get("llm_calls", 0) for p in persons),
                "input_tokens": sum(p.get("input_tokens", 0) for p in persons),
                "output_tokens": sum(p.get("output_tokens", 0) for p in persons),
                "cost_usd": report.get("cost_usd", 0),
                "elapsed_ms_mean": _mean(p.get("elapsed_ms", 0) for p in persons),
                "elapsed_ms_median": _median(p.get("elapsed_ms", 0) for p in persons),
            }
        )
    return rows


def _share(part: int, whole: int) -> float:
    return round(part / whole, 3) if whole else 0.0


def _mean(values: Any) -> float:
    held = [value for value in values if value]
    return round(sum(held) / len(held), 1) if held else 0.0


def _median(values: Any) -> float:
    held: list[float] = sorted(float(value) for value in values if value)
    if not held:
        return 0.0
    middle = len(held) // 2
    if len(held) % 2:
        return float(held[middle])
    return round((held[middle - 1] + held[middle]) / 2, 1)


__all__ = [
    "CountingSearch",
    "batch_texts",
    "ceilings",
    "displacement",
    "fact_table",
    "gained",
    "load",
    "run",
    "summary",
]
