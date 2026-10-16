"""A second Jev asked before the junk screen lets an article go: the two must agree.

The decision screen (`decision_screen.DecisionScreen`, «JEV-1») is asked whether an article
reads like a criminal case; under its cutoff the purge wipes the article for good. A real
case it misjudges is lost without a trace. So the cascade (`CascadeScreen`) asks one more
question, only about the articles JEV-1 would let go: «can the monitoring safely throw this
article away before any further check?» (`drop_verifier_prompt`, «JEV-2»). JEV-2 reads the
whole article and the rubric, never what JEV-1 answered.

An article goes only when both agree: JEV-1 under its cutoff and JEV-2 choosing DROP with
P(DROP) at least `MIN_P_DROP`. Everything else is held, as any article JEV-1 holds, and the
hold reader (DeepSeek) reads it next: KEEP, UNCERTAIN, a DROP below `MIN_P_DROP`, and every
failure of JEV-2 — a timeout, an HTTP error, an answer that is not JSON, out of shape, or from
another model. JEV-2 fails open; it never stops the purge and never deletes on its own.

JEV-1 keeps its own promise: it fails closed (`JunkScreenError` stops the purge), and
nothing here changes what it holds. What the purge receives is the screen's contract — a
score per article, held at or above the cutoff: a rescued article scores the cutoff.

Each article is logged (`event=junk_screen_cascade`: both answers and the final decision),
each batch is counted (`event=junk_screen_cascade_batch`), and `CascadeScreen.counts` sums
the batches.

`MIN_P_DROP` was chosen on the development OOF set (1192 articles, 81 relevant), JEV-1 at its
production cutoff: the most articles let go among the policies that lost no relevant one
(`evaluation.jev_cascade`).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial

import httpx

from processing.junk.article_screen import ArticleScreen, JunkScreenError
from processing.junk.decision_screen import (
    DECISION_MODEL,
    DECISIONS_URL,
    RETRIES,
    RETRY_STATUSES,
    _price,
)
from processing.junk.drop_verifier_prompt import (
    DECISION,
    DECISION_OPTIONS,
    DROP,
    PROMPT_VERSION,
    article_text,
    request_for,
)

VERSION = "jev-drop-verifier-v1"
# JEV-2 lets an article go only on DROP at least this sure. Chosen on dev (see above).
MIN_P_DROP = 0.5
# The whole article is read: a long request answers slower than the 1 500 characters of JEV-1.
TIMEOUT_SECONDS = 120.0
CONCURRENCY = 8
TITLE_CHARS = 120
# The decision of JEV-2 that is not an answer: the article is kept.
ERROR = "ERROR"

logger = logging.getLogger("monitoring")


class VerifierAnswerError(Exception):
    """JEV-2 did not answer, or answered so that its decision cannot be read."""


@dataclass(frozen=True)
class Verdict:
    """What JEV-2 said about one article; `ERROR` with the reason when it said nothing."""

    decision: str
    p_drop: float | None = None
    confidence: float | None = None
    error: str | None = None

    @property
    def confirms_drop(self) -> bool:
        return self.decision == DROP and self.p_drop is not None and self.p_drop >= MIN_P_DROP


def _probability(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise VerifierAnswerError(f"{where}: {value!r} is not a probability")
    return float(value)


def verdict_of(answers: object) -> Verdict:
    """The decision out of the `answers` of a reply (or of a cached reading), strictly: a
    choice that is not an option, or probabilities not one per option, is an error. The
    diagnostic answers beside it are not read."""
    decision = answers.get(DECISION) if isinstance(answers, Mapping) else None
    if not isinstance(decision, Mapping):
        raise VerifierAnswerError("the answer has no decision")
    choice = decision.get("choice")
    if choice not in DECISION_OPTIONS:
        raise VerifierAnswerError(f"the choice {choice!r} is not one of {DECISION_OPTIONS}")
    probabilities = decision.get("probabilities")
    if not isinstance(probabilities, Mapping) or set(probabilities) != set(DECISION_OPTIONS):
        raise VerifierAnswerError(f"the probabilities {probabilities!r} are not one per option")
    p_drop = _probability(probabilities[DROP], "P(DROP)")
    for option in DECISION_OPTIONS:
        _probability(probabilities[option], f"P({option})")
    confidence = decision.get("confidence")
    return Verdict(
        decision=str(choice),
        p_drop=p_drop,
        confidence=None if confidence is None else _probability(confidence, "confidence"),
    )


class DropVerifier:
    """JEV-2: asks the decision model about each article, and never raises for one."""

    def __init__(
        self,
        connect: Callable[[], httpx.Client],
        api_key: str,
        *,
        model: str = DECISION_MODEL,
        sleep: float = 2.0,
    ) -> None:
        self._connect = connect
        self._api_key = api_key
        self._model = model
        self._sleep = sleep
        # Asked from threads.
        self._lock = threading.Lock()
        self._cost = 0.0

    @property
    def name(self) -> str:
        return f"{VERSION}:{PROMPT_VERSION}:{self._model}:p_drop>={MIN_P_DROP:.2f}"

    @property
    def cost_usd(self) -> float:
        with self._lock:
            return self._cost

    def verdicts(self, articles: Sequence[tuple[str, str]]) -> list[Verdict]:
        """A verdict per (title, text), in order; a failure is an `ERROR` verdict."""
        if not articles:
            return []
        with self._connect() as http, ThreadPoolExecutor(CONCURRENCY) as pool:
            return list(pool.map(partial(self._verdict, http), articles))

    def _verdict(self, http: httpx.Client, article: tuple[str, str]) -> Verdict:
        title, body = article
        try:
            answer = self._ask(http, request_for(article_text(title, body)))
            return verdict_of(answer.get("answers") if isinstance(answer, Mapping) else None)
        except Exception as exc:  # noqa: BLE001 - any failure of JEV-2 keeps the article: fail open
            return Verdict(decision=ERROR, error=f"{type(exc).__name__}: {exc}")

    def _ask(self, http: httpx.Client, request: Mapping[str, object]) -> object:
        failure = "no attempt was made"
        for attempt in range(RETRIES + 1):
            if attempt:
                time.sleep(self._sleep * attempt)
            try:
                response = http.post(
                    DECISIONS_URL,
                    json={"model": self._model, **request},
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=TIMEOUT_SECONDS,
                )
            except httpx.HTTPError as exc:
                failure = f"{type(exc).__name__}: {exc}"
                continue
            if response.status_code in RETRY_STATUSES:
                failure = f"HTTP {response.status_code}"
                continue
            if response.status_code != httpx.codes.OK:
                raise VerifierAnswerError(f"HTTP {response.status_code}")
            try:
                answer = response.json()
            except ValueError as exc:
                raise VerifierAnswerError("the answer is not JSON") from exc
            answered = answer.get("model") if isinstance(answer, Mapping) else None
            if not isinstance(answered, str) or not (
                answered == self._model or answered.startswith(f"{self._model}-")
            ):
                raise VerifierAnswerError(f"asked {self._model}, answered by {answered!r}")
            with self._lock:
                self._cost += _price(answer)
            return answer
        raise VerifierAnswerError(f"no answer: {failure}")


# The counters of a batch, in the order they are logged.
COUNTS = (
    "total",
    "jev1_keep",
    "jev1_drop",
    "jev2_called",
    "jev2_keep_rescue",
    "jev2_drop_confirmed",
    "jev2_error",
    "final_keep",
    "final_drop",
)


class CascadeScreen:
    """An `ArticleScreen`: JEV-1's score, unless JEV-2 does not confirm its drop."""

    def __init__(self, first: ArticleScreen, verifier: DropVerifier) -> None:
        self._first = first
        self._verifier = verifier
        self._counts: Counter[str] = Counter()

    @property
    def name(self) -> str:
        return f"{self._first.name}+{self._verifier.name}"

    @property
    def cutoff(self) -> float:
        return self._first.cutoff

    @property
    def counts(self) -> dict[str, int]:
        """Every batch so far, summed."""
        return {name: self._counts[name] for name in COUNTS}

    def scores(self, articles: Sequence[tuple[str, str]]) -> list[float]:
        # JEV-1 fails closed: its error stops the purge, before JEV-2 is asked anything.
        first = self._first.scores(articles)
        cutoff = self.cutoff
        dropped = [i for i, score in enumerate(first) if score < cutoff]
        verdicts = dict(
            zip(dropped, self._verifier.verdicts([articles[i] for i in dropped]), strict=True)
        )
        final: list[float] = []
        counts: Counter[str] = Counter()
        for i, ((title, _), score) in enumerate(zip(articles, first, strict=True)):
            verdict = verdicts.get(i)
            keep = verdict is None or not verdict.confirms_drop
            final.append(cutoff if verdict is not None and keep else score)
            counts.update(_counted(verdict, keep))
            _log_article(title, score, cutoff, verdict, keep)
        self._counts.update(counts)
        logger.info(
            "event=junk_screen_cascade_batch %s screen=%s",
            " ".join(f"{name}={counts[name]}" for name in COUNTS),
            self.name,
        )
        return final


def _counted(verdict: Verdict | None, keep: bool) -> list[str]:
    counted = ["total", "final_keep" if keep else "final_drop"]
    if verdict is None:
        return [*counted, "jev1_keep"]
    counted += ["jev1_drop", "jev2_called"]
    counted.append("jev2_keep_rescue" if keep else "jev2_drop_confirmed")
    if verdict.decision == ERROR:
        counted.append("jev2_error")
    return counted


def _log_article(
    title: str, score: float, cutoff: float, verdict: Verdict | None, keep: bool
) -> None:
    if verdict is None:
        jev2 = "jev2_called=no"
    else:
        jev2 = (
            f"jev2_called=yes jev2_decision={verdict.decision} "
            f"jev2_p_drop={_number(verdict.p_drop)} jev2_confidence={_number(verdict.confidence)}"
        )
        if verdict.error is not None:
            jev2 += f" jev2_error={verdict.error[:200]!r}"
    logger.log(
        logging.WARNING if verdict is not None and verdict.decision == ERROR else logging.INFO,
        "event=junk_screen_cascade jev1_score=%.4f cutoff=%.4f %s final=%s title=%r",
        score,
        cutoff,
        jev2,
        "keep" if keep else "drop",
        title[:TITLE_CHARS],
    )


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def _connect() -> httpx.Client:
    return httpx.Client()


def cascade_screen_from_env(first: ArticleScreen, env: Mapping[str, str]) -> CascadeScreen:
    """JEV-1 with JEV-2 behind it, JEV-2 asked once here: a verifier that does not answer at
    the start would hold every article JEV-1 lets go, so it stops the purge instead."""
    api_key = env.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise JunkScreenError("The drop verifier is on, but OPENROUTER_API_KEY is empty")
    verifier = DropVerifier(_connect, api_key)
    [probe] = verifier.verdicts([("Проверка", "Проверка второй модели отсева перед очисткой.")])
    if probe.decision == ERROR:
        raise JunkScreenError(f"The drop verifier did not answer: {probe.error}")
    return CascadeScreen(first, verifier)
