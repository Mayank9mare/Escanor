"""
tools/base.py

A "Tool" is an action Escanor may take: look something up, read a file,
control the music. The model can only ASK to run a tool by name with
arguments; the agent decides whether it may run (tools/permissions.py)
and then runs it.

Each tool declares, as class attributes:

  side_effect     "read"     -- only reads (search, fetch, time)
                  "write"    -- changes Escanor's own state (notes, queue)
                  "external" -- acts on the world outside the conversation
                                (sends, creates, deletes, controls apps):
                                always needs the user's confirmation
  group           which tool group it belongs to. "core" tools are always
                  offered to the model; other groups are switched on with
                  the enable_tools tool, keeping each request small.
  untrusted_output  True when the result contains text from outside
                  (web pages, papers, imported notes) that could try to
                  instruct the model. Such output is fenced as data.
  affects_prompt  True when what it writes is put into every future
                  prompt (e.g. remembered facts). After untrusted content
                  was read in a turn, these need confirmation too.
"""
from __future__ import annotations
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

READ, WRITE, EXTERNAL = "read", "write", "external"


@dataclass
class ToolResult:
    """What happened when a tool ran."""
    ok: bool          # True if it worked, False if it failed
    output: str        # human/model-readable text describing what happened
    data: Any = None    # optional structured data, e.g. file contents


class Tool(ABC):
    name: str
    description: str
    # A JSON schema describing the arguments this tool accepts. Models
    # read this to know how to call the tool correctly.
    input_schema: dict
    side_effect: str = READ
    group: str = "core"
    untrusted_output: bool = False
    affects_prompt: bool = False

    @abstractmethod
    def run(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def to_schema(self) -> dict:
        """Convert this tool into the JSON format models expect to see."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def describe_call(self, arguments: dict) -> str:
        """One line shown to the user when asking to confirm this call."""
        return f"{self.name} {json.dumps(arguments, ensure_ascii=False)}"


class AsyncTool(Tool):
    """A tool whose work needs the event loop (e.g. starting the DJ's
    background task). The registry awaits run_async() instead of running
    run() on a worker thread."""

    async def run_async(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def run(self, **kwargs) -> ToolResult:
        raise RuntimeError(f"{self.name} is async; use ToolRegistry.dispatch_async")
