"""
tools/registry.py

The ToolRegistry is the agent's "front desk." The model never runs code
directly -- it only ever says "please run tool X with these arguments."
The registry looks up tool X, runs it, and reports back what happened.
(Whether it's ALLOWED to run is a separate question, answered by
tools/permissions.py before dispatch() is called.)
"""
from __future__ import annotations
from ..models.base import ToolCall
from .base import Tool, ToolResult


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools = {tool.name: tool for tool in tools or []}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        """The list of tool descriptions to hand to the model."""
        return [tool.to_schema() for tool in self._tools.values()]

    def dispatch(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(ok=False, output=f"Unknown tool: {call.name}")
        try:
            return tool.run(**call.arguments)
        except TypeError as e:
            return ToolResult(ok=False, output=f"Bad arguments for {call.name}: {e}")
        except Exception as e:  # noqa: BLE001 -- a failing tool is a result the model can react to
            return ToolResult(ok=False, output=f"{call.name} failed: {type(e).__name__}: {e}")
