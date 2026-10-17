"""
tools/base.py

A "Tool" is an action the agent is allowed to take in the real world:
reading a file, writing a file, running a shell command, and so on.

The model itself cannot touch your computer directly. It can only ASK
to run a tool by name, with some arguments. The harness (this program)
is the only thing that actually does it -- and it decides whether to
allow it (see harness/permissions.py).

This file defines the common shape every tool must follow, the same
way models/base.py defines the shape every AI provider must follow.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


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
