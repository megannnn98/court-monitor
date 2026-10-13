"""The same questions asked two ways: `local + Telegram` and the same plus the web.

What is measured is what a reader of the result gets, and what it cost:

- **facts**: of the facts the golden set holds of the person, how many the result
  reports, and what it reports that the golden set does not hold. The web cannot move
  this — a text found outside is a lead, never a fact (ADR 0027) — and the benchmark
  says so with numbers rather than with an assumption;
- **material**: of the pages we already know exist about the person (their own
  publications, by canonical address), how many the run actually surfaced, per source.
  This is where an outside source can add something;
- **unnamed**: of the figurants, whether the candidates the run ranks are the ones a
  corroborating publication names, and whether the likeliest one is announced;
- the price: searches, Tavily requests and results, credits, model calls and their
  cost, and the time a run took.

Read-only: the database is opened with a read-only transaction and no run is stored.
The web mode spends real credits; it is asked for by name (`web`), never by default.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from application import build_investigation_retrievers
from evaluation.investigation.golden import GoldenSet, PersonCase, UnnamedCase
from investigation.models import InvestigationResult
from investigation.service import InvestigationService
from investigation.services.model import investigation_model_from_env

# What the web and the channels are asked for when the run is measured: the defaults,
# so that a measured run is the run an operator would get.
TELEGRAM_ENV = "INVESTIGATION_TELEGRAM_CHANNELS"
WEB = "web"
TELEGRAM_PREFIX = "telegram"
# One credit per basic search; the answer says so too (`usage.credits`), and this is what
# the count is priced with when the answer is not read.
CREDITS_PER_SEARCH = 1

_URLS_OF_ARTICLES = text(
    """
    SELECT d.canonical_url
    FROM parsed_articles a JOIN source_documents d ON d.id = a.document_id
    WHERE a.id = ANY(:ids)
    """
)


def canonical(url: str | None) -> str:
    """An address as two sources write it alike; what one page is, whatever wrote it."""
    if not url:
        return ""
    value = url.split("#", 1)[0].split("?", 1)[0].lower()
    value = value.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return value.rstrip("/")


class CountingSearch:
    """The retriever's own search, counting what the run asked the source and what it
    brought back. A proxy, so the source itself is not changed to be measurable."""

    def __init__(self, search: Any) -> None:
        self._search = search
        self.requests = 0
        self.results = 0

    @property
    def source(self) -> str:
        return str(self._search.source)

    @property
    def title(self) -> str:
        return str(self._search.title)

    async def search(self, query: Any) -> list[Any]:
        self.requests += 1
        found: list[Any] = await self._search.search(query)
        self.results += len(found)
        return found


def service(
    session_factory: sessionmaker[Session],
    *,
    use_web: bool,
    use_telegram: bool = True,
    with_model: bool = False,
    env: Mapping[str, str] | None = None,
) -> tuple[InvestigationService, list[CountingSearch]]:
    """The registry of one mode, and the counting proxies of what it asked outside."""
    settings = dict(env or os.environ)
    settings[TELEGRAM_ENV] = "" if use_telegram else "none"
    if not use_web:
        settings.pop("TAVILY_API_KEY", None)
    retrievers = build_investigation_retrievers(session_factory, env=settings)
    counted: list[CountingSearch] = []
    for retriever in retrievers:
        if retriever.name == "web:tavily":
            counting = CountingSearch(retriever._search)  # type: ignore[attr-defined]
            retriever._search = counting  # type: ignore[attr-defined]
            counted.append(counting)
    return (
        InvestigationService(
            retrievers=retrievers,
            model=investigation_model_from_env(settings) if with_model else None,
        ),
        counted,
    )


def _value(statement: str) -> str:
    value = statement.split(": ", 1)[1] if ": " in statement else statement
    return value.split(" (", 1)[0]


def _reported(result: InvestigationResult) -> set[tuple[str, str]]:
    return {(finding.fact, _value(finding.statement)) for finding in result.findings}


def material(session: Session, case: PersonCase) -> set[str]:
    """The pages we already know exist about this person: their own publications."""
    if not case.relevant_articles:
        return set()
    rows = session.execute(_URLS_OF_ARTICLES, {"ids": case.relevant_articles})
    return {canonical(str(row[0])) for row in rows}


def _shown_urls(result: InvestigationResult) -> set[str]:
    return {canonical(item.url) for item in result.evidence if item.url}


def _by_source(result: InvestigationResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in result.evidence:
        key = (
            "web"
            if item.source_id == "web:tavily"
            else "telegram"
            if (item.source_id.startswith(TELEGRAM_PREFIX))
            else "local"
        )
        counts[key] = counts.get(key, 0) + 1
    return counts


def measure_person(
    session: Session,
    built: InvestigationService,
    counted: Sequence[CountingSearch],
    case: PersonCase,
    *,
    max_followups: int | None = None,
) -> dict[str, Any]:
    """One person, asked once: what it reports, what it shows, what it cost."""
    question = f"Что известно про {case.query}?"
    expected = material(session, case)
    started = time.perf_counter()
    result = asyncio.run(built.investigate(question, max_followups=max_followups))
    elapsed = (time.perf_counter() - started) * 1000
    reported = _reported(result)
    held = {(fact, value) for fact, value in case.facts.items()}
    shown = _shown_urls(result)
    hits = expected & shown
    web_items = [item for item in result.evidence if item.source_id == "web:tavily"]
    return {
        "case_id": case.case_id,
        "kind": "person",
        "form": case.form,
        "status": result.status,
        "subject_correct": result.subject is not None
        and result.subject.entity_key == case.entity_key,
        "facts_expected": len(held),
        "facts_found": len(reported & held),
        "facts_recall": round(len(reported & held) / len(held), 3) if held else None,
        "facts_precision": round(len(reported & held) / len(reported), 3) if reported else None,
        "false_facts": sorted(f"{fact}={value}" for fact, value in reported - held),
        "missing": result.missing_information,
        "material_expected": len(expected),
        "material_found": len(hits),
        "material_recall": round(len(hits) / len(expected), 3) if expected else None,
        "evidence": len(result.evidence),
        "evidence_by_source": _by_source(result),
        "leads": sum(1 for item in result.evidence if item.leads),
        "web_leads": sum(1 for item in web_items if item.leads),
        "searches": result.usage.search_calls,
        "network_searches": result.usage.network_searches,
        "failed_searches": result.usage.failed_searches,
        "followup_rounds": result.usage.followup_rounds,
        "llm_calls": result.usage.llm_calls,
        "cost_usd": round(result.usage.cost_usd, 6),
        "tavily_requests": counted[0].requests if counted else 0,
        "tavily_results": counted[0].results if counted else 0,
        "tavily_credits": (counted[0].requests if counted else 0) * CREDITS_PER_SEARCH,
        "elapsed_ms": round(elapsed),
        "warnings": result.warnings,
    }


def measure_unnamed(
    built: InvestigationService,
    counted: Sequence[CountingSearch],
    case: UnnamedCase,
) -> dict[str, Any]:
    """One unnamed figurant: who the run ranks, who it announces, what it cost."""
    question = f"Установи фигуранта публикации {case.article_id}"
    started = time.perf_counter()
    result = asyncio.run(built.investigate(question))
    elapsed = (time.perf_counter() - started) * 1000
    web_items = [item for item in result.evidence if item.source_id == "web:tavily"]
    leading = next(
        (finding.statement for finding in result.findings if finding.fact == "probable_candidate"),
        None,
    )
    return {
        "case_id": case.case_id,
        "kind": "unnamed",
        "status": result.status,
        "operator_resolution": case.resolution,
        "candidates": len(result.candidates),
        "leading_announced": leading is not None,
        "leading": leading,
        "contradicts_operator": bool(case.resolution == "no_rf_match" and leading is not None),
        "evidence": len(result.evidence),
        "evidence_by_source": _by_source(result),
        "corroborations": sum(1 for item in result.evidence if "corroboration" in item.leads),
        "web_corroborations": sum(1 for item in web_items if "corroboration" in item.leads),
        "searches": result.usage.search_calls,
        "network_searches": result.usage.network_searches,
        "followup_rounds": result.usage.followup_rounds,
        "llm_calls": result.usage.llm_calls,
        "cost_usd": round(result.usage.cost_usd, 6),
        "tavily_requests": counted[0].requests if counted else 0,
        "tavily_results": counted[0].results if counted else 0,
        "tavily_credits": (counted[0].requests if counted else 0) * CREDITS_PER_SEARCH,
        "elapsed_ms": round(elapsed),
    }


def run(
    session_factory: sessionmaker[Session],
    golden: GoldenSet,
    *,
    use_web: bool,
    persons: Sequence[PersonCase],
    unnamed: Sequence[UnnamedCase],
    with_model: bool = False,
) -> dict[str, Any]:
    """One mode over the cases: every measurement, every case."""
    built, counted = service(
        session_factory, use_web=use_web, use_telegram=True, with_model=with_model
    )
    cases: list[dict[str, Any]] = []
    with session_factory() as session:
        for case in persons:
            cases.append(measure_person(session, built, counted, case))
    for figurante in unnamed:
        cases.append(measure_unnamed(built, counted, figurante))
    return {"mode": "local+telegram+web" if use_web else "local+telegram", "cases": cases}


def _mean(values: Sequence[float]) -> float:
    held = [value for value in values if value is not None]
    return round(sum(held) / len(held), 3) if held else 0.0


def summarise(report: Mapping[str, Any]) -> dict[str, Any]:
    """One row per mode: what the reader gets and what it costs."""
    cases = list(report.get("cases", []))
    people = [case for case in cases if case.get("kind") == "person"]
    unnamed = [case for case in cases if case.get("kind") == "unnamed"]

    def total(key: str, where: Sequence[dict[str, Any]] = cases) -> int:
        return sum(int(case.get(key) or 0) for case in where)

    def average(key: str, where: Sequence[dict[str, Any]] = cases) -> float:
        return round(total(key, where) / len(where), 2) if where else 0.0

    closed = [case for case in people if case["status"] == "completed"]
    return {
        "mode": report.get("mode"),
        "cases": len(cases),
        "person_cases": len(people),
        "subject_accuracy": _mean([float(c["subject_correct"]) for c in people]) if people else 0.0,
        "facts_recall_mean": _mean([case.get("facts_recall") for case in people]),
        "facts_precision_mean": _mean([case.get("facts_precision") for case in people]),
        "false_facts": total("false_facts", people),
        "dossiers_closed": len(closed),
        "material_recall_mean": _mean([case.get("material_recall") for case in people]),
        "material_found": total("material_found", people),
        "material_expected": total("material_expected", people),
        "leads": total("leads"),
        "web_leads": total("web_leads"),
        "evidence_mean": average("evidence"),
        "evidence_local": total("evidence"),
        "searches_mean": average("searches"),
        "searches_total": total("searches"),
        "followup_rounds_mean": average("followup_rounds"),
        "failed_searches": total("failed_searches"),
        "llm_calls": total("llm_calls"),
        "cost_usd": round(sum(float(case.get("cost_usd") or 0) for case in cases), 6),
        "tavily_requests": total("tavily_requests"),
        "tavily_results": total("tavily_results"),
        "tavily_credits": total("tavily_credits"),
        "tavily_requests_mean": average("tavily_requests"),
        "tavily_results_mean": average("tavily_results"),
        "elapsed_ms_mean": average("elapsed_ms"),
        "elapsed_ms_total": total("elapsed_ms"),
        "unnamed_cases": len(unnamed),
        "unnamed_leading_announced": sum(1 for c in unnamed if c.get("leading_announced")),
        "unnamed_contradicts_operator": sum(1 for c in unnamed if c.get("contradicts_operator")),
        "web_corroborations": total("web_corroborations", unnamed),
        "corroborations": total("corroborations", unnamed),
    }


def delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """What the web changed, sign by sign: B minus A."""
    left, right = summarise(before), summarise(after)
    return {
        key: round(float(right.get(key, 0)) - float(left.get(key, 0)), 3)
        for key in left
        if isinstance(right.get(key), (int, float))
        and key not in {"mode", "cases", "person_cases", "unnamed_cases", "material_expected"}
    }


def as_markdown(reports: Sequence[Mapping[str, Any]]) -> str:
    rows = [summarise(report) for report in reports]
    header = (
        "| режим | кейсов | recall фактов | precision | ложных фактов | закрытых досье "
        "| recall материала | наводок | улик/кейс | поисков/кейс | кругов | Tavily | "
        "кредитов | LLM | USD | мс/кейс |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    lines = [header]
    for row in rows:
        lines.append(
            f"| {row['mode']} | {row['cases']} | {row['facts_recall_mean']} | "
            f"{row['facts_precision_mean']} | {row['false_facts']} | {row['dossiers_closed']} | "
            f"{row['material_recall_mean']} ({row['material_found']}/{row['material_expected']}) | "
            f"{row['leads']} | {row['evidence_mean']} | {row['searches_mean']} | "
            f"{row['followup_rounds_mean']} | {row['tavily_requests']} | {row['tavily_credits']} | "
            f"{row['llm_calls']} | {row['cost_usd']} | {row['elapsed_ms_mean']} |"
        )
    if len(rows) == 2:
        change = delta(reports[0], reports[1])
        lines.append("")
        lines.append("### Что изменил web (B − A)")
        lines.append("")
        for key, value in change.items():
            if value:
                lines.append(f"- `{key}`: {value:+}")
    return "\n".join(lines)
