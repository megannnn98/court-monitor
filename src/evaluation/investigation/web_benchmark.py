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
from investigation.services.external import names
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
        # What each request brought, in order: a ceiling on the queries a run may spend,
        # or on the results one may bring, is read back from here rather than paid for
        # twice.
        self.batches: list[dict[str, Any]] = []

    @property
    def source(self) -> str:
        return str(self._search.source)

    @property
    def title(self) -> str:
        return str(self._search.title)

    def mark(self) -> tuple[int, int, int]:
        """Where the counters stand now, so that what one case cost can be told from
        what the whole run has cost so far. One search object serves a whole run; its
        counters only ever grow."""
        return (self.requests, self.results, len(self.batches))

    def since(self, mark: tuple[int, int, int]) -> dict[str, Any]:
        """What was asked and brought between a mark and now: this case's own requests
        and batches, not the ones the earlier cases left behind."""
        requests, results, batches = mark
        asked = self.requests - requests
        return {
            "tavily_requests": asked,
            "tavily_results": self.results - results,
            "tavily_credits": asked * CREDITS_PER_SEARCH,
            "batches": [dict(batch) for batch in self.batches[batches:]],
        }

    async def search(self, query: Any) -> list[Any]:
        self.requests += 1
        found: list[Any] = await self._search.search(query)
        self.results += len(found)
        self.batches.append(
            {
                "query": str(getattr(query, "text", "")),
                "results": len(found),
                "items": [
                    {
                        "url": getattr(item, "url", ""),
                        "text": getattr(item, "text", ""),
                    }
                    for item in found
                ],
            }
        )
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


def _holds(text: str, value: object) -> bool:
    """A text holds a value of a fact as it is written. A part may be several ways of
    writing the same thing («ч. 2» or «части 2»): any of them is the part."""
    options = value if isinstance(value, list) else [value]
    folded = text.lower()
    return any(str(option).strip().lower() in folded for option in options)


def _sources(items: Sequence[Any]) -> dict[str, int]:
    """What kind of source the text of a lead came from."""
    return {
        "web": sum(1 for item in items if item.source_id == "web:tavily"),
        "telegram": sum(1 for item in items if item.source_id.startswith(TELEGRAM_PREFIX)),
        "local": sum(
            1
            for item in items
            if item.source_id != "web:tavily" and not item.source_id.startswith(TELEGRAM_PREFIX)
        ),
    }


def _useful_leads(
    result: InvestigationResult,
    expected: Mapping[str, Any],
    reported: set[tuple[str, str]],
    *,
    person: str,
) -> dict[str, Any]:
    """The facts the golden set holds and the result did not report: for each, whether some
    evidence holds the whole value and is about this person.

    A value is a list of parts, and all of them must stand in one text: «18 лет колонии
    строгого режима» is a verdict, «18 лет» alone is a bus ticket. The loose form of the
    same fact — the bare article number, the bare year — is counted beside the strict one,
    so a report can show how much the loose form would have claimed for itself.
    """
    found: list[dict[str, Any]] = []
    for fact, entry in sorted(expected.items()):
        value = str(entry["value"])
        if (fact, value) in reported:
            continue
        parts = [part for part in entry.get("parts", ())] or [value]
        common = entry.get("common", value)
        about = [item for item in result.evidence if names(item.text, person)]
        strict = [item for item in about if all(_holds(item.text, part) for part in parts)]
        loose = [item for item in about if _holds(item.text, common)]
        found.append(
            {
                "fact": fact,
                "value": value,
                "parts": parts,
                "common": common,
                "held": bool(strict),
                "held_by_common_form": bool(loose),
                "by_source": _sources(strict),
                "by_source_common": _sources(loose),
                "refs": [item.ref for item in strict],
            }
        )
    return {
        "facts_not_reported": len(found),
        "useful_leads": sum(1 for entry in found if entry["held"]),
        "useful_leads_web": sum(1 for entry in found if entry["by_source"]["web"]),
        "useful_leads_telegram": sum(1 for entry in found if entry["by_source"]["telegram"]),
        "useful_leads_local": sum(1 for entry in found if entry["by_source"]["local"]),
        "useful_leads_common_form": sum(1 for entry in found if entry["held_by_common_form"]),
        "lead_detail": found,
    }


def _sample(result: InvestigationResult, limit: int = 40) -> list[dict[str, Any]]:
    """What a person would read: every outside item with enough of its text to judge it
    by eye, and what it was kept for."""
    return [
        {
            "ref": item.ref,
            "source": item.source_id,
            "url": item.url,
            "leads": list(item.leads),
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "text": item.text[:400],
        }
        for item in result.evidence[:limit]
    ]


def _web_only(session: Session, case: PersonCase) -> dict[str, str]:
    """The facts of the golden set the database does not hold: not decided there, and not
    written in a publication of that person the run already stores. Everything else is
    not the web's to be credited with."""
    from evaluation.investigation.presence import presence

    held = {
        fact: value
        for fact, value in case.facts.items()
        if presence(session, case.name, fact, value)["state"] == "no"
    }
    return held


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
    # One search object serves the whole run, so its counters are read as a difference:
    # what a case costs is what it added, not what the run had spent when it was asked.
    mark = counted[0].mark() if counted else (0, 0, 0)
    started = time.perf_counter()
    result = asyncio.run(built.investigate(question, max_followups=max_followups))
    elapsed = (time.perf_counter() - started) * 1000
    reported = _reported(result)
    held = {(fact, value) for fact, value in case.facts.items()}
    shown = _shown_urls(result)
    hits = expected & shown
    web_items = [item for item in result.evidence if item.source_id == "web:tavily"]
    # The web is credited with what the database does not hold: a fact already decided
    # there, or a publication of that very person already saying it, is not the web's.
    for_web = _web_only(session, case)
    useful = _useful_leads(
        result,
        {fact: {"value": value} for fact, value in for_web.items()},
        reported,
        person=case.name,
    )
    web_leads = [item for item in web_items if item.leads]
    cost = _cost(counted, mark)
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
        **useful,
        "evidence": len(result.evidence),
        "evidence_by_source": _by_source(result),
        "leads": sum(1 for item in result.evidence if item.leads),
        "web_leads": len(web_leads),
        "web_leads_holding_a_value": sum(
            1
            for item in web_leads
            if any(_holds(item.text, str(entry["value"])) for entry in useful["lead_detail"])
        ),
        "local_shown": sorted(
            address
            for address in (
                canonical(item.url)
                for item in result.evidence
                if item.url
                and item.source_id != "web:tavily"
                and not item.source_id.startswith(TELEGRAM_PREFIX)
            )
            if address
        ),
        "local_expected": sorted(expected),
        "searches": result.usage.search_calls,
        "network_searches": result.usage.network_searches,
        "failed_searches": result.usage.failed_searches,
        "followup_rounds": result.usage.followup_rounds,
        "llm_calls": result.usage.llm_calls,
        "cost_usd": round(result.usage.cost_usd, 6),
        **_cost(counted, mark),
        "elapsed_ms": round(elapsed),
        "warnings": result.warnings,
        "evidence_sample": _sample(result),
        # What the web was asked, and what it brought, in order — what a ceiling on the
        # queries or the results would be read back from.
        "web_batches": cost["batches"],
    }


def measure_unnamed(
    built: InvestigationService,
    counted: Sequence[CountingSearch],
    case: UnnamedCase,
) -> dict[str, Any]:
    """One unnamed figurant: who the run ranks, who it announces, what it cost."""
    question = f"Установи фигуранта публикации {case.article_id}"
    mark = counted[0].mark() if counted else (0, 0, 0)
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
        **_cost(counted, mark),
        "web_batches": _cost(counted, mark)["batches"],
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
    # `есть ли в перечне Росфинмониторинга` is required of every dossier and is decided
    # only for a person who IS on the list — that is most of none of them, so no dossier
    # closes. The second figure is the same count without that fact: what the retrieval
    # answers, with the defect of the subject left aside (it is not this benchmark's to
    # fix).
    without_list = [
        case
        for case in people
        if [m for m in case.get("missing", ()) if "Росфинмониторинга" not in m] == []
    ]
    return {
        "mode": report.get("mode"),
        "cases": len(cases),
        "person_cases": len(people),
        "subject_accuracy": _mean([float(c["subject_correct"]) for c in people]) if people else 0.0,
        "facts_recall_mean": _mean([case.get("facts_recall") for case in people]),
        "facts_precision_mean": _mean([case.get("facts_precision") for case in people]),
        "false_facts": total("false_facts", people),
        "dossiers_closed": len(closed),
        "dossiers_closed_without_rf_list": len(without_list),
        "still_missing_rf_list": sum(
            1 for case in people if any("Росфинмониторинга" in m for m in case.get("missing", ()))
        ),
        "material_recall_mean": _mean([case.get("material_recall") for case in people]),
        "material_found": total("material_found", people),
        "material_expected": total("material_expected", people),
        "facts_not_reported": total("facts_not_reported", people),
        "useful_leads": total("useful_leads", people),
        # A case is a case: three facts of one person are not three people.
        "cases_with_a_web_lead": sum(1 for case in people if (case.get("useful_leads_web") or 0)),
        "cases_with_missing_facts": sum(
            1 for case in people if (case.get("facts_not_reported") or 0)
        ),
        "facts_held_by_common_form_only": total("useful_leads_common_form", people)
        - total("useful_leads", people),
        "useful_leads_web": total("useful_leads_web", people),
        "useful_leads_telegram": total("useful_leads_telegram", people),
        "useful_leads_local": total("useful_leads_local", people),
        "leads": total("leads"),
        "web_leads": total("web_leads"),
        "web_leads_holding_a_value": total("web_leads_holding_a_value", people),
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


def displaced(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """What the web pushed out of the shown evidence: local pages a reader saw without it
    and does not see with it, and how many of those were about the person."""
    left = {
        case["case_id"]: case for case in before.get("cases", []) if case.get("kind") == "person"
    }
    right = {
        case["case_id"]: case for case in after.get("cases", []) if case.get("kind") == "person"
    }
    rows: list[dict[str, Any]] = []
    for case_id, one in left.items():
        two = right.get(case_id)
        if two is None:
            continue
        lost = sorted(set(one.get("local_shown", [])) - set(two.get("local_shown", [])))
        if not lost:
            continue
        expected = set(one.get("local_expected", []))
        rows.append(
            {
                "case_id": case_id,
                "lost": len(lost),
                "lost_about_the_person": len([url for url in lost if url in expected]),
                "urls": lost[:10],
            }
        )
    return {
        "cases_with_loss": len(rows),
        "pages_lost": sum(row["lost"] for row in rows),
        "pages_lost_about_the_person": sum(row["lost_about_the_person"] for row in rows),
        "per_case": rows,
    }


def _cost(counted: Sequence[CountingSearch], mark: tuple[int, int, int]) -> dict[str, Any]:
    """What one case asked of the web and was given, in its own terms."""
    if not counted:
        return {"tavily_requests": 0, "tavily_results": 0, "tavily_credits": 0, "batches": []}
    return counted[0].since(mark)


def batch_texts(
    case: Mapping[str, Any], *, queries: int | None = None, results: int | None = None
) -> list[str]:
    """The texts a run would have shown with a ceiling on the queries it may spend or on
    the results one may bring — read back from what it really was given, not paid for
    twice."""
    batches: list[Mapping[str, Any]] = list(case.get("web_batches") or [])
    if queries is not None:
        batches = batches[:queries]
    kept: list[str] = []
    for batch in batches:
        rows: list[Mapping[str, Any]] = list(batch.get("items") or [])
        kept.extend(
            str(row.get("text", "")) for row in (rows if results is None else rows[:results])
        )
    return kept


def _holds_all(texts: Sequence[str], parts: Sequence[Any]) -> bool:
    joined = " ".join(texts).lower()
    return all(
        any(
            str(option).lower() in joined for option in (part if isinstance(part, list) else [part])
        )
        for part in parts
    )


def ceilings(
    report: Mapping[str, Any], queries: Sequence[int], results: Sequence[int | None]
) -> list[dict[str, Any]]:
    """What a ceiling costs and buys: the same questions and the same answers, fewer
    queries per question or fewer results per query — read back from what was paid for
    once."""
    rows: list[dict[str, Any]] = []
    for cap_queries in queries:
        for cap_results in results:
            useful = credits = asked = 0
            # Both report shapes: the automatic one holds `cases`, the manual one
            # `persons`; a ceiling is read back the same way from either.
            for case in [*report.get("cases", []), *report.get("persons", [])]:
                if case.get("kind", "person") != "person":
                    continue
                batches = list(case.get("web_batches") or [])
                asked += len(batches)
                credits += min(cap_queries, len(batches))
                texts_held = batch_texts(case, queries=cap_queries, results=cap_results)
                # Both report shapes: the automatic one counts `lead_detail`, the manual
                # one counts `facts` — a ceiling is read back the same way from either.
                entries = list(case.get("lead_detail") or case.get("facts") or [])
                for entry in entries:
                    parts = entry.get("parts") or [entry.get("value")]
                    if entry.get("in_db") == "—" or entry.get("kind"):
                        continue
                    if _holds_all(texts_held, list(parts)):
                        useful += 1
            rows.append(
                {
                    "max_queries": cap_queries,
                    "max_results": cap_results if cap_results is not None else 10,
                    "queries_asked": asked,
                    "credits": credits,
                    "useful_leads": useful,
                }
            )
    return rows


def as_markdown(reports: Sequence[Mapping[str, Any]]) -> str:
    rows = [summarise(report) for report in reports]
    header = (
        "| режим | кейсов | recall фактов | precision | ложных фактов | закрытых досье "
        "| recall материала | не сообщено фактов | полезных наводок (веб/тг/лок) | "
        "наводок веб | из них со значением | улик/кейс | поисков/кейс | кругов | Tavily | "
        "кредитов | LLM | USD | мс/кейс |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    lines = [header]
    for row in rows:
        lines.append(
            f"| {row['mode']} | {row['cases']} | {row['facts_recall_mean']} | "
            f"{row['facts_precision_mean']} | {row['false_facts']} | {row['dossiers_closed']} | "
            f"{row['material_recall_mean']} ({row['material_found']}/{row['material_expected']}) | "
            f"{row['facts_not_reported']} | {row['useful_leads']} "
            f"({row['useful_leads_web']}/{row['useful_leads_telegram']}/{row['useful_leads_local']}) | "
            f"{row['web_leads']} | {row['web_leads_holding_a_value']} | "
            f"{row['evidence_mean']} | {row['searches_mean']} | "
            f"{row['followup_rounds_mean']} | {row['tavily_requests']} | {row['tavily_credits']} | "
            f"{row['llm_calls']} | {row['cost_usd']} | {row['elapsed_ms_mean']} |"
        )
    if len(rows) == 2:
        change = delta(reports[0], reports[1])
        pushed = displaced(reports[0], reports[1])
        lines.append("")
        lines.append("### Что изменил web (B − A)")
        lines.append("")
        for key, value in change.items():
            if value:
                lines.append(f"- `{key}`: {value:+}")
        lines.append("")
        lines.append(
            f"### Вытеснено потолком: {pushed['pages_lost']} локальных страниц в "
            f"{pushed['cases_with_loss']} кейсах, из них о человеке — "
            f"{pushed['pages_lost_about_the_person']}"
        )
    return "\n".join(lines)
