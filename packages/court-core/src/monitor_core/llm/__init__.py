"""Asking a language model over an OpenAI-compatible API: the request and the
provider's envelope. Prompts, answer schemas, failure policy and budgets belong to the
caller."""

from monitor_core.llm.chat import (
    CHAT_ENVELOPE_ERRORS,
    ChatCompletion,
    post_json_chat,
    read_chat_completion,
)

__all__ = [
    "CHAT_ENVELOPE_ERRORS",
    "ChatCompletion",
    "post_json_chat",
    "read_chat_completion",
]
