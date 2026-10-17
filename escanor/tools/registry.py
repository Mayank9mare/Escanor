"""
tools/registry.py

The ToolRegistry is the agent's "front desk." The model never runs code
directly -- it only ever says "please run tool X with these arguments."
The registry looks up tool X, runs it, and reports back what happened.
(Whether it's ALLOWED to run is decided first, by tools/permissions.py.)

Tool groups: only tools in enabled groups are offered to the model.
"core" is always enabled; the rest are switched on by the enable_tools
tool and stay on until reset() (a new conversation). Fewer schemas per
request means fewer tokens on every call and better tool choice from
small local models.
"""
from __future__ import annotations
import asyncio
from ..models.base import ToolCall
from .base import AsyncTool, Tool, ToolResult

CORE = "core"


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        self._enabled: set[str] = {CORE}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def groups(self) -> dict[str, list[str]]:
        """Every group and the tool names in it, core first."""
        result: dict[str, list[str]] = {}
        for tool in self._tools.values():
            result.setdefault(tool.group, []).append(tool.name)
        return dict(sorted(result.items(), key=lambda item: (item[0] != CORE, item[0])))

    def enabled_groups(self) -> set[str]:
        return set(self._enabled)

    def enable(self, group: str) -> bool:
        """Turn a group on. Returns False if no tool is in that group."""
        if group not in self.groups():
            return False
        self._enabled.add(group)
        return True

    def reset(self) -> None:
        self._enabled = {CORE}

    def schemas(self) -> list[dict]:
        """Descriptions of the tools in enabled groups, for the model."""
        return [tool.to_schema() for tool in self._tools.values() if tool.group in self._enabled]

    def _refusal(self, call: ToolCall) -> ToolResult | None:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(ok=False, output=f"Unknown tool: {call.name}")
        if tool.group not in self._enabled:
            return ToolResult(ok=False, output=f"{call.name} is in the '{tool.group}' group; call enable_tools first.")
        return None

    async def dispatch_async(self, call: ToolCall) -> ToolResult:
        """Run a call from async code: AsyncTools on the loop, the rest on a thread."""
        tool = self._tools.get(call.name)
        if not isinstance(tool, AsyncTool) or self._refusal(call):
            return await asyncio.to_thread(self.dispatch, call)
        try:
            return await tool.run_async(**call.arguments)
        except TypeError as e:
            return ToolResult(ok=False, output=f"Bad arguments for {call.name}: {e}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(ok=False, output=f"{call.name} failed: {type(e).__name__}: {e}")

    def dispatch(self, call: ToolCall) -> ToolResult:
        refusal = self._refusal(call)
        if refusal:
            return refusal
        tool = self._tools[call.name]
        try:
            return tool.run(**call.arguments)
        except TypeError as e:
            return ToolResult(ok=False, output=f"Bad arguments for {call.name}: {e}")
        except Exception as e:  # noqa: BLE001 -- a failing tool is a result the model can react to
            return ToolResult(ok=False, output=f"{call.name} failed: {type(e).__name__}: {e}")
