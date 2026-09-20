"""CLI for ER v2: dry-run resolution, human review, evaluation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from db.database import create_database_engine, create_session_factory
from db.maintenance import require_disposable_database, truncate_disposable_tables
from persons.persistence import SqlAlchemyPersonPersistence
from persons.resolution.ai_factory import build_entity_review_service
from persons.resolution.ai_policy import EntityReviewSettings
from persons.resolution.candidates import CandidateConfig
from persons.resolution.decision import ResolutionThresholds
from persons.resolution.evaluation import (
    format_er_evaluation,
    load_er_corpus,
    pipeline_identity,
    run_er_evaluation,
)
from persons.resolution.factory import build_person_resolution_engine
from persons.resolution.models import ScoredPersonCandidate
from persons.resolution.redecide import redecide_name_only_reviews
from persons.resolution.review import (
    PersonResolutionReviewService,
    ResolutionReviewAction,
    ResolutionReviewError,
    ResolutionReviewView,
)
from persons.resolution.service import ResolutionPlan

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ER_CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "er_v2_corpus.json"
ER_EVALUATION_COLLECTIONS = ("eval_er_persons_semantic", "eval_er_events_semantic")


def add_person_resolution_arguments(subparsers: Any) -> None:
    resolve = subparsers.add_parser(
        "resolve-person",
        help="Dry-run ER v2 for a name: normalization, candidates, features, decision",
    )
    resolve.add_argument("name", help='Surface form, e.g. "И. И. Иванов"')
    resolve.add_argument("--json", action="store_true", help="Print the plan as JSON")

    reviews = subparsers.add_parser(
        "person-resolution-reviews", help="List, show or apply ER v2 review decisions"
    )
    actions = reviews.add_subparsers(dest="review_command", required=True)
    listing = actions.add_parser("list", help="Pending person resolution reviews")
    listing.add_argument("--limit", type=int, default=50)
    show = actions.add_parser("show", help="Structured comparison of one decision")
    show.add_argument("decision_id", type=int)
    apply = actions.add_parser("apply", help="Apply a reviewer action (writes to the database)")
    apply.add_argument("decision_id", type=int)
    apply.add_argument(
        "--action", required=True, choices=[action.value for action in ResolutionReviewAction]
    )
    apply.add_argument("--person-id", type=int, default=None, help="Target person")
    apply.add_argument(
        "--source-person-id",
        type=int,
        default=None,
        help="merge_persons: person merged away; keep_separate: the different person",
    )
    apply.add_argument("--note", default=None)
    ai = actions.add_parser(
        "ai",
        help="Review pending decisions with the configured AI reviewer and apply what the "
        "policy allows (writes to the database)",
    )
    ai.add_argument("--limit", type=int, default=100, help="Pending decisions to review")

    redecide = actions.add_parser(
        "redecide-name-only",
        help="Re-decide pending name-only reviews under the current rules (dry run by default)",
    )
    redecide.add_argument(
        "--apply", action="store_true", help="Link the accepted reviews (writes to the database)"
    )
    redecide.add_argument("--limit", type=int, default=None)

    evaluate = subparsers.add_parser(
        "evaluate-er",
        help="Evaluate ER v2 on the golden corpus in a disposable database",
    )
    evaluate.add_argument(
        "--database-url",
        default=None,
        help="Disposable database (name ends with _test or _eval); default EVALUATION_DATABASE_URL",
    )
    evaluate.add_argument("--corpus-path", type=Path, default=DEFAULT_ER_CORPUS_PATH)
    evaluate.add_argument("--sweep", action="store_true", help="Sweep decision thresholds")
    evaluate.add_argument(
        "--semantic",
        action="store_true",
        help="Also evaluate the semantic candidate generator (loads the embedding model)",
    )
    evaluate.add_argument("--qdrant-url", default=":memory:")
    evaluate.add_argument("--output-path", type=Path, default=None)


def format_plan(plan: ResolutionPlan) -> str:
    decision = plan.decision
    lines = [
        f"Incoming: {plan.identity.surface_text or plan.identity.name}",
        f"Stored form: {plan.identity.name}   matching_key: {plan.identity.matching_key}",
        f"Normalized: {plan.name.canonical_form}",
        "Readings: "
        + (
            "; ".join(
                ", ".join(f"{part.role.value}={part.text}" for part in variant.components)
                for variant in plan.name.variants
            )
            or "none (unparsed)"
        ),
    ]
    counts = ", ".join(
        f"{source.value} {count}" for source, count in plan.generation.counts.items()
    )
    lines.append(
        f"Candidates: {len(plan.generation.candidates)} ({counts}); "
        f"semantic source: {plan.generation.semantic_source.value}"
    )
    for candidate in decision.candidates:
        lines.extend(_format_candidate(candidate))
    reasons = ", ".join(reason.value for reason in decision.reasons)
    selected = f" → person #{decision.selected_person_id}" if decision.selected_person_id else ""
    margin = "" if decision.decision_margin is None else f", margin {decision.decision_margin:.2f}"
    lines.append(f"Decision: {decision.action.value}{selected} ({reasons}{margin})")
    lines.append("Dry run: nothing was written.")
    return "\n".join(lines)


def _format_candidate(scored: ScoredPersonCandidate) -> list[str]:
    candidate, features = scored.candidate, scored.features
    semantic = (
        "n/a" if candidate.semantic_similarity is None else f"{candidate.semantic_similarity:.2f}"
    )
    return [
        "",
        (
            f"  candidate #{candidate.person_id}: {candidate.canonical_name}"
            f"   resolution_score {scored.resolution_score:.2f}"
            f"   sources {', '.join(source.value for source in candidate.sources)}"
        ),
        f"    compared with: {features.compared_form}"
        + (" (alias)" if features.compared_form_is_alias else ""),
        (
            f"    surname: {features.surname.value}   given name: {features.given_name.value}"
            f"   patronymic: {features.patronymic.value}"
        ),
        (
            f"    order: {'different' if features.order_differs else 'same'}"
            f"   alias: {'yes' if features.exact_alias else 'no'}   semantic: {semantic}"
        ),
        "    conflicts: "
        + (", ".join(conflict.value for conflict in features.conflicts) or "none"),
    ]


def format_review(view: ResolutionReviewView) -> str:
    lines = [
        (
            f"Decision #{view.decision_id} ({view.status}, review #{view.review_id}, "
            f"{view.resolver_version})"
        ),
        f"Incoming: {view.surface_text or view.incoming_name}  (stored as {view.incoming_name})",
        (
            f"Source: {view.source.source_name or '-'} {view.source.url or ''} "
            f"{view.source.title or ''}"
        ).rstrip(),
        f"Reasons: {', '.join(view.reasons)}   semantic source: {view.semantic_source}",
    ]
    for candidate in view.candidates:
        semantic = (
            "n/a"
            if candidate.semantic_similarity is None
            else f"{candidate.semantic_similarity:.2f}"
        )
        lines += [
            "",
            (
                f"  candidate #{candidate.person_id} [{candidate.person_status}]: "
                f"{candidate.canonical_name}   resolution_score {candidate.resolution_score:.2f}"
            ),
            (
                f"    surname: {candidate.surname.match}   given name: {candidate.given_name.match}"
                f"   patronymic: {candidate.patronymic.match}"
            ),
            (
                f"    order: {'different' if candidate.order_differs else 'same'}"
                f"   alias: {'yes' if candidate.exact_alias else 'no'}   semantic: {semantic}"
            ),
            f"    conflicts: {', '.join(candidate.conflicts) or 'none'}",
        ]
    return "\n".join(lines)


def run_evaluate_er(args: argparse.Namespace) -> None:
    database_url = args.database_url or os.environ.get("EVALUATION_DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "evaluate-er needs --database-url or EVALUATION_DATABASE_URL "
            "(a disposable database whose name ends with _test or _eval)"
        )
    engine = create_database_engine(database_url)
    require_disposable_database(engine)
    truncate_disposable_tables(engine)
    session_factory = create_session_factory(engine)
    env_config = CandidateConfig.from_env(os.environ)
    config = CandidateConfig(
        candidate_limit=env_config.candidate_limit,
        semantic_enabled=args.semantic,
        semantic_min_score=env_config.semantic_min_score,
    )
    retriever = None
    index_semantic = None
    if args.semantic:
        from semantic_retrieval.factory import SemanticRetrievalConfig, create_semantic_components
        from semantic_retrieval.models import RetrievalBackend, RetrievalEntityType

        components = create_semantic_components(
            session_factory,
            SemanticRetrievalConfig(
                qdrant_url=args.qdrant_url,
                person_collection=ER_EVALUATION_COLLECTIONS[0],
                event_collection=ER_EVALUATION_COLLECTIONS[1],
            ),
            with_reranker=False,
        )
        retriever = components.retriever(RetrievalBackend.DENSE)

        def index_semantic() -> None:
            components.indexer().rebuild(RetrievalEntityType.PERSON)

    run = run_er_evaluation(
        session_factory,
        load_er_corpus(args.corpus_path),
        thresholds=ResolutionThresholds.from_env(os.environ),
        config=config,
        semantic_retriever=retriever,
        index_semantic=index_semantic,
        sweep=args.sweep,
    )
    print(format_er_evaluation(run))
    if args.output_path is not None:
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        args.output_path.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _run_ai_review(
    session_factory: sessionmaker[Session],
    settings: EntityReviewSettings | None,
    *,
    limit: int,
) -> str:
    settings = settings or EntityReviewSettings.from_env(os.environ)
    service = build_entity_review_service(session_factory, settings)
    if service is None:
        raise SystemExit(
            "AI review is not configured: set ENTITY_REVIEW_PROVIDER=together "
            "(and the Together AI variables)"
        )
    result = service.review_pending(limit=limit)
    return "\n".join(
        f"{name}: {value}"
        for name, value in (
            ("reviewed", result.reviewed),
            ("auto_accepted", result.auto_accepted),
            ("auto_rejected", result.auto_rejected),
            ("human_required", result.human_required),
            ("failed", result.failed),
            ("skipped", result.skipped),
        )
    )


def run_person_resolution_command(
    args: argparse.Namespace,
    session_factory: sessionmaker[Session],
    *,
    entity_review: EntityReviewSettings | None = None,
) -> bool:
    """Handle an ER v2 command; False when `args.command` is not one."""
    if args.command == "resolve-person":
        engine = build_person_resolution_engine(session_factory)
        with session_factory() as session:
            plan = engine.plan(pipeline_identity(args.name), session)
            session.rollback()
        if args.json:
            print(
                json.dumps(
                    {
                        "identity": plan.identity.model_dump(mode="json"),
                        "normalized": plan.name.model_dump(mode="json"),
                        "decision": plan.decision.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(format_plan(plan))
        return True

    if args.command == "person-resolution-reviews":
        reviews = PersonResolutionReviewService(SqlAlchemyPersonPersistence(session_factory))
        try:
            if args.review_command == "list":
                with session_factory() as session:
                    for view in reviews.list_pending(session, limit=args.limit):
                        print(
                            f"#{view.decision_id}  {view.incoming_name}  "
                            f"candidates {len(view.candidates)}  {', '.join(view.reasons)}"
                        )
            elif args.review_command == "show":
                with session_factory() as session:
                    print(format_review(reviews.get(session, args.decision_id)))
            elif args.review_command == "ai":
                print(_run_ai_review(session_factory, entity_review, limit=args.limit))
            elif args.review_command == "redecide-name-only":
                summary = redecide_name_only_reviews(
                    session_factory, apply=args.apply, limit=args.limit, reviews=reviews
                )
                mode = "linked" if args.apply else "dry run, would link"
                print(
                    f"Checked {summary.checked} name-only reviews: "
                    f"{summary.linkable} accepted by the current rules; "
                    f"{mode} {summary.linked if args.apply else summary.linkable}."
                )
            else:
                with session_factory.begin() as session:
                    result = reviews.apply(
                        session,
                        args.decision_id,
                        ResolutionReviewAction(args.action),
                        person_id=args.person_id,
                        source_person_id=args.source_person_id,
                        note=args.note,
                    )
                print(result.model_dump_json(indent=2))
        except ResolutionReviewError as exc:
            raise SystemExit(f"{type(exc).__name__}: {exc}") from None
        return True

    return False
