"""LLM judgement on a match candidate, and the ways the call goes wrong.

Every candidate the pipeline produces scores the same, and no source publishes
the birth dates that would separate them (see known-risks). What a model *can*
do is read the document and say whether the person described there is
consistent with the registry record — the work the operator currently does by
eye.

The failure modes tested here are the ones a live probe against the API
actually produced, not hypotheticals: a reasoning model given too small a token
budget returns HTTP 200 with an empty ``content``, and JSON mode is a prompt
hint rather than an enforced schema, so invalid JSON has to be survivable.
"""

from __future__ import annotations

import json

import httpx
import pytest

from court_monitor.llm.client import LlmClient, LlmUnavailable
from court_monitor.llm.disambiguation import (
    Disambiguation,
    LlmDisambiguator,
    Verdict,
)
from court_monitor.storage.orm import (
    ExtractedFact,
    MatchCandidate,
    PersonRecord,
    SourceDocument,
)


def _completion(content: str, *, reasoning_tokens: int = 200) -> dict:
    """The OpenAI-compatible response shape DeepSeek returns."""
    return {
        "model": "deepseek-v4-pro",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 500,
            "completion_tokens": 300,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    }


def _client(handler) -> LlmClient:
    return LlmClient(
        base_url="https://llm.invalid",
        api_key="test-key",
        model="test-model",
        transport=httpx.MockTransport(handler),
    )


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


def test_a_well_formed_judgement_is_parsed():
    payload = {
        "verdict": "contradicts",
        "quote": "губернатор Воробьев",
        "reasoning": "Разные лица.",
    }

    client = _client(lambda _r: httpx.Response(200, json=_completion(json.dumps(payload))))
    result = client.complete_json("prompt", schema=Disambiguation)

    assert result.verdict is Verdict.contradicts
    assert result.quote == "губернатор Воробьев"


def test_an_empty_response_is_retried_not_accepted():
    """Measured against the live API: a reasoning model whose whole token budget
    went to reasoning returns HTTP 200 with content="" and no error at all.
    Accepting that would silently record "no judgement" as a real answer."""
    bodies = [
        _completion(""),
        _completion(json.dumps({"verdict": "consistent", "quote": "q", "reasoning": "r"})),
    ]
    calls: list[int] = []

    def handler(_request):
        calls.append(1)
        return httpx.Response(200, json=bodies[len(calls) - 1])

    result = _client(handler).complete_json("prompt", schema=Disambiguation)

    assert len(calls) == 2, "must retry the empty response"
    assert result.verdict is Verdict.consistent


def test_invalid_json_is_retried():
    """JSON mode is a prompt hint, not an enforced schema — malformed output is
    a normal outcome, not an exception."""
    bodies = [
        _completion("вот json: {broken"),
        _completion(json.dumps({"verdict": "insufficient", "quote": "q", "reasoning": "r"})),
    ]
    calls: list[int] = []

    def handler(_request):
        calls.append(1)
        return httpx.Response(200, json=bodies[len(calls) - 1])

    assert _client(handler).complete_json("prompt", schema=Disambiguation).verdict is (
        Verdict.insufficient
    )
    assert len(calls) == 2


def test_a_response_missing_a_required_field_is_retried():
    """A quote is mandatory: a judgement an operator cannot check against the
    document is worth nothing."""
    bodies = [
        _completion(json.dumps({"verdict": "consistent", "reasoning": "r"})),  # no quote
        _completion(json.dumps({"verdict": "consistent", "quote": "q", "reasoning": "r"})),
    ]
    calls: list[int] = []

    def handler(_request):
        calls.append(1)
        return httpx.Response(200, json=bodies[len(calls) - 1])

    assert _client(handler).complete_json("prompt", schema=Disambiguation).quote == "q"
    assert len(calls) == 2


def test_giving_up_raises_rather_than_inventing_an_answer():
    client = _client(lambda _r: httpx.Response(200, json=_completion("")))

    with pytest.raises(LlmUnavailable) as exc:
        client.complete_json("prompt", schema=Disambiguation)

    assert "пуст" in str(exc.value).lower() or "empty" in str(exc.value).lower()


def test_an_http_error_surfaces_as_unavailable_not_a_raw_httpx_error():
    client = _client(lambda _r: httpx.Response(401, json={"error": "bad key"}))

    with pytest.raises(LlmUnavailable):
        client.complete_json("prompt", schema=Disambiguation)


def test_the_api_key_never_reaches_the_error_message():
    """Errors get logged and shown to operators; a leaked key in a traceback is
    a real disclosure path."""
    client = _client(lambda _r: httpx.Response(500, text="boom"))

    with pytest.raises(LlmUnavailable) as exc:
        client.complete_json("prompt", schema=Disambiguation)

    assert "test-key" not in str(exc.value)


def test_max_tokens_leaves_room_for_reasoning():
    """A reasoning model spends most of the budget thinking — measured at
    60–75% of completion tokens. Too small a budget is the empty-content bug."""
    seen: dict = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=_completion(json.dumps({"verdict": "consistent", "quote": "q", "reasoning": "r"})),
        )

    _client(handler).complete_json("prompt", schema=Disambiguation)

    assert seen["max_tokens"] >= 4000
    assert seen["response_format"] == {"type": "json_object"}


def test_the_prompt_says_json_because_the_api_requires_it():
    """DeepSeek's JSON mode requires the word "json" in the prompt; without it
    the request is rejected."""
    seen: dict = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=_completion(json.dumps({"verdict": "consistent", "quote": "q", "reasoning": "r"})),
        )

    _client(handler).complete_json("Оцени кандидата", schema=Disambiguation)

    assert "json" in seen["messages"][-1]["content"].lower()


# ---------------------------------------------------------------------------
# The disambiguator
# ---------------------------------------------------------------------------


class _StubClient:
    def __init__(self, result=None, error: Exception | None = None):
        self.result, self.error, self.prompts = result, error, []

    def complete_json(self, prompt: str, *, schema):  # noqa: ARG002
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.result


def _candidate(db_session):
    doc = SourceDocument(
        url="https://example.invalid/1",
        source_type="telegram",
        content_hash="h1",
        text="Зампред Совбеза Дмитрий Медведев заявил...",
    )
    db_session.add(doc)
    db_session.flush()
    fact = ExtractedFact(
        document_id=doc.id,
        entity="person",
        field="full_name_original",
        value="Дмитрий Медведев",
        quote="Совбеза Дмитрий Медведев заявил",
        extraction_method="regex:name:two_tokens",
    )
    rec = PersonRecord(
        source="rfm",
        raw_name="МЕДВЕДЕВ ДМИТРИЙ МАКСИМОВИЧ",
        search_name="медведев дмитрий максимович",
        normalized_name="медведев дмитрий максимович",
        birth_date="2003-05-05",
    )
    db_session.add_all([fact, rec])
    db_session.flush()
    cand = MatchCandidate(
        extracted_fact_id=fact.id,
        person_record_id=rec.id,
        score=0.5,
        status="pending",
        name_score=0.5,
        namesakes=16,
        algorithm_version="match-v3",
    )
    db_session.add(cand)
    db_session.flush()
    return cand


def test_the_judgement_is_stored_on_the_candidate(db_session):
    cand = _candidate(db_session)
    stub = _StubClient(
        Disambiguation(
            verdict=Verdict.contradicts,
            quote="Совбеза Дмитрий Медведев",
            reasoning="Разные отчества.",
        )
    )

    LlmDisambiguator(stub).judge(db_session, cand)

    assert cand.llm_verdict == "contradicts"
    assert cand.llm_quote == "Совбеза Дмитрий Медведев"
    assert "отчества" in cand.llm_reasoning


def test_the_prompt_carries_the_document_the_name_and_the_record(db_session):
    cand = _candidate(db_session)
    stub = _StubClient(Disambiguation(verdict=Verdict.insufficient, quote="q", reasoning="r"))

    LlmDisambiguator(stub).judge(db_session, cand)

    prompt = stub.prompts[0]
    assert "Дмитрий Медведев" in prompt
    assert "МЕДВЕДЕВ ДМИТРИЙ МАКСИМОВИЧ" in prompt
    assert "Зампред Совбеза" in prompt, "the document text must be there to judge from"
    assert "16" in prompt, "namesake count is context the model should see"


def test_the_status_is_never_changed_by_the_model(db_session):
    """The whole system is built on nothing being confirmed automatically. A
    judgement is an inferred fact for the operator, not a decision."""
    cand = _candidate(db_session)
    stub = _StubClient(Disambiguation(verdict=Verdict.consistent, quote="q", reasoning="r"))

    LlmDisambiguator(stub).judge(db_session, cand)

    assert cand.status == "pending"
    assert cand.score == 0.5, "score stays a deterministic statement about name/date/place"


def test_an_unavailable_model_leaves_the_candidate_untouched(db_session):
    """Same degradation contract as the NER extractor: an optional component
    being down must not cost the pipeline anything it already had."""
    cand = _candidate(db_session)
    stub = _StubClient(error=LlmUnavailable("нет связи"))

    judged = LlmDisambiguator(stub).judge(db_session, cand)

    assert judged is False
    assert cand.llm_verdict is None
    assert cand.status == "pending"
