"""What the pre-filter drops: only an article Jev chose DROP for, under a fixed policy.

`argmax` drops every DROP choice. `p_drop>=t` drops a DROP choice whose P(DROP) is at least
t, for the thresholds fixed before the run (`THRESHOLDS`) and no others. A KEEP or UNCERTAIN
choice always passes, whatever the probabilities; so does an article without an answer
(an API or model error). The diagnostic answers are never read here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from evaluation.jev_prefilter.prompt import DECISION, DROP

THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)
ARGMAX = "argmax"
POLICIES = (ARGMAX, *(f"p_drop>={t:.2f}" for t in THRESHOLDS))
_THRESHOLD_OF = {f"p_drop>={t:.2f}": t for t in THRESHOLDS}

Outcome = Literal["DROP", "PASS"]


def decide(readings: Mapping[str, Any] | None, policy: str) -> Outcome:
    """DROP or PASS for one article's readings (`None`: no answer) under one policy."""
    if policy != ARGMAX and policy not in _THRESHOLD_OF:
        raise ValueError(f"unknown policy {policy!r}")
    if readings is None:
        return "PASS"
    decision = readings[DECISION]
    if decision["choice"] != DROP:
        return "PASS"
    if policy == ARGMAX or decision["probabilities"][DROP] >= _THRESHOLD_OF[policy]:
        return "DROP"
    return "PASS"
