"""A slice of OpenRouter's `/api/v1/models`, as it answered on 16.10.2026: per-token USD."""

from __future__ import annotations

STRUCTURED = ["max_tokens", "response_format", "structured_outputs", "temperature"]


def _model(model_id: str, prompt: str, completion: str, parameters: list[str]) -> dict[str, object]:
    return {
        "id": model_id,
        "name": model_id,
        "pricing": {"prompt": prompt, "completion": completion},
        "supported_parameters": parameters,
    }


CATALOG_PAYLOAD: dict[str, object] = {
    "data": [
        _model("deepseek/deepseek-v4.1-flash", "0.00000009", "0.00000018", STRUCTURED),
        _model("deepseek/deepseek-v4-pro", "0.00000045", "0.00000348", STRUCTURED),
        _model("xiaomi/mimo-v2.6-flash", "0.0000001", "0.00000028", STRUCTURED),
        _model("xiaomi/mimo-v2.6-pro", "0.000000435", "0.00000087", STRUCTURED),
        _model("qwen/qwen3.8-flash", "0.00000015", "0.00000047", STRUCTURED),
        _model("qwen/qwen3.7-plus", "0.00000032", "0.00000128", STRUCTURED),
        _model("nvidia/nemotron-3.5-super-vl:free", "0", "0", STRUCTURED),
        _model("nvidia/nemotron-3.5-super-vl", "0.0000001", "0.0000004", STRUCTURED),
        _model("openai/gpt-oss-120b", "0.00000003", "0.00000015", STRUCTURED),
        # A free model without a JSON schema: the names step cannot use it.
        _model("google/gemma-4-31b-it:free", "0", "0", ["max_tokens", "response_format"]),
        # A router: its price depends on the route.
        _model("openrouter/auto", "-1", "-1", STRUCTURED),
        # `stealth/space-bunny-alpha` is not listed: it has no endpoints any more.
    ]
}
