"""
core/router.py

Escalation: Haiku answers by default because it's fast and cheap; some
turns deserve Sonnet. The router picks the model for one turn:

- you ask for it: "think hard", "think carefully", "take your time",
  "deep dive" -> the escalation model for this turn only
- the turn is getting long: after `after_steps` tool rounds the
  remaining steps switch to the escalation model -- only when both
  models are from the same provider, since a turn's in-flight tool calls
  can't move between providers (D4, D13)

After an escalated turn the agent goes back to its normal model. The
switch is announced as a ModelSwitched event either way.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Callable
from ..models.base import Model

_THINK_HARD = re.compile(r"\b(think (hard|harder|carefully|deeply|step by step)|take your time|deep dive|reason carefully)\b", re.I)


@dataclass
class Router:
    build: Callable[[str], tuple[Model, str]]   # alias -> (model, "provider:model-id")
    escalate_to: str = "sonnet"
    after_steps: int = 4                         # 0 disables step-based escalation

    def wants_escalation(self, user_text: str) -> bool:
        return bool(_THINK_HARD.search(user_text))

    def escalation(self) -> tuple[Model, str] | None:
        try:
            return self.build(self.escalate_to)
        except (ValueError, RuntimeError):
            return None  # e.g. no key for the escalation model: stay put


def same_provider(spec_a: str, spec_b: str) -> bool:
    return spec_a.split(":", 1)[0] == spec_b.split(":", 1)[0]
