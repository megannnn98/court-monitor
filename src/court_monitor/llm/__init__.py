"""LLM-assisted judgement, provider-neutral.

The backend is whatever ``CM_LLM_BASE_URL`` points at — anything speaking the
OpenAI chat-completions shape. Disabled by default (``CM_LLM_MODE=disabled``);
nothing in the pipeline requires it, and everything degrades to the
deterministic path when it is off or unreachable.
"""

from court_monitor.llm.client import LlmClient, LlmUnavailable, client_from_settings
from court_monitor.llm.disambiguation import Disambiguation, LlmDisambiguator, Verdict

__all__ = [
    "Disambiguation",
    "LlmClient",
    "LlmDisambiguator",
    "LlmUnavailable",
    "client_from_settings",
    "Verdict",
]
