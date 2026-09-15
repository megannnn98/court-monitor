"""Central safety gates and overall status for real-world validation.

Hard gates count false statements about real persons and state corruption;
their threshold is zero. A dangerous error is excluded from a hard gate only
when its case lists exactly that kind in `known_limitation_kinds` (the error
is still reported). A NOT_RUN gate is never a PASS.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from evaluation.real_world.golden import DangerousKind
from evaluation.real_world.policy import AT_MOST_TARGETS, EvaluationPolicy
from evaluation.real_world.results import (
    CandidateSection,
    EntityResolutionSection,
    ExtractionSection,
    Failure,
    GateKind,
    GateOutcome,
    GateStatus,
    Metric,
    MonitoringSection,
    OverallStatus,
    PersecutionSection,
    ResearchSection,
    RetrievalSection,
    ScenarioResult,
    SectionStatus,
)

# Every gated failure with a dangerous kind is a false statement about a real
# person: this gate is enforced even when a policy file omits it.
DANGEROUS_FAILURES_GATE = "gated_dangerous_failures"

EXIT_OK = 0
EXIT_GATES_FAILED = 1
EXIT_INFRASTRUCTURE_ERROR = 2


def gated_count(
    failures: Sequence[Failure], *, kind: str | None = None, dangerous: DangerousKind | None = None
) -> int:
    return sum(
        1
        for failure in failures
        if failure.gated
        and (kind is None or failure.kind == kind)
        and (dangerous is None or failure.dangerous_kind is dangerous)
    )


@dataclass(frozen=True)
class GateInputs:
    extraction: ExtractionSection
    entity_resolution: EntityResolutionSection
    persecution: PersecutionSection
    candidate_query: CandidateSection
    retrieval: RetrievalSection
    research: ResearchSection
    monitoring: MonitoringSection
    failures: Sequence[Failure]
    review_workload_per_100: float | None


class RealWorldSafetyGateEvaluator:
    def __init__(self, policy: EvaluationPolicy) -> None:
        self._policy = policy

    def evaluate(self, inputs: GateInputs) -> list[GateOutcome]:
        return [*self._hard(inputs), *self._quality(inputs), *self._advisory(inputs)]

    # -- hard gates ------------------------------------------------------------------------

    def _hard_values(self, inputs: GateInputs) -> dict[str, tuple[Metric, str | None]]:
        failures = inputs.failures
        er = inputs.entity_resolution
        research = inputs.research
        monitoring = inputs.monitoring
        scenarios = {scenario.name: scenario for scenario in monitoring.scenarios}

        def section_value(
            status: SectionStatus, value: Metric, reason: str
        ) -> tuple[Metric, str | None]:
            return (value, None) if status is not SectionStatus.NOT_RUN else (None, reason)

        def scenario_value(name: str, metric: str) -> tuple[Metric, str | None]:
            scenario: ScenarioResult | None = scenarios.get(name)
            if scenario is None or scenario.status is GateStatus.NOT_RUN:
                return None, f"scenario {name} not run"
            value = scenario.metrics.get(metric)
            return (value if isinstance(value, int) else None), scenario.detail

        monitoring_run = monitoring.status is not SectionStatus.NOT_RUN
        return {
            "false_person_auto_link": section_value(
                er.status,
                gated_count(failures, dangerous=DangerousKind.FALSE_PERSON_LINK),
                "entity resolution not evaluated",
            ),
            "namesake_different_person_auto_link": section_value(
                er.namesake.status,
                er.namesake.different_person_auto_links,
                er.namesake.not_run_reason or "namesake benchmark not run",
            ),
            # Component and report failures are both itemized: count each once.
            "false_rf_not_matched": (
                gated_count(failures, dangerous=DangerousKind.FALSE_RF_NOT_MATCHED),
                None,
            ),
            "cross_person_persecution_attribution": section_value(
                inputs.persecution.status,
                gated_count(failures, kind="cross_person_political_attribution"),
                "persecution not evaluated",
            ),
            "contradicted_report_claims": section_value(
                research.status, research.contradicted_claims, "research benchmark not run"
            ),
            "unsupported_rf_absence_claims": section_value(
                research.status,
                research.unsupported_rf_absence_claims,
                "research benchmark not run",
            ),
            "dangerous_silent_reinterpretation": section_value(
                research.intake_status,
                research.dangerous_silent_reinterpretation,
                research.intake_not_run_reason or "intake not run",
            ),
            "duplicate_monitoring_findings": (
                (
                    monitoring.rerun_duplicates.get("findings", 0)
                    + monitoring.rerun_duplicates.get("logical_findings", 0)
                    + monitoring.db_invariants.get("duplicate_active_findings", 0),
                    None,
                )
                if monitoring_run
                else (None, monitoring.not_run_reason or "monitoring not run")
            ),
            "rerun_duplicates": (
                (sum(monitoring.rerun_duplicates.values()), None)
                if monitoring_run
                and monitoring.periods
                and any(p.rerun_new for p in monitoring.periods)
                else (None, "repeated runs not executed")
            ),
            "no_snapshot_absence_findings": scenario_value("no_rf_snapshot", "findings"),
            "rf_review_status_findings": scenario_value(
                "rf_review_statuses_not_findings", "in_findings_or_candidates"
            ),
            "db_invariant_violations": (
                (sum(monitoring.db_invariants.values()), None)
                if monitoring.db_invariants
                else (None, "invariants not checked")
            ),
            DANGEROUS_FAILURES_GATE: (
                sum(1 for f in failures if f.gated and f.dangerous_kind is not None),
                ", ".join(
                    f"{kind}={count}"
                    for kind, count in sorted(
                        Counter(
                            f.dangerous_kind.value
                            for f in failures
                            if f.gated and f.dangerous_kind is not None
                        ).items()
                    )
                )
                or None,
            ),
        }

    def _hard(self, inputs: GateInputs) -> list[GateOutcome]:
        values = self._hard_values(inputs)
        outcomes = []
        gates = {**self._policy.hard_gates}
        gates.setdefault(DANGEROUS_FAILURES_GATE, 0)
        for name, maximum in gates.items():
            value, detail = values.get(name, (None, "no measurement for this gate"))
            if value is None:
                status = GateStatus.NOT_RUN
            else:
                status = GateStatus.PASS if float(value) <= maximum else GateStatus.FAIL
            outcomes.append(
                GateOutcome(
                    name=name,
                    kind=GateKind.HARD,
                    status=status,
                    value=value,
                    threshold=maximum,
                    comparator="<=",
                    detail=detail if status is not GateStatus.PASS else None,
                )
            )
        return outcomes

    # -- quality targets -------------------------------------------------------------------

    @staticmethod
    def quality_values(inputs: GateInputs) -> dict[str, Metric]:
        extraction = inputs.extraction
        er = inputs.entity_resolution
        persecution = inputs.persecution
        candidates = inputs.candidate_query
        return {
            "person_extraction_precision": extraction.person_mentions.get("precision"),
            "person_extraction_recall": extraction.person_mentions.get("recall"),
            "event_precision": extraction.events.get("precision"),
            "event_recall": extraction.events.get("recall"),
            "person_event_association_accuracy": extraction.person_event_association_accuracy,
            "er_candidate_recall_at_5": er.candidate_recall_at_5,
            "er_auto_link_precision": er.auto_link_precision,
            "political_precision": persecution.political.get("precision"),
            "political_recall": persecution.political.get("recall"),
            "candidate_precision": candidates.candidates.get("precision"),
            "candidate_recall": candidates.candidates.get("recall"),
            "candidate_with_evidence_rate": candidates.evidence.with_evidence_rate,
            "relevant_evidence_rate": candidates.evidence.relevant_evidence_rate,
            "semantic_recall_at_5": inputs.retrieval.semantic_recall_at_5,
            "supported_report_claims_rate": inputs.research.supported_claims_rate,
        }

    def _quality(self, inputs: GateInputs) -> list[GateOutcome]:
        values = self.quality_values(inputs)
        outcomes = []
        for name, target in self._policy.quality_targets.items():
            value = values.get(name)
            status = (
                GateStatus.NOT_RUN
                if value is None
                else GateStatus.PASS
                if float(value) >= target
                else GateStatus.FAIL
            )
            outcomes.append(
                GateOutcome(
                    name=name,
                    kind=GateKind.QUALITY,
                    status=status,
                    value=value,
                    threshold=target,
                    comparator=">=",
                    detail="not measured (no data or component not run)" if value is None else None,
                )
            )
        return outcomes

    def _advisory(self, inputs: GateInputs) -> list[GateOutcome]:
        outcomes = []
        for name, target in self._policy.advisory_targets.items():
            value = (
                inputs.review_workload_per_100
                if name == "max_manual_reviews_per_100_articles"
                else None
            )
            if value is None:
                status = GateStatus.NOT_RUN
            elif name in AT_MOST_TARGETS:
                status = GateStatus.PASS if value <= target else GateStatus.FAIL
            else:
                status = GateStatus.PASS if value >= target else GateStatus.FAIL
            outcomes.append(
                GateOutcome(
                    name=name,
                    kind=GateKind.ADVISORY,
                    status=status,
                    value=value,
                    threshold=target,
                    comparator="<=" if name in AT_MOST_TARGETS else ">=",
                    detail="advisory: never fails the evaluation",
                )
            )
        return outcomes


def overall_status(
    gates: Sequence[GateOutcome],
    *,
    verified_articles: int,
    draft_articles_evaluated: int,
    min_verified_articles: int,
) -> tuple[OverallStatus, int, list[str]]:
    reasons: list[str] = []
    hard_failed = [g.name for g in gates if g.kind is GateKind.HARD and g.status is GateStatus.FAIL]
    quality_failed = [
        g.name for g in gates if g.kind is GateKind.QUALITY and g.status is GateStatus.FAIL
    ]
    not_run = [
        g.name for g in gates if g.kind is not GateKind.ADVISORY and g.status is GateStatus.NOT_RUN
    ]
    exit_code = EXIT_GATES_FAILED if hard_failed or quality_failed else EXIT_OK
    if hard_failed:
        reasons.append(f"hard safety gates failed: {', '.join(hard_failed)}")
    if quality_failed:
        reasons.append(f"quality targets missed: {', '.join(quality_failed)}")
    if not_run:
        reasons.append(f"not run: {', '.join(not_run)}")
    preliminary = verified_articles < min_verified_articles or draft_articles_evaluated > 0
    if preliminary:
        reasons.append(
            f"PRELIMINARY: {verified_articles} VERIFIED golden articles (< {min_verified_articles} "
            f"required) and {draft_articles_evaluated} DRAFT articles evaluated; no production "
            "quality claim can be made"
        )
    if hard_failed:
        return OverallStatus.FAILED_GATES, exit_code, reasons
    if preliminary:
        return OverallStatus.PRELIMINARY, exit_code, reasons
    if quality_failed or not_run:
        return OverallStatus.FAILED_GATES, exit_code, reasons
    return OverallStatus.PASSED, exit_code, reasons


def workload_total(values: Mapping[str, Metric]) -> float | None:
    value = values.get("total_reviews_per_100_articles")
    return float(value) if isinstance(value, int | float) else None
