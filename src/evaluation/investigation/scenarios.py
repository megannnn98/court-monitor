"""Whole investigations over the golden set: rules only, local sources only, nothing
stored. What is measured is the result a reader gets, not a ranking:

- subject: the person asked about is the entity group answering (or, for a name two
  groups share, the question goes to a person with both as candidates);
- facts: of the facts the pipeline holds of the person, how many the result reports;
- false facts: findings whose value the pipeline does not hold (should be none);
- unnamed: how often a result names a likeliest candidate on its own, and whether an
  operator's resolution agrees.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from application import build_investigation_retrievers
from evaluation.investigation.golden import GoldenSet
from evaluation.investigation.metrics import mean
from investigation.models import InvestigationResult
from investigation.service import InvestigationService

LOCAL = {"INVESTIGATION_TELEGRAM_CHANNELS": "none"}


def _service(session_factory: sessionmaker[Session]) -> InvestigationService:
    return InvestigationService(
        retrievers=build_investigation_retrievers(session_factory, env=LOCAL)
    )


def _value(statement: str) -> str:
    """A finding's value as the golden set holds it: a role's finer kind («figurant
    (accused)») is the step's own detail, the role is «figurant»."""
    value = statement.split(": ", 1)[1] if ": " in statement else statement
    return value.split(" (", 1)[0]


def evaluate(session_factory: sessionmaker[Session], golden: GoldenSet) -> dict[str, Any]:
    service = _service(session_factory)

    def ask(question: str) -> InvestigationResult:
        return asyncio.run(service.investigate(question, local_only=True))

    subject = Counter[str]()
    by_form: dict[str, Counter[str]] = {}
    covered: list[float] = []
    false_facts = 0
    statuses = Counter[str]()
    elapsed: list[float] = []
    for case in golden.persons:
        started = time.perf_counter()
        result = ask(f"Что известно про {case.query}?")
        elapsed.append((time.perf_counter() - started) * 1000)
        statuses[result.status] += 1
        found = result.subject
        if found is not None and found.entity_key == case.entity_key:
            subject["correct"] += 1
            by_form.setdefault(case.form, Counter())["correct"] += 1
        elif (
            found is not None
            and found.entity_key is None
            and any(candidate.entity_key == case.entity_key for candidate in found.candidates)
        ):
            subject["review_with_the_person_among_candidates"] += 1
            by_form.setdefault(case.form, Counter())["review_with_the_person_among_candidates"] += 1
        elif found is not None and found.entity_key is not None:
            subject["wrong_person"] += 1
            by_form.setdefault(case.form, Counter())["wrong_person"] += 1
        else:
            subject["not_found"] += 1
            by_form.setdefault(case.form, Counter())["not_found"] += 1
        reported = {(finding.fact, _value(finding.statement)) for finding in result.findings}
        held = {(fact, value) for fact, value in case.facts.items()}
        if found is not None and found.entity_key == case.entity_key and held:
            covered.append(len(reported & held) / len(held))
        false_facts += len({item for item in reported if item[0] in case.facts} - held)
    namesakes = Counter[str]()
    for namesake in golden.namesakes:
        result = ask(f"Что известно про {namesake.query}?")
        candidates = {c.entity_key for c in (result.subject.candidates if result.subject else [])}
        if result.status == "review_required" and len(candidates) >= 2:
            namesakes["review_requested"] += 1
        elif result.subject is not None and result.subject.entity_key:
            namesakes["picked_one_alone"] += 1
        else:
            namesakes["other"] += 1
    unnamed = Counter[str]()
    for figurant in golden.unnamed:
        result = ask(f"Установи фигуранта публикации {figurant.article_id}")
        unnamed[result.status] += 1
        if figurant.resolution == "no_rf_match" and any(
            finding.fact == "probable_candidate" for finding in result.findings
        ):
            unnamed["contradicts_operator_no_rf_match"] += 1
    return {
        "retrievers": sorted(retriever.name for retriever in service.retrievers),
        "person_cases": len(golden.persons),
        "subject": dict(subject),
        "subject_by_form": {form: dict(counts) for form, counts in sorted(by_form.items())},
        "subject_accuracy": round(subject["correct"] / max(len(golden.persons), 1), 3),
        "facts_covered_mean": round(mean(covered), 3),
        "false_factual_findings": false_facts,
        "statuses": dict(statuses),
        "latency_ms_mean": round(mean(elapsed), 1),
        "latency_ms_max": round(max(elapsed, default=0.0), 1),
        "namesakes": dict(namesakes),
        "unnamed": dict(unnamed),
    }
