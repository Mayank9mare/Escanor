"""
core/failover.py

Free tiers run out: Gemini's daily quota, Groq's per-minute limit,
OpenRouter's 50 requests a day. When a model answers "rate limited" or
"quota exhausted", the agent carries on with the next model in
[models] fallbacks that has a key, instead of failing the turn:

    gemini hits its quota -> "switched to groq" -> the same step is retried

A per-minute limit rests the model only as long as the provider asks
("try again in 7.3s"), else a minute. A request that sends nothing for 20 s (a stuck free-tier queue) fails over
the same way and rests the model for two minutes.

The exhausted model rests for a cooldown (longer for a daily quota), and
the next turn after it has rested switches back to the model you chose.

Only limit-type errors fail over. A bad request or a refusal would fail
the same way on every model, so those still end the turn with the error.
A step fails over only before any of its reply has streamed, so you never
hear half an answer twice.
"""
from __future__ import annotations
import re
import time
from typing import Callable
from ..models.base import Model

_LIMIT = re.compile(r"\b429\b|rate.?limit|quota|resource.?exhausted|too many requests|\b503\b|overloaded|"
                    r"unavailable|capacity|timed? ?out", re.I)
_TIMEOUT = re.compile(r"timed? ?out", re.I)
_RETRY_IN = re.compile(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)\b", re.I)
_PER_MINUTE = re.compile(r"per minute|\bTPM\b|\bRPM\b", re.I)
_DAILY = re.compile(r"per.?day|daily|PerDay|tokens per day|requests per day|\bRPD\b|\bTPD\b", re.I)


def is_limit_error(message: str) -> bool:
    return bool(_LIMIT.search(message))


class Failover:
    def __init__(self, build: Callable[[str], tuple[Model, str]], chain: list[str],
                 cooldown_seconds: float = 900.0, daily_cooldown_seconds: float = 3 * 3600.0,
                 clock: Callable[[], float] = time.monotonic):
        self.build = build
        self.chain = list(chain)
        self.cooldown = cooldown_seconds
        self.daily_cooldown = daily_cooldown_seconds
        self.clock = clock
        self._resting: dict[str, float] = {}   # "provider:model" -> rest until (clock time)
        self.preferred: str | None = None      # the model the user chose, while we're on a fallback

    def mark(self, spec: str, message: str) -> None:
        retry = _RETRY_IN.search(message)
        if retry:                        # the provider says when: Groq's "try again in 7.3s"
            minutes, amount, unit = retry.groups()
            rest = int(minutes or 0) * 60 + float(amount) / (1000 if unit.lower() == "ms" else 1) + 2
        elif _DAILY.search(message):
            rest = self.daily_cooldown
        elif _PER_MINUTE.search(message):
            rest = 60.0
        elif _TIMEOUT.search(message):   # one stuck request: a short rest, not the full cooldown
            rest = min(self.cooldown, 120.0)
        else:
            rest = self.cooldown
        self._resting[spec] = self.clock() + rest

    def resting(self, spec: str) -> bool:
        until = self._resting.get(spec)
        if until is None:
            return False
        if self.clock() >= until:
            del self._resting[spec]
            return False
        return True

    def next(self, current: str) -> tuple[Model, str] | None:
        """The first fallback that isn't `current`, isn't resting and has a key."""
        for alias in self.chain:
            try:
                model, spec = self.build(alias)
            except (ValueError, RuntimeError):
                continue  # no key for it, or unknown: skip
            if spec != current and not self.resting(spec):
                return model, spec
        return None

    def back_home(self, current: str) -> tuple[Model, str] | None:
        """The user's own model again, once it has rested."""
        if self.preferred is None or self.preferred == current or self.resting(self.preferred):
            return None
        try:
            model, spec = self.build(self.preferred)
        except (ValueError, RuntimeError):
            return None
        self.preferred = None
        return model, spec
