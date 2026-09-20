"""`CliEntityMatchReviewer`: an agent CLI as the reviewer backend.

The command under test is a small Python script, never a real agent: no network, no
account, no cost.
"""

from __future__ import annotations

import shlex
import sys

import pytest

from llm.cli_reviewer import ANSWER_INSTRUCTION, CliEntityMatchReviewer, parse_cli_answer
from persons.resolution.ai_review import (
    CandidateReviewContext,
    EntityReviewDecision,
    EntityReviewError,
    EntityReviewRequest,
    EvidenceExcerpt,
    MentionReviewContext,
)

ANSWER = (
    '{"decision": "same_person", "confidence": 0.91, "supporting_evidence": ["one case"], '
    '"conflicting_evidence": [], "explanation": "same case number"}'
)


def request(evidence_text: str = "Суд арестовал Ивана Иванова") -> EntityReviewRequest:
    return EntityReviewRequest(
        decision_id=7,
        mention=MentionReviewContext(
            mention_id=3,
            name="Иван Иванов",
            evidence=(EvidenceExcerpt(article_id=1, text=evidence_text),),
        ),
        candidate=CandidateReviewContext(
            person_id=42,
            canonical_name="Иван Иванов",
            matching_key="иванов иван",
            resolution_score=0.8,
            surname_match="exact",
            given_name_match="exact",
            patronymic_match="missing",
        ),
        deterministic_score=0.8,
    )


def script_command(body: str) -> str:
    """A command that behaves like an agent CLI: reads the prompt on stdin, prints."""
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(body)}"


def reviewer(body: str, *, timeout_seconds: float = 30.0) -> CliEntityMatchReviewer:
    return CliEntityMatchReviewer(
        script_command(body),
        model="stub-cli",
        provider="cli",
        timeout_seconds=timeout_seconds,
    )


def test_a_json_answer_on_stdout_becomes_a_result() -> None:
    body = f"import sys; sys.stdin.read(); print({ANSWER!r})"

    result = reviewer(body).review(request())

    assert result.decision is EntityReviewDecision.SAME_PERSON
    assert result.confidence == 0.91


def test_the_prompt_reaches_the_command_on_stdin_with_the_contract() -> None:
    # The command echoes back what it was given, wrapped in a valid answer.
    body = (
        "import json, sys; prompt = sys.stdin.read(); "
        'print(json.dumps({"decision": "uncertain", "confidence": 0.1, '
        '"supporting_evidence": [str(len(prompt))], "conflicting_evidence": [], '
        '"explanation": str("UNTRUSTED DATA" in prompt and "candidate_person" in prompt '
        'and "Иванова" in prompt)}))'
    )

    result = reviewer(body).review(request())

    assert result.explanation == "True"
    assert int(result.supporting_evidence[0]) > len(ANSWER_INSTRUCTION)


@pytest.mark.parametrize(
    "printed",
    [
        f"```json\n{ANSWER}\n```",
        f"Here is my answer:\n{ANSWER}",
        f"{ANSWER}\nHope this helps.",
    ],
)
def test_a_fence_or_chatter_around_the_object_is_tolerated(printed: str) -> None:
    result = parse_cli_answer(printed)

    assert result.decision is EntityReviewDecision.SAME_PERSON


@pytest.mark.parametrize(
    "printed",
    [
        "I cannot answer that.",
        "{not json at all}",
        (
            '{"decision": "maybe", "confidence": 0.9, "supporting_evidence": [], '
            '"conflicting_evidence": [], "explanation": ""}'
        ),
        (
            '{"decision": "same_person", "confidence": 3, "supporting_evidence": [], '
            '"conflicting_evidence": [], "explanation": ""}'
        ),
    ],
)
def test_an_answer_outside_the_contract_is_a_final_failure(printed: str) -> None:
    with pytest.raises(EntityReviewError) as raised:
        parse_cli_answer(printed)

    assert raised.value.transient is False


def test_a_non_zero_exit_is_transient() -> None:
    body = "import sys; sys.stdin.read(); sys.stderr.write('rate limited'); sys.exit(1)"

    with pytest.raises(EntityReviewError) as raised:
        reviewer(body).review(request())

    assert raised.value.transient is True
    assert "exited with 1" in str(raised.value)


def test_a_timeout_is_transient() -> None:
    body = "import sys, time; sys.stdin.read(); time.sleep(5)"

    with pytest.raises(EntityReviewError) as raised:
        reviewer(body, timeout_seconds=0.5).review(request())

    assert raised.value.transient is True
    assert "timed out" in str(raised.value)


def test_a_missing_command_is_a_configuration_failure() -> None:
    missing = CliEntityMatchReviewer(
        "/nonexistent/reviewer-binary", model="stub-cli", provider="cli", timeout_seconds=5.0
    )

    with pytest.raises(EntityReviewError) as raised:
        missing.review(request())

    assert raised.value.transient is False
    assert "OSError" in str(raised.value)


def test_an_empty_command_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="ENTITY_REVIEW_CLI_COMMAND"):
        CliEntityMatchReviewer("   ", model="stub-cli", provider="cli", timeout_seconds=5.0)
