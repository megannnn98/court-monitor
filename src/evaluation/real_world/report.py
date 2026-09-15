"""JSON and Markdown reports, and the deterministic "Recommended Next Work" ranking.

Ranking: failed hard gates first (by S0 failure count), then missed quality
targets by relative gap to the target, then the most frequent failure kinds.
The text names the measured problem; it never prescribes a technology.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from evaluation.real_world.results import (
    ErrorComponent,
    Failure,
    GateKind,
    GateOutcome,
    GateStatus,
    Metric,
    RealWorldValidationReport,
    RecommendedWork,
    SectionStatus,
)

JSON_NAME = "real_world_validation_v1.json"
MARKDOWN_NAME = "real_world_validation_v1.md"
MAX_RECOMMENDATIONS = 5

# metric -> (component, problem statement, possible work)
_GATE_TOPICS: dict[str, tuple[ErrorComponent, str, str]] = {
    "false_person_auto_link": (
        ErrorComponent.ENTITY_RESOLUTION,
        "different real people are merged into one canonical person",
        "study the linked pairs and what distinguishes them in the data; keep them apart or send them to review",
    ),
    "namesake_different_person_auto_link": (
        ErrorComponent.ENTITY_RESOLUTION,
        "namesakes (same or near-same name, different person) are auto-linked",
        "decide which evidence may separate namesakes and when a namesake must go to review",
    ),
    "false_rf_not_matched": (
        ErrorComponent.ROSFINMONITORING,
        "persons listed in the snapshot are reported as not matched",
        "inspect the missed name variants and make the matcher return a review status instead of absence",
    ),
    "cross_person_persecution_attribution": (
        ErrorComponent.PERSECUTION,
        "political evidence about one person is attributed to another",
        "inspect evidence windows in multi-person sentences and articles",
    ),
    "contradicted_report_claims": (
        ErrorComponent.REPORT,
        "research reports state facts the sources contradict",
        "trace each contradicted claim to the component that produced the wrong fact",
    ),
    "unsupported_rf_absence_claims": (
        ErrorComponent.REPORT,
        "reports word an unconfirmed Rosfinmonitoring status as absence",
        "audit status wording for non-NOT_MATCHED statuses",
    ),
    "dangerous_silent_reinterpretation": (
        ErrorComponent.RESEARCH_INTAKE,
        "ambiguous or unsupported requests are executed without clarification",
        "collect the reinterpreted queries and require clarification for them",
    ),
    "duplicate_monitoring_findings": (
        ErrorComponent.MONITORING,
        "repeated monitoring creates duplicate findings",
        "find which run step recreates findings for unchanged persons",
    ),
    "rerun_duplicates": (
        ErrorComponent.MONITORING,
        "a repeated run without upstream change writes new domain rows",
        "identify the stage whose pending-work selection is not idempotent",
    ),
    "no_snapshot_absence_findings": (
        ErrorComponent.MONITORING,
        "findings appear without a Rosfinmonitoring snapshot",
        "trace the finding criteria that ignore the missing snapshot",
    ),
    "rf_review_status_findings": (
        ErrorComponent.MONITORING,
        "persons with an unresolved RF status reach main findings",
        "check the finding and candidate criteria for review statuses",
    ),
    "db_invariant_violations": (
        ErrorComponent.DATA_QUALITY,
        "the database violates domain invariants after the end-to-end run",
        "reproduce each violated invariant on the smallest article set",
    ),
    "gated_dangerous_failures": (
        ErrorComponent.DATA_QUALITY,
        "false statements about real persons remain after gating (see per-kind counts)",
        "group the dangerous failures by kind and trace each kind to the component that produced it",
    ),
    "person_extraction_precision": (
        ErrorComponent.EXTRACTION,
        "non-person text is extracted as person mentions",
        "classify spurious mentions (titles, organizations, places) by frequency",
    ),
    "person_extraction_recall": (
        ErrorComponent.EXTRACTION,
        "person mentions in real publications are not extracted",
        "classify missed mentions (surname-only, initials, declensions, list items) by frequency",
    ),
    "event_precision": (
        ErrorComponent.EXTRACTION,
        "extracted events do not correspond to annotated events",
        "inspect spurious event sentences by event type",
    ),
    "event_recall": (
        ErrorComponent.EXTRACTION,
        "annotated events are not extracted",
        "inspect missed events by type and sentence structure",
    ),
    "person_event_association_accuracy": (
        ErrorComponent.EVENT_ASSOCIATION,
        "events are linked to the wrong set of persons",
        "inspect association errors in multi-person sentences and shared events",
    ),
    "er_candidate_recall_at_5": (
        ErrorComponent.ENTITY_RESOLUTION,
        "the existing person is not among ER candidates",
        "inspect name forms of the missed candidates",
    ),
    "er_auto_link_precision": (
        ErrorComponent.ENTITY_RESOLUTION,
        "automatic links go to the wrong person",
        "inspect wrong AUTO_LINK decisions and their scores",
    ),
    "political_precision": (
        ErrorComponent.PERSECUTION,
        "non-political persons are classified political",
        "inspect false political classifications and the text windows that triggered them",
    ),
    "political_recall": (
        ErrorComponent.PERSECUTION,
        "politically persecuted persons are not classified political",
        "inspect missed political persons: missing evidence types, review outcomes, unmapped persons",
    ),
    "candidate_precision": (
        ErrorComponent.PERSECUTION,
        "the main candidate query returns persons who are not candidates",
        "trace each false candidate to persecution or RF errors",
    ),
    "candidate_recall": (
        ErrorComponent.PERSECUTION,
        "expected main candidates are missing from the candidate query",
        "trace each missed candidate to extraction, ER, classification or RF",
    ),
    "candidate_with_evidence_rate": (
        ErrorComponent.REPORT,
        "candidates are returned without evidence",
        "find why the evidence of these persons is not linked",
    ),
    "relevant_evidence_rate": (
        ErrorComponent.REPORT,
        "candidate evidence spans are not about the candidate",
        "inspect irrelevant evidence spans by source article",
    ),
    "semantic_recall_at_5": (
        ErrorComponent.RETRIEVAL,
        "semantic queries miss relevant persons/events in the top 5",
        "analyse missed queries: document representation vs query wording",
    ),
    "supported_report_claims_rate": (
        ErrorComponent.REPORT,
        "report claims are not fully supported by annotated evidence",
        "classify unsupported and partial claims by claim type",
    ),
}


def recommended_next_work(
    gates: Sequence[GateOutcome], failures: Sequence[Failure]
) -> list[RecommendedWork]:
    s0_by_component = Counter(f.component for f in failures if f.severity.value == "S0")
    ranked: list[tuple[tuple[float, float, float], GateOutcome]] = []
    for gate in gates:
        if gate.status is not GateStatus.FAIL or gate.kind is GateKind.ADVISORY:
            continue
        topic = _GATE_TOPICS.get(gate.name)
        component = topic[0] if topic else ErrorComponent.DATA_QUALITY
        if gate.kind is GateKind.HARD:
            key = (0.0, -float(gate.value or 0), -float(s0_by_component[component]))
        else:
            value = float(gate.value or 0.0)
            gap = (gate.threshold - value) / gate.threshold if gate.threshold else 0.0
            key = (1.0, -gap, 0.0)
        ranked.append((key, gate))
    ranked.sort(key=lambda item: (item[0], item[1].name))
    work: list[RecommendedWork] = []
    for _, gate in ranked[:MAX_RECOMMENDATIONS]:
        component, problem, possible = _GATE_TOPICS.get(
            gate.name, (ErrorComponent.DATA_QUALITY, gate.name, "investigate")
        )
        kinds = Counter(f.kind for f in failures if f.component is component)
        work.append(
            RecommendedWork(
                priority=len(work) + 1,
                component=component,
                problem=problem,
                metric=gate.name,
                value=gate.value,
                target=f"{gate.comparator} {gate.threshold}",
                evidence=", ".join(f"{kind}={count}" for kind, count in kinds.most_common(3))
                or "no itemized failures",
                possible_work=possible,
            )
        )
    if len(work) < MAX_RECOMMENDATIONS:
        covered = {w.component for w in work}
        by_kind = Counter(
            (f.component, f.kind) for f in failures if f.severity.value in ("S0", "S1", "S2")
        )
        for (component, kind), count in by_kind.most_common():
            if len(work) >= MAX_RECOMMENDATIONS:
                break
            if component in covered:
                continue
            covered.add(component)
            work.append(
                RecommendedWork(
                    priority=len(work) + 1,
                    component=component,
                    problem=f"recurring {kind} failures",
                    metric=f"failures.{kind}",
                    value=count,
                    target="0",
                    evidence=f"{count} itemized failures",
                    possible_work="review the itemized failures in the JSON report and group them by cause",
                )
            )
    return work


def failure_summary(failures: Sequence[Failure]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for failure in failures:
        row = summary.setdefault(failure.component.value, {})
        row[failure.severity.value] = row.get(failure.severity.value, 0) + 1
    return dict(sorted(summary.items()))


# -- rendering --------------------------------------------------------------------------


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") if value != int(value) else f"{value:.1f}"
    return str(value)


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_fmt(cell) for cell in row) + " |" for row in rows]
    return lines


def _metrics(title: str, values: dict[str, Metric]) -> list[str]:
    if not values:
        return [f"{title}: not measured"]
    return [f"{title}: " + ", ".join(f"{k}={_fmt(v)}" for k, v in values.items())]


def render_markdown(report: RealWorldValidationReport) -> str:
    p = report.provenance
    d = report.dataset
    lines: list[str] = ["# Real-World Validation v1", ""]
    lines += ["## Executive Summary", ""]
    lines.append(
        f"**Overall status: {report.overall_status.value}** (exit code {report.exit_code})"
    )
    lines.append("")
    lines += [f"- {reason}" for reason in report.status_reasons]
    lines.append(
        f"- corpus = {d.corpus_articles} real articles; golden draft = {d.golden_draft}; "
        f"golden verified = {d.golden_verified}"
    )
    hard = [g for g in report.safety_gates if g.kind is GateKind.HARD]
    lines.append(
        f"- hard gates: {sum(g.status is GateStatus.PASS for g in hard)} pass, "
        f"{sum(g.status is GateStatus.FAIL for g in hard)} fail, "
        f"{sum(g.status is GateStatus.NOT_RUN for g in hard)} not run"
    )
    lines.append(
        "- DRAFT annotations were written by an AI agent and are not human-verified ground truth."
    )
    lines.append("")

    lines += ["## Dataset", ""]
    lines += _table(
        ["item", "value"],
        [
            ["corpus period", d.corpus_period or "—"],
            ["published range", d.corpus_published_range or "—"],
            ["corpus articles", d.corpus_articles],
            ["by source", ", ".join(f"{k}={v}" for k, v in d.corpus_by_source.items())],
            ["by temporal period", ", ".join(f"{k}={v}" for k, v in d.corpus_by_period.items())],
            ["source status", ", ".join(f"{k}={v}" for k, v in d.source_status.items())],
            ["evaluation sample", d.evaluation_sample],
            [
                "golden articles (draft / verified)",
                f"{d.golden_articles} ({d.golden_draft} / {d.golden_verified})",
            ],
            ["golden persons", d.golden_persons],
            [
                "golden by split (draft/verified)",
                ", ".join(f"{k}={v.draft}/{v.verified}" for k, v in d.golden_by_split.items()),
            ],
            ["evaluated articles / persons", f"{d.evaluated_articles} / {d.evaluated_persons}"],
            ["namesake cases", d.namesake_cases],
            ["retrieval queries", d.retrieval_queries],
            ["research queries", d.research_queries],
            ["corpus manifest hash", p.corpus_manifest_hash or "—"],
            ["golden dataset hash", p.golden_dataset_hash],
        ],
    )
    lines.append("")

    lines += ["## Pipeline", ""]
    lines.append(
        "real sources → discovery → ingestion → extraction → Person/Event → ER v2 → persecution "
        "classification → Rosfinmonitoring matching → semantic indexing → CandidateQuery → "
        "monitoring findings → research workflow → ResearchReport."
    )
    lines.append("")
    lines += _table(["component", "version"], sorted(p.component_versions.items()))
    lines.append("")
    lines += [
        (
            f"git commit `{p.git_commit}`, split `{p.split}` (verified only: {p.verified_only}), "
            f"policy `{p.policy_version}` ({p.policy_hash[:12]}), RF snapshot `{p.rf_snapshot_id}` "
            f"({(p.rf_snapshot_hash or '—')[:12]}), embedding model `{p.embedding_model_id or '—'}`, "
            f"generated {p.timestamp.isoformat()}."
        ),
        "",
    ]

    lines += ["## Metrics", ""]
    lines += _table(
        ["metric", "value", "target", "status"],
        [
            [g.name, g.value, f"{g.comparator} {g.threshold}", g.status.value]
            for g in report.safety_gates
            if g.kind is GateKind.QUALITY
        ],
    )
    lines.append("")

    lines += ["## Safety Gates", ""]
    lines += _table(
        ["gate", "kind", "value", "threshold", "status", "detail"],
        [
            [
                g.name,
                g.kind.value,
                g.value,
                f"{g.comparator} {g.threshold}",
                g.status.value,
                g.detail or "",
            ]
            for g in report.safety_gates
            if g.kind is not GateKind.QUALITY
        ],
    )
    lines.append("")

    x = report.extraction
    lines += ["## Extraction", ""]
    lines += _metrics("Person mentions", x.person_mentions)
    lines += ["", *_metrics("Events", x.events), ""]
    lines += _metrics("Historical events (recall)", x.historical_events)
    lines += [
        "",
        (
            f"Person-event association accuracy: {_fmt(x.person_event_association_accuracy)} "
            f"({x.events_association_correct}/{x.events_matched} matched events; "
            f"{x.cross_person_event_links} links to a wrong golden person)"
        ),
        "",
    ]

    e = report.entity_resolution
    lines += ["## Entity Resolution", ""]
    lines += _table(
        ["metric", "value"],
        [
            ["mentions evaluated", e.mentions_evaluated],
            ["mentions where a link was expected", e.link_expected_mentions],
            ["candidate recall@1", e.candidate_recall_at_1],
            ["candidate recall@5", e.candidate_recall_at_5],
            ["AUTO_LINK (correct / all)", f"{e.correct_auto_links} / {e.auto_links}"],
            ["AUTO_LINK precision", e.auto_link_precision],
            ["AUTO_LINK recall", e.auto_link_recall],
            ["REVIEW (count / rate)", f"{e.reviews} / {_fmt(e.review_rate)}"],
            ["false identity links", e.false_links],
            ["false create-new", e.false_create_new],
            ["duplicate canonical persons", e.duplicate_canonical_persons],
        ],
    )
    n = e.namesake
    lines += [
        "",
        f"Namesake benchmark: {n.status.value}"
        + (f" ({n.not_run_reason})" if n.not_run_reason else ""),
    ]
    if n.status is not SectionStatus.NOT_RUN:
        lines.append(
            f"{n.cases} cases {n.by_category}; different-person AUTO_LINK = {n.different_person_auto_links}; "
            + ", ".join(f"{k}={_fmt(v)}" for k, v in n.decision.items())
        )
    lines.append("")

    s = report.persecution
    lines += ["## Persecution", ""]
    lines.append(
        f"Evaluated persons: {s.evaluated} (unmapped: {s.unmapped_persons}), accuracy {_fmt(s.accuracy)}"
    )
    lines += [
        "",
        *_metrics("POLITICAL", s.political),
        "",
        *_metrics("NON_POLITICAL", s.non_political),
    ]
    lines += [
        "",
        f"UNCERTAIN/NEEDS_REVIEW rate: {_fmt(s.uncertain_or_review_rate)}; cross-person political attribution: {s.cross_person_political_attribution}",
        "",
    ]
    lines += _confusion("expected \\ actual", s.confusion_matrix)

    r = report.rosfinmonitoring
    lines += ["## Rosfinmonitoring", ""]
    lines.append(
        f"Snapshot `{r.snapshot_id}`; evaluated {r.evaluated}; accuracy {_fmt(r.accuracy)}; "
        f"**false NOT_MATCHED = {r.false_not_matched}**; review status accepted instead of expected: {r.review_instead_of_expected}"
    )
    lines.append("")
    lines += _confusion("expected \\ actual", r.confusion_matrix)

    c = report.candidate_query
    lines += ["## Candidate Query", ""]
    lines += _metrics("CandidateQueryService", c.candidates)
    lines += ["", *_metrics("Active monitoring findings", c.findings), ""]
    ev = c.evidence
    lines.append(
        f"Evidence: {ev.candidates_checked} candidates checked, with evidence {_fmt(ev.with_evidence_rate)}, "
        f"relevant {_fmt(ev.relevant_evidence_rate)}, supports classification {_fmt(ev.supports_classification_rate)}, "
        f"traceable {_fmt(ev.traceable_rate)}, offsets valid {_fmt(ev.offset_valid_rate)} ({ev.evidence_spans} spans)"
    )
    lines.append("")

    t = report.retrieval
    lines += ["## Semantic Retrieval", ""]
    lines.append(
        f"Status {t.status.value}"
        + (f" ({t.not_run_reason})" if t.not_run_reason else "")
        + f"; model `{t.embedding_model_id or '—'}`; {t.queries} queries ({t.person_queries} person, {t.event_queries} event)"
    )
    if t.backends:
        lines.append("")
        lines += _table(
            ["backend", "cases", "Recall@5", "Recall@10", "MRR", "nDCG@5"],
            [
                [k, b.cases, b.recall_at_5, b.recall_at_10, b.mrr, b.ndcg_at_5]
                for k, b in t.backends.items()
            ],
        )
    lines.append("")

    if t.query_error_categories:
        lines += [
            "",
            "Query-level causes: "
            + ", ".join(f"{k}={v}" for k, v in t.query_error_categories.items()),
        ]
        lines.append("")
        lines += _table(
            ["query", "type", "lexical", "dense", "hybrid", "cause"],
            [
                [
                    d.query_id,
                    d.entity_type,
                    d.ranks.get("lexical"),
                    d.ranks.get("dense"),
                    d.ranks.get("hybrid"),
                    d.category,
                ]
                for d in t.query_diagnostics
            ],
        )
    lines.append("")

    q = report.research
    lines += ["## Research Reports", ""]
    lines.append(
        f"{q.queries} queries {q.by_class}; workflow completed {q.workflow_completed}, clarification "
        f"{q.workflow_clarification}, failed {q.workflow_failed}"
    )
    lines.append(
        f"Intake: {q.intake_status.value}"
        + (f" ({q.intake_not_run_reason})" if q.intake_not_run_reason else "")
        + f"; correct {q.intake_correct}; clarification {q.intake_clarification_given}/{q.intake_clarification_expected}; "
        f"silent reinterpretation {q.dangerous_silent_reinterpretation}"
    )
    lines += ["", *_metrics("Persons", q.persons)]
    lines.append("")
    lines.append(
        "Claims: "
        + ", ".join(f"{k}={v}" for k, v in q.claims.items())
        + f" ({q.contradicted_claims_unique} distinct contradicted facts)"
        + f"; supported rate {_fmt(q.supported_claims_rate)}; required missing {q.required_claims_missing}; "
        f"forbidden present {q.forbidden_claims_present}"
    )
    lines.append("")
    lines.append("Dangerous claim kinds: " + ", ".join(f"{k}={v}" for k, v in q.dangerous.items()))
    lines.append("")
    lines.append(
        "Claim failure causes (occurrences / distinct facts): "
        + (
            ", ".join(
                f"{k}={v}/{q.claim_failure_categories_unique.get(k, 0)}"
                for k, v in q.claim_failure_categories.items()
            )
            or "none"
        )
    )
    lines.append("")

    m = report.monitoring
    lines += ["## Monitoring E2E", ""]
    lines.append(
        f"Status {m.status.value}" + (f" ({m.not_run_reason})" if m.not_run_reason else "")
    )
    if m.periods:
        lines.append("")
        lines += _table(
            [
                "period",
                "articles",
                "runs",
                "new documents",
                "new persons",
                "new events",
                "new reviews",
                "new classifications",
                "new RF",
                "new findings",
                "semantic indexed",
                "rerun new rows",
                "seconds",
            ],
            [
                [
                    pr.period,
                    pr.articles_published,
                    ", ".join(f"{k}:{v}" for k, v in pr.run_status.items()),
                    pr.new.get("source_documents"),
                    pr.new.get("persons"),
                    pr.new.get("events"),
                    pr.new.get("pending_person_reviews"),
                    pr.new.get("classifications"),
                    pr.new.get("rf_results"),
                    pr.new.get("findings"),
                    pr.new.get("semantic_indexed"),
                    sum(pr.rerun_new.values()) if pr.rerun_new else "not run",
                    pr.duration_seconds,
                ]
                for pr in m.periods
            ],
        )
    lines += [
        "",
        "Rerun duplicates: "
        + (", ".join(f"{k}={v}" for k, v in m.rerun_duplicates.items()) or "—"),
    ]
    lines += [
        "",
        "Finding timing: " + (", ".join(f"{k}={v}" for k, v in m.finding_timing.items()) or "—"),
        "",
    ]
    lines += ["## Failure Injection", ""]
    if m.scenarios:
        lines += _table(
            ["scenario", "status", "detail"],
            [[sc.name, sc.status.value, sc.detail] for sc in m.scenarios],
        )
    else:
        lines.append("Not run.")
    lines.append("")

    perf = report.performance
    lines += ["## Performance", ""]
    lines.append(
        f"Status {perf.status.value}; {perf.articles} articles, {perf.persons} persons in {_fmt(perf.total_seconds)} s "
        f"({_fmt(perf.articles_per_minute)} articles/min, {_fmt(perf.persons_per_minute)} persons/min)"
    )
    if perf.stage_seconds:
        lines.append("")
        lines += _table(["stage", "seconds"], sorted(perf.stage_seconds.items()))
    lines.append("")

    lines += ["## Manual Review Workload", ""]
    lines += (
        _table(["metric", "value"], sorted(m.review_workload.items()))
        if m.review_workload
        else ["Not measured."]
    )
    lines += [
        "",
        "DB invariants: "
        + (", ".join(f"{k}={v}" for k, v in m.db_invariants.items()) or "not checked"),
        "",
    ]

    lines += ["## Known Limitations", ""]
    lines += [f"- {item}" for item in report.known_limitations]
    lines.append("")

    lines += ["## Critical Failures", ""]
    critical = [f for f in report.failures if f.severity.value in ("S0", "S1")]
    lines.append(
        "Failures by component and severity: "
        + "; ".join(f"{k}: {v}" for k, v in report.failure_summary.items())
    )
    lines.append("")
    if critical:
        lines += _table(
            ["severity", "component", "kind", "case", "person", "gated", "detail"],
            [
                [
                    f.severity.value,
                    f.component.value,
                    f.kind,
                    f.case_id or "",
                    f.golden_person_id or "",
                    str(f.gated),
                    f.detail[:160],
                ]
                for f in sorted(
                    critical,
                    key=lambda f: (f.severity.value, f.component.value, f.kind, f.case_id or ""),
                )[:60]
            ],
        )
        if len(critical) > 60:
            lines.append(f"... {len(critical) - 60} more in the JSON report")
    else:
        lines.append("None.")
    lines.append("")

    lines += ["## Recommended Next Work", ""]
    if report.recommended_next_work:
        for item in report.recommended_next_work:
            lines.append(
                f"{item.priority}. **{item.component.value}: {item.problem}** — `{item.metric}` = "
                f"{_fmt(item.value)} (target {item.target}); evidence: {item.evidence}. Possible work: {item.possible_work}."
            )
    else:
        lines.append("No failed gate or recurring failure.")
    lines.append("")
    return "\n".join(lines)


def _confusion(corner: str, matrix: dict[str, dict[str, int]]) -> list[str]:
    if not matrix:
        return ["Not measured.", ""]
    columns = sorted(
        {c for row in matrix.values() for c in row},
        key=lambda c: (
            list(next(iter(matrix.values()))).index(c) if c in next(iter(matrix.values())) else 99
        ),
    )
    rows = [
        [expected, *[row.get(c, 0) for c in columns]]
        for expected, row in matrix.items()
        if any(row.values())
    ]
    return [*_table([corner, *columns], rows), ""]


def write_reports(report: RealWorldValidationReport, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / JSON_NAME
    markdown_path = directory / MARKDOWN_NAME
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
