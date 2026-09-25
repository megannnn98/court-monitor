"""The Claude name normalizer over a fake client: what is sent and what is kept."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from entities.normalizer import (
    ClaudeNameNormalizer,
    NameItem,
    NameNormalizerError,
    NormalizedBatch,
    NormalizedName,
    OpenRouterNameNormalizer,
    matched_answers,
    name_normalizer_from_env,
)


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def _normalizer(response: Any) -> tuple[ClaudeNameNormalizer, FakeMessages]:
    messages = FakeMessages(response)
    return ClaudeNameNormalizer(SimpleNamespace(messages=messages), model="m"), messages  # type: ignore[arg-type]


def _response(names: list[NormalizedName], stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=NormalizedBatch(names=names),
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        _request_id="req_1",
    )


ITEMS = [NameItem(0, ("Александра Моора", "Моору"), "Суд арестовал Александра Моора.")]


def test_forms_and_quote_are_sent_and_answers_for_unknown_ids_dropped() -> None:
    normalizer, messages = _normalizer(
        _response(
            [
                NormalizedName(
                    id=0,
                    source="Александра Моора",
                    nominative="Александр Моор",
                    gender="male",
                    is_person=True,
                ),
                NormalizedName(
                    id=7, source="x", nominative="Лишний", gender="unknown", is_person=True
                ),
            ]
        )
    )

    answers = normalizer.normalize(ITEMS)

    assert list(answers) == [0] and answers[0].nominative == "Александр Моор"
    [call] = messages.calls
    assert call["model"] == "m" and call["output_format"] is NormalizedBatch
    assert json.loads(call["messages"][0]["content"]) == [
        {
            "id": 0,
            "forms": ["Александра Моора", "Моору"],
            "quote": "Суд арестовал Александра Моора.",
        }
    ]


def test_a_cut_answer_is_a_failure_not_a_name() -> None:
    normalizer, _ = _normalizer(_response([], stop_reason="max_tokens"))

    with pytest.raises(NameNormalizerError, match="max_tokens"):
        normalizer.normalize(ITEMS)


def test_without_an_api_key_there_is_no_normalizer() -> None:
    assert ClaudeNameNormalizer.from_env({}) is None
    assert ClaudeNameNormalizer.from_env({"ANTHROPIC_API_KEY": " "}) is None
    configured = ClaudeNameNormalizer.from_env(
        {"ANTHROPIC_API_KEY": "sk-test", "ENTITY_NORMALIZE_MODEL": "claude-opus-5"}
    )
    assert configured is not None and configured.model == "claude-opus-5"


def _openrouter(handler: Any) -> tuple[OpenRouterNameNormalizer, list[dict[str, Any]]]:
    import httpx

    sent: list[dict[str, Any]] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        result: httpx.Response = handler(request)
        return result

    client = httpx.Client(transport=httpx.MockTransport(record))
    return OpenRouterNameNormalizer("or-key", model="deepseek/x", http_client=client), sent


def _answer(content: str, finish_reason: str = "stop") -> Any:
    import httpx

    return lambda _request: httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


def test_openrouter_asks_for_the_schema_and_only_providers_that_honour_it() -> None:
    names = {
        "names": [
            {
                "id": 0,
                "source": "Александра Моора",
                "nominative": "Александр Моор",
                "gender": "male",
                "is_person": True,
            }
        ]
    }
    normalizer, sent = _openrouter(_answer(json.dumps(names, ensure_ascii=False)))

    answers = normalizer.normalize(ITEMS)

    assert answers[0].nominative == "Александр Моор"
    [body] = sent
    assert body["model"] == "deepseek/x"
    assert body["provider"] == {"require_parameters": True}
    # A reasoning model would spend max_tokens thinking and cut the answer.
    assert body["reasoning"] == {"enabled": False}
    assert body["response_format"]["json_schema"]["strict"] is True
    assert json.loads(body["messages"][1]["content"])[0]["forms"] == ["Александра Моора", "Моору"]


@pytest.mark.parametrize(
    "handler",
    [
        lambda _request: __import__("httpx").Response(402, json={"error": "no credit"}),
        _answer("not json"),
        _answer('{"names": [{"id": 0}]}'),
        _answer('{"names": []}', finish_reason="length"),
    ],
    ids=["http-error", "not-json", "schema-broken", "cut"],
)
def test_openrouter_failures_are_errors_not_names(handler: Any) -> None:
    normalizer, _ = _openrouter(handler)

    with pytest.raises(NameNormalizerError):
        normalizer.normalize(ITEMS)


def test_openrouter_is_preferred_when_both_keys_are_set() -> None:
    both = name_normalizer_from_env({"OPENROUTER_API_KEY": "or", "ANTHROPIC_API_KEY": "sk"})
    claude_only = name_normalizer_from_env({"ANTHROPIC_API_KEY": "sk"})

    assert (
        isinstance(both, OpenRouterNameNormalizer) and both.model == "deepseek/deepseek-v4.1-flash"
    )
    assert isinstance(claude_only, ClaudeNameNormalizer)
    assert name_normalizer_from_env({}) is None


def test_a_cut_answer_names_the_cut_not_the_broken_json() -> None:
    normalizer, _ = _openrouter(_answer('{"names": [{"id": 0, "nomin', finish_reason="length"))

    with pytest.raises(NameNormalizerError, match="finish_reason=length"):
        normalizer.normalize(ITEMS)


def _named(id_: int, source: str, nominative: str, *, is_person: bool = True) -> NormalizedName:
    return NormalizedName(
        id=id_, source=source, nominative=nominative, gender="male", is_person=is_person
    )


def test_an_answer_that_lost_count_is_dropped_not_applied() -> None:
    items = [
        NameItem(0, ("Сергей Власов",), ""),
        NameItem(1, ("Геннадий Капралов", "Геннадий Геннадьевич Капралов"), ""),
        NameItem(2, ("Слава Украине",), ""),
    ]
    answers = matched_answers(
        items,
        [
            # One answer per form: the ids after it are off by one.
            _named(0, "Сергей Власов", "Роман Андреевич Попков"),
            _named(1, "Геннадий Капралов", "Геннадий Геннадьевич Капралов"),
            _named(2, "Геннадий Геннадьевич Капралов", "Геннадий Капралов"),
            _named(2, "Слава Украине", "Слава Украине", is_person=False),
        ],
    )

    # 0: the surname of another person; the first answer for 2 echoes another item's
    # form and is dropped, the one that echoes its own form is kept.
    assert {id_: answer.nominative for id_, answer in answers.items()} == {
        1: "Геннадий Геннадьевич Капралов",
        2: "Слава Украине",
    }


def test_a_declined_surname_still_matches_its_nominative() -> None:
    items = [NameItem(0, ("Александра Моора", "Моору"), "")]

    answers = matched_answers(items, [_named(0, "Александра Моора", "Александр Моор")])

    assert answers[0].nominative == "Александр Моор"


def test_a_surname_first_form_still_matches_the_answer_that_reorders_it() -> None:
    items = [NameItem(0, ("Турбин Арсений", "Арсений"), "")]

    answers = matched_answers(items, [_named(0, "Турбин Арсений", "Арсений Турбин")])

    assert answers[0].nominative == "Арсений Турбин"
