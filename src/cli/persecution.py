"""Persecution classification of persons."""

from __future__ import annotations

import argparse

from cli.context import CliContext
from cli_progress import ProgressBar
from persecution.classification_service import PersecutionClassificationService
from persons.persistence import SqlAlchemyPersonPersistence


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    classify_persecution_parser = subparsers.add_parser(
        "classify-persecution",
        help="Classify persons for political persecution",
    )
    classify_persecution_parser.add_argument(
        "--person-id",
        type=int,
        default=None,
        help="Classify a specific person",
    )
    classify_persecution_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of persons to process",
    )
    classify_persecution_parser.set_defaults(handler=run_classify_persecution)


def run_classify_persecution(args: argparse.Namespace, context: CliContext) -> None:
    session_factory = context.session_factory
    person_persistence = SqlAlchemyPersonPersistence(session_factory)
    classification_service = PersecutionClassificationService(session_factory)

    if args.person_id is not None:
        person = person_persistence.get_person(args.person_id)
        if person is None:
            raise SystemExit(f"Person not found: {args.person_id}")

        classification = classification_service.classify_person(person.id)
        print(f"Classified person {person.id}: {classification.status}")
        print(f"Confidence: {classification.confidence:.2f}")
        if classification.reasons:
            print("Reasons:")
            for reason in classification.reasons:
                print(f"  - {reason}")
    else:
        persons = person_persistence.list_active_persons(limit=args.limit)
        classified_count = 0
        political_count = 0

        with ProgressBar("classify-persecution", len(persons)) as progress:
            for person in persons:
                classification = classification_service.classify_person(person.id)
                classified_count += 1
                if classification.status == "political":
                    political_count += 1
                progress.advance()

        print(f"Classified {classified_count} persons: {political_count} political persecution")
