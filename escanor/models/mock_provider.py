"""
models/mock_provider.py

A fake model that needs no API key and costs nothing. Two modes:

- Echo (default): replies "(mock reply) You said: ..." -- lets you try
  the CLI end to end with no provider set up.
- Scripted: give it a list of ModelResponse objects and it returns them
  in order, one per call -- how tests drive the agent loop through
  specific tool-call sequences without a real model.

chat_stream_tools() streams the reply word by word so streaming code
paths get exercised too. Every call's messages/tools are recorded in
`calls` for tests to inspect.
"""
from __future__ import annotations
from typing import Iterator
from .base import Model, ModelResponse, StreamEvent


class MockModel(Model):
    def __init__(self, role: str = "generic", script: list[ModelResponse] | None = None, name: str = "mock-model"):
        self.role = role
        self.name = name
        self._script = list(script) if script is not None else None
        self.calls: list[dict] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        if self._script is not None:
            if not self._script:
                raise RuntimeError("MockModel script exhausted")
            return self._script.pop(0)
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        reply = f"(mock reply) You said: {last_user}" if last_user else "(mock model has nothing to say)"
        return ModelResponse(text=reply, stop_reason="end_turn")

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        for event in self.chat_stream_tools(messages):
            if event.kind == "text":
                yield event.text

    def chat_stream_tools(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[StreamEvent]:
        response = self.chat(messages, tools)
        words = response.text.split(" ") if response.text else []
        for i, word in enumerate(words):
            yield StreamEvent("text", text=word + (" " if i < len(words) - 1 else ""))
        yield StreamEvent("done", response=response)
