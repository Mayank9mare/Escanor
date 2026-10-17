"""
core/events.py

What the agent loop emits while it works. Every interface (the CLI now;
the web UI and the voice loop later) is just a consumer of this stream:
it prints, renders or speaks each event, and never talks to a model or a
tool itself.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from ..models.base import ToolCall
from ..tools.base import ToolResult


@dataclass
class TextDelta:
    """A fragment of the reply, in order. Concatenate to get the reply."""
    text: str


@dataclass
class ToolStarted:
    call: ToolCall


@dataclass
class ToolFinished:
    call: ToolCall
    result: ToolResult


@dataclass
class TurnDone:
    """The turn ended normally. stop_reason is the model's last one
    ("end_turn", "refusal", ...); usage sums tokens across every model
    call in the turn, where the provider reported them."""
    stop_reason: str
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class Error:
    """The turn ended abnormally (provider failure, step limit, cut-off
    reply). The session is left consistent either way."""
    message: str


Event = TextDelta | ToolStarted | ToolFinished | TurnDone | Error
