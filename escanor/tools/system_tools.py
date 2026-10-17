"""
tools/system_tools.py

Tools about Escanor itself rather than the world: the current time,
switching tool groups on, and switching models.
"""
from __future__ import annotations
from datetime import datetime
from typing import Callable
from .base import READ, WRITE, Tool, ToolResult
from .registry import ToolRegistry


class GetTimeTool(Tool):
    name = "get_time"
    description = "Get the current local date, time, weekday and timezone."
    input_schema = {"type": "object", "properties": {}}
    side_effect = READ

    def __init__(self, now: Callable[[], datetime] | None = None):
        self._now = now or (lambda: datetime.now().astimezone())

    def run(self) -> ToolResult:
        now = self._now()
        return ToolResult(ok=True, output=f"{now:%A %d %B %Y, %H:%M} ({now.tzname()}, {now.isoformat(timespec='seconds')})")


class EnableToolsTool(Tool):
    """Switches on a tool group. Its description lists the groups that
    exist, so the model knows what it can ask for without every group's
    schemas being sent on every request."""
    name = "enable_tools"
    side_effect = READ

    def __init__(self, registry: ToolRegistry, group_descriptions: dict[str, str] | None = None):
        self._registry = registry
        self._descriptions = group_descriptions or {}

    @property
    def description(self) -> str:  # type: ignore[override]
        optional = [g for g in self._registry.groups() if g != "core"]
        if not optional:
            return "Switch on an extra group of tools. (No extra groups are installed yet.)"
        lines = [f"- {g}: {self._descriptions.get(g, ', '.join(self._registry.groups()[g]))}" for g in optional]
        return "Switch on an extra group of tools for the rest of this conversation. Groups:\n" + "\n".join(lines)

    @property
    def input_schema(self) -> dict:  # type: ignore[override]
        return {
            "type": "object",
            "properties": {"group": {"type": "string", "description": "Group name."}},
            "required": ["group"],
        }

    def run(self, group: str) -> ToolResult:
        if group == "core":
            return ToolResult(ok=True, output="The core tools are always on.")
        if not self._registry.enable(group):
            known = ", ".join(g for g in self._registry.groups() if g != "core") or "none"
            return ToolResult(ok=False, output=f"No tool group '{group}'. Available: {known}.")
        names = ", ".join(self._registry.groups()[group])
        return ToolResult(ok=True, output=f"Enabled '{group}': {names}. They're available from your next step.")


class SwitchModelTool(Tool):
    """Lets the user say "switch to sonnet" in plain language. The switch
    applies after the current turn (Agent.request_model_switch), so one
    turn never mixes two models' tool calls."""
    name = "switch_model"
    side_effect = WRITE
    input_schema = {
        "type": "object",
        "properties": {"model": {"type": "string", "description": "An alias (e.g. haiku, sonnet, gpt, gemini, local) or provider:model-id."}},
        "required": ["model"],
    }

    def __init__(self, request_switch: Callable[[str], str], aliases: dict[str, str]):
        self._request_switch = request_switch
        self._aliases = aliases

    @property
    def description(self) -> str:  # type: ignore[override]
        names = ", ".join(f"{alias} ({spec})" for alias, spec in self._aliases.items() if alias != "mock")
        return f"Switch the AI model answering the user, only when they ask. Known models: {names}."

    def run(self, model: str) -> ToolResult:
        try:
            spec = self._request_switch(model)
        except (ValueError, RuntimeError) as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output=f"Switching to {spec}; it answers from the user's next message.")
