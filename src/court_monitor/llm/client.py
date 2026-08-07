"""OpenAI-compatible chat client, deliberately provider-neutral.

The project talks to whatever endpoint ``CM_LLM_BASE_URL`` points at — DeepSeek
today, a local vLLM/Ollama or another host tomorrow — so nothing here is vendor
specific beyond the wire format, which is the OpenAI chat-completions shape that
every candidate backend speaks. Switching provider is a settings change.

Two failure modes drive the design, both measured against the live API rather
than assumed:

* **HTTP 200 with empty content.** A reasoning model spends most of its token
  budget thinking (60–75% of completion tokens, measured); if ``max_tokens`` is
  too small the entire budget goes to reasoning and the reply comes back empty,
  with no error of any kind. That is why ``MAX_TOKENS`` is generous and an empty
  reply is retried rather than accepted.
* **JSON mode is a hint, not a schema.** The API asks for the word "json" in the
  prompt and does its best; it does not enforce a structure. So the response is
  validated against a Pydantic model here, and a malformed or incomplete reply is
  an ordinary retry rather than an exception the caller has to expect.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from court_monitor.observability import get_logger

_log = get_logger(__name__)

Schema = TypeVar("Schema", bound=BaseModel)

# Generous on purpose — see the module docstring. Below roughly this figure a
# reasoning model can spend the whole budget thinking and return nothing.
MAX_TOKENS = 4000

# Attempts per call. The failures being retried (empty reply, malformed JSON)
# are stochastic, so a couple of retries clears them; more would just spend
# money on an endpoint that is answering but not usefully.
MAX_ATTEMPTS = 3

TIMEOUT_SECONDS = 180.0

# The API rejects JSON mode unless the prompt itself contains "json", so the
# instruction is appended by the client rather than left to each caller.
_JSON_INSTRUCTION = (
    "Ответ верни строго как один json-объект без пояснений вокруг него, "
    "по следующей схеме:\n{schema}"
)


class LlmUnavailable(RuntimeError):
    """The model produced no usable answer — network, auth, or unusable output."""


class LlmClient:
    """Chat-completions client for an OpenAI-compatible endpoint.

    The underlying ``httpx.Client`` is held for the lifetime of the instance
    rather than created per-request, which keeps the TCP connection (and TLS
    session) to the endpoint alive across calls. At ``--limit 20`` with
    MAX_ATTEMPTS=3 that saves ~60 handshakes per ``judge-matches`` run.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport)

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._client.close()

    def complete_json(self, prompt: str, *, schema: type[Schema]) -> Schema:
        """Ask for one JSON object and return it validated against ``schema``.

        Raises :class:`LlmUnavailable` when no attempt produced a usable answer,
        so callers degrade deliberately instead of storing a fabricated result.
        """
        message = f"{prompt}\n\n" + _JSON_INSTRUCTION.format(
            schema=json.dumps(_schema_hint(schema), ensure_ascii=False)
        )
        last_problem = "неизвестная причина"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            content = self._post(message, attempt=attempt)
            if not content.strip():
                # Not an error condition on the wire — the budget went to
                # reasoning. Retrying is what actually helps.
                last_problem = "модель вернула пустой ответ"
                _log.warning("llm.empty_response", attempt=attempt, model=self._model)
                continue
            try:
                return schema.model_validate_json(_strip_code_fence(content))
            except (ValidationError, ValueError) as exc:
                last_problem = f"ответ не соответствует схеме: {type(exc).__name__}"
                _log.warning(
                    "llm.invalid_response", attempt=attempt, model=self._model, error=str(exc)[:200]
                )

        raise LlmUnavailable(
            f"{self._model}: не удалось получить разбираемый ответ за {MAX_ATTEMPTS} попытки "
            f"({last_problem})"
        )

    def _post(self, message: str, *, attempt: int) -> str:
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": message}],
            "max_tokens": MAX_TOKENS,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            # str(exc) on httpx errors carries the request URL but never headers,
            # so the key cannot leak through here.
            raise LlmUnavailable(f"нет связи с моделью: {type(exc).__name__}") from exc

        if response.status_code != 200:
            # Deliberately does not interpolate the request — the Authorization
            # header would end up in logs and operator-facing messages.
            raise LlmUnavailable(
                f"модель ответила HTTP {response.status_code} "
                f"({response.text[:200] if response.text else 'без тела'})"
            )

        return _extract_content(response.json(), attempt=attempt, model=self._model)


def _extract_content(payload: dict[str, Any], *, attempt: int, model: str) -> str:
    choices = payload.get("choices") or []
    if not choices:
        _log.warning("llm.no_choices", attempt=attempt, model=model)
        return ""
    usage = payload.get("usage") or {}
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if reasoning is not None:
        _log.info(
            "llm.usage",
            model=model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            reasoning_tokens=reasoning,
        )
    return (choices[0].get("message") or {}).get("content") or ""


def _strip_code_fence(content: str) -> str:
    """Unwrap ```json fences some backends add despite JSON mode.

    Works line by line rather than by ``rsplit`` on backticks: a ````` inside the
    body (a citation quoting output, say) would otherwise cut the fence at the
    wrong spot.
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    if len(lines) == 1:
        return ""
    body_lines = lines[1:]
    # A closing fence is a line made entirely of backtick characters (3+).
    while body_lines and _is_closing_fence(body_lines[-1]):
        body_lines.pop()
    return "\n".join(body_lines).strip()


def _is_closing_fence(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and stripped.strip("`") == ""


def _schema_hint(schema: type[BaseModel]) -> dict[str, Any]:
    """A compact field→type sketch for the prompt.

    The full JSON Schema is long and mostly noise to a model that is being
    *asked* rather than constrained; the field names and allowed values are the
    part that changes the output.
    """
    hint: dict[str, Any] = {}
    for name, field in schema.model_fields.items():
        annotation = field.annotation
        choices = getattr(annotation, "__members__", None)
        hint[name] = "|".join(choices) if choices else (field.description or "строка")
    return hint


def client_from_settings() -> LlmClient | None:
    """Build a client from ``CM_LLM_*``, or ``None`` when LLM support is off.

    Returning ``None`` rather than raising keeps the caller's shape simple: LLM
    assistance is optional everywhere it appears, so "not configured" and
    "configured" differ by one ``if``, not by an exception path.
    """
    from court_monitor.config.settings import settings  # noqa: PLC0415 - import cycle

    if settings.llm_mode == "disabled":
        return None
    missing = [
        name
        for name, value in (
            ("CM_LLM_BASE_URL", settings.llm_base_url),
            ("CM_LLM_API_KEY", settings.llm_api_key),
            ("CM_LLM_MODEL", settings.llm_model),
        )
        if not value
    ]
    if missing:
        _log.error("llm.misconfigured", mode=settings.llm_mode, missing=missing)
        return None
    return LlmClient(
        base_url=str(settings.llm_base_url),
        api_key=str(settings.llm_api_key),
        model=str(settings.llm_model),
    )
