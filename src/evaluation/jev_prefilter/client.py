"""Asking Jev through OpenRouter decisions, reading the answer strictly, caching it.

Only `MODEL` (or a dated snapshot of it) answers: a reply from any other model is an error,
and nothing falls back to another model. An answer that is not a complete set of the four
choices, each with a probability per option, is an error too — never a guessed choice.
What happens to an article whose answer is an error is the policy's business: it passes.

Every readable answer is cached under `cache_key` (model, prompt version, rubric version,
title, exact text sent), so a rerun asks nothing it has asked before.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from processing.junk.drop_verifier_prompt import (
    PROMPT_VERSION,
    QUESTION_OPTIONS,
    RUBRIC_VERSION,
    article_text,
    request_for,
)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
TIMEOUT_SECONDS = 120.0
RETRIES = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class JevAnswerError(RuntimeError):
    """Jev did not answer, answered with another model, or answered out of shape."""


class Reading(BaseModel):
    """One question's answer: the choice and a probability for every option."""

    model_config = ConfigDict(frozen=True)

    choice: str
    probabilities: dict[str, float]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


def cache_key(
    model: str,
    title: str,
    text: str,
    *,
    prompt_version: str = PROMPT_VERSION,
    rubric_version: str = RUBRIC_VERSION,
) -> str:
    key = json.dumps([model, prompt_version, rubric_version, title, text], ensure_ascii=False)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _probability(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise JevAnswerError(f"{where}: probability {value!r} is not a number")
    if not 0.0 <= value <= 1.0:
        raise JevAnswerError(f"{where}: probability {value!r} is outside 0..1")
    return float(value)


def _reading(raw: object, options: tuple[str, ...], where: str) -> Reading:
    if not isinstance(raw, Mapping):
        raise JevAnswerError(f"{where}: not a choice answer: {raw!r}")
    choice = raw.get("choice")
    if choice not in options:
        raise JevAnswerError(f"{where}: choice {choice!r} is not one of {options}")
    probabilities = raw.get("probabilities")
    if not isinstance(probabilities, Mapping) or set(probabilities) != set(options):
        raise JevAnswerError(f"{where}: probabilities {probabilities!r} are not one per option")
    confidence = raw.get("confidence")
    return Reading(
        choice=str(choice),
        probabilities={o: _probability(probabilities[o], f"{where}.{o}") for o in options},
        confidence=None if confidence is None else _probability(confidence, f"{where}.conf"),
    )


def parse_answer(answer: object) -> dict[str, Reading]:
    """Every question's reading; exactly the questions asked, nothing else."""
    answers = answer.get("answers") if isinstance(answer, Mapping) else None
    if not isinstance(answers, Mapping):
        raise JevAnswerError("the answer has no answers")
    if set(answers) != set(QUESTION_OPTIONS):
        raise JevAnswerError(f"answered {sorted(answers)}, asked {sorted(QUESTION_OPTIONS)}")
    return {
        name: _reading(answers[name], options, name) for name, options in QUESTION_OPTIONS.items()
    }


class AnswerCache:
    """Append-only JSONL of answered articles, keyed by `cache_key`."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    self._records[record["cache_key"]] = record

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._records.get(key)

    def put(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as sink:
                sink.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._records[record["cache_key"]] = record


class Jev:
    """Asks the decisions endpoint; counts calls and cost; answers come only from MODEL."""

    def __init__(
        self,
        connect: Callable[[], httpx.Client],
        api_key: str,
        *,
        model: str = MODEL,
        sleep: float = 3.0,
    ) -> None:
        self._connect = connect
        self._api_key = api_key
        self.model = model
        self._sleep = sleep
        self._lock = threading.Lock()
        self.calls = 0
        self.cost_usd = 0.0

    def ask(self, request: Mapping[str, Any]) -> dict[str, Any]:
        body = {"model": self.model, **request}
        failure = "no attempt was made"
        with self._connect() as http:
            for attempt in range(RETRIES + 1):
                if attempt:
                    time.sleep(self._sleep * attempt)
                try:
                    response = http.post(
                        DECISIONS_URL,
                        json=body,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        timeout=TIMEOUT_SECONDS,
                    )
                except httpx.HTTPError as exc:
                    failure = f"{type(exc).__name__}: {exc}"
                    continue
                with self._lock:
                    self.calls += 1
                if response.status_code in RETRY_STATUSES:
                    failure = f"HTTP {response.status_code}: {response.text[:300]}"
                    continue
                if response.status_code != httpx.codes.OK:
                    raise JevAnswerError(f"HTTP {response.status_code}: {response.text[:500]}")
                try:
                    answer: dict[str, Any] = response.json()
                except ValueError as exc:
                    raise JevAnswerError("Jev's answer is not JSON") from exc
                if not isinstance(answer, dict):
                    raise JevAnswerError(f"Jev's answer is not an object: {answer!r:.200}")
                answered_by = answer.get("model")
                # A dated snapshot of the model («…-20260917») is the model; nothing else is.
                if not isinstance(answered_by, str) or not (
                    answered_by == self.model or answered_by.startswith(f"{self.model}-")
                ):
                    raise JevAnswerError(f"asked {self.model}, answered by {answered_by!r}")
                with self._lock:
                    self.cost_usd += float((answer.get("usage") or {}).get("cost") or 0.0)
                return answer
        raise JevAnswerError(f"Jev did not answer: {failure}")


def classify(
    article_id: int, title: str, text: str, jev: Jev, cache: AnswerCache
) -> tuple[dict[str, Any], bool]:
    """The cached record of one article, and whether it came from cache."""
    sent = article_text(title, text)
    key = cache_key(jev.model, title, sent)
    cached = cache.get(key)
    if cached is not None:
        return cached, True
    answer = jev.ask(request_for(sent))
    readings = parse_answer(answer)
    usage = answer.get("usage") or {}
    record: dict[str, Any] = {
        "article_id": article_id,
        "cache_key": key,
        "prompt_version": PROMPT_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "requested_model": jev.model,
        "model_id": answer.get("model"),
        "generation_id": answer.get("id"),
        "usage": usage,
        "cost": usage.get("cost"),
        "timestamp": datetime.now(UTC).isoformat(),
        "readings": {name: r.model_dump() for name, r in readings.items()},
    }
    cache.put(record)
    return record, False
