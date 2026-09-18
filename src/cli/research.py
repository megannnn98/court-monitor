"""Structured research and natural-language `ask`; arguments live in `research.cli`."""

from __future__ import annotations

import argparse
import logging

from candidates.service import CandidateQueryService
from cli.context import CliContext
from cli.external import register_group
from research.cli import (
    ResearchCliError,
    add_ask_arguments,
    add_research_arguments,
    ask_exit_code,
    build_research_request,
    format_query_result,
    format_research_plan,
    format_research_response,
    format_structured_request,
)
from research.repository import SqlAlchemyPersonResearchRepository
from research.service import ResearchService, ResearchSnapshotNotFoundError
from research.workflow.graph import run_research_query
from research.workflow.llm import LlmConfigurationError
from research.workflow_factory import create_research_graph
from semantic_retrieval.models import SemanticConfigurationError


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    register_group(subparsers, add_research_arguments, run_research)
    register_group(subparsers, add_ask_arguments, run_ask)


def run_research(args: argparse.Namespace, context: CliContext) -> None:
    session_factory = context.session_factory
    try:
        research_request = build_research_request(args)
    except ResearchCliError as exc:
        raise SystemExit(str(exc)) from None
    research_service = ResearchService(
        repository=SqlAlchemyPersonResearchRepository(session_factory),
        candidate_query=CandidateQueryService(session_factory),
    )
    try:
        research_response = research_service.execute(research_request)
    except ResearchSnapshotNotFoundError as exc:
        raise SystemExit(str(exc)) from None
    if args.json:
        print(research_response.model_dump_json(indent=2))
    else:
        print(format_research_response(research_response))


def run_ask(args: argparse.Namespace, context: CliContext) -> None:
    session_factory = context.session_factory
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        research_graph = create_research_graph(session_factory)
    except (LlmConfigurationError, SemanticConfigurationError) as exc:
        raise SystemExit(str(exc)) from None
    query_result = run_research_query(research_graph, args.query)
    if args.show_request:
        print(format_structured_request(query_result))
    if args.show_plan:
        print(format_research_plan(query_result))
    if args.json:
        print(query_result.model_dump_json(indent=2))
    else:
        print(format_query_result(query_result, raw=args.raw))
    exit_code = ask_exit_code(query_result)
    if exit_code:
        raise SystemExit(exit_code)
