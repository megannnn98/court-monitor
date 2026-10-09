"""Asking a language model over an OpenAI-compatible API: the request, the provider's
envelope and its failures. Prompts, answer schemas and budgets belong to the caller."""

from monitor_core.llm.chat import ChatCompletion, Endpoint, ModelError, request_json_chat

__all__ = [
    "ChatCompletion",
    "Endpoint",
    "ModelError",
    "request_json_chat",
]
