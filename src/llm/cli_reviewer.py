"""`EntityMatchReviewer` over a local agent CLI (`claude -p`, `qwen -p`, …).

Some deployments have no HTTP LLM endpoint but do have an agent CLI that is already
authenticated. This adapter runs that command once per (mention, candidate) pair, writes
the prompt to its stdin and validates the JSON object it prints. It is the second
implementation of the same domain contract: the policy, the service and the audit do not
change, and the model still only answers.

The command is configuration (`ENTITY_REVIEW_CLI_COMMAND`); nothing here is provider
specific beyond running a process and reading its output.
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess

from pydantic import ValidationError

from llm.entity_reviewer import review_user_message
from persons.resolution.ai_review import (
    ENTITY_REVIEW_PROMPT_VERSION,
    ENTITY_REVIEW_SYSTEM_PROMPT,
    EntityReviewError,
    EntityReviewRequest,
    EntityReviewResult,
)

logger = logging.getLogger("person_resolution")

# A CLI agent has no JSON-schema mode, so the contract is stated in the prompt and
# enforced by the same Pydantic model the HTTP adapter uses.
ANSWER_INSTRUCTION = (
    "Answer with one JSON object and nothing else — no prose, no markdown fence:\n"
    '{"decision": "same_person|different_person|uncertain", "confidence": 0.0, '
    '"supporting_evidence": [], "conflicting_evidence": [], "explanation": ""}'
)

# A failing CLI prints its reason on stderr and nothing on stdout. Dropping that text
# leaves a failure undiagnosable ("exited with 1" and no more), so its tail travels into
# the log and into the error, which the audit stores as the failure reason.
OUTPUT_TAIL_MAX_CHARS = 500


def output_tail(output: str | bytes | None) -> str:
    """The end of a command's output, whitespace collapsed onto one line and bounded."""
    if not output:
        return ""
    text = output.decode(errors="replace") if isinstance(output, bytes) else output
    text = " ".join(text.split())
    if len(text) <= OUTPUT_TAIL_MAX_CHARS:
        return text
    return "…" + text[-OUTPUT_TAIL_MAX_CHARS:]


def parse_cli_answer(output: str) -> EntityReviewResult:
    """The JSON object a CLI printed, with a markdown fence or trailing text tolerated."""
    text = output.strip()
    if text.startswith("```"):
        # ```json … ``` — take what is between the fences.
        parts = text.split("```")
        text = next((part for part in parts if "{" in part), text)
        text = text.removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        # What it printed instead is the whole diagnosis: a refusal, a usage notice, a
        # login prompt all look the same without it.
        printed = output_tail(output)
        raise EntityReviewError(
            f"the reviewer printed no JSON object: {printed}"
            if printed
            else "the reviewer printed nothing",
            transient=False,
        )
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise EntityReviewError(f"the reviewer output is not valid JSON: {exc}", transient=False)
    try:
        return EntityReviewResult.model_validate(data)
    except ValidationError as exc:
        raise EntityReviewError(
            f"the reviewer answer does not match the contract ({exc.error_count()} problems)",
            transient=False,
        ) from None


class CliEntityMatchReviewer:
    def __init__(
        self,
        command: str,
        *,
        model: str,
        provider: str,
        timeout_seconds: float,
        prompt_version: str = ENTITY_REVIEW_PROMPT_VERSION,
    ) -> None:
        self._command = shlex.split(command)
        if not self._command:
            raise ValueError("ENTITY_REVIEW_CLI_COMMAND must not be empty")
        self._model = model
        self._provider = provider
        self._timeout_seconds = timeout_seconds
        self._prompt_version = prompt_version

    @property
    def model(self) -> str:
        return self._model

    def review(self, request: EntityReviewRequest) -> EntityReviewResult:
        prompt = "\n\n".join(
            (ENTITY_REVIEW_SYSTEM_PROMPT, ANSWER_INSTRUCTION, review_user_message(request))
        )
        try:
            completed = subprocess.run(
                self._command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            tail = output_tail(exc.stderr)
            raise EntityReviewError(
                f"the reviewer command timed out after {self._timeout_seconds}s"
                + (f": {tail}" if tail else ""),
                transient=True,
            ) from exc
        except OSError as exc:
            # The command is missing or not executable: configuration, not a hiccup.
            raise EntityReviewError(f"OSError: {exc}", transient=False) from exc
        if completed.returncode != 0:
            # A rate limit or a lost session shows up as a non-zero exit: worth a retry.
            # Which of them it was is only in stderr, so it is logged and kept.
            tail = output_tail(completed.stderr) or output_tail(completed.stdout)
            logger.warning(
                "event=entity_review_cli_failed decision_id=%s candidate_person_id=%s "
                "model=%s returncode=%s stderr=%s",
                request.decision_id,
                request.candidate.person_id,
                self._model,
                completed.returncode,
                tail or "<empty>",
            )
            raise EntityReviewError(
                f"the reviewer command exited with {completed.returncode}"
                + (f": {tail}" if tail else ""),
                transient=True,
            )
        return parse_cli_answer(completed.stdout)
