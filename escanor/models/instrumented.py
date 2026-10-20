"""
models/instrumented.py

Wraps any Model with telemetry recording, without any provider file
needing to know telemetry exists. registry.get_model() is the only
place this gets applied -- every provider stays exactly as focused on
"how do I talk to this API" as before.

Trace attribution (which unit of work a call belongs to) is set
explicitly via with_trace(), not inferred -- see telemetry.py's module
docstring for why an ambient/contextvars approach doesn't work here.
"""
from __future__ import annotations
from typing import Iterator
from .base import Model, ModelResponse, StreamEvent
from .. import telemetry


def _request_summary(messages: list[dict]) -> dict:
    """What telemetry keeps of a request: its size and the newest message.
    Storing the whole conversation on every call made telemetry.db grow
    with (turns x conversation length) and copied private notes into a
    second database (B9)."""
    last = messages[-1] if messages else {}
    return {"messages": len(messages), "last_role": last.get("role"), "last": str(last.get("content", ""))[:2000]}


class InstrumentedModel(Model):
    def __init__(self, inner: Model, provider: str, role: str):
        self._inner = inner
        self._provider = provider
        self._role = role
        self.name = inner.name
        self._trace_id: str | None = None
        self._component: str | None = None

    def traced(self, trace_id: str, component: str) -> "InstrumentedModel":
        """A copy attributed to one trace (a turn). Unlike with_trace(), it
        leaves this shared instance alone, so concurrent turns -- a page and
        a hotkey, or the DJ -- never record into each other's trace."""
        copy = InstrumentedModel(self._inner, self._provider, self._role)
        copy._trace_id, copy._component = trace_id, component
        return copy

    def with_trace(self, trace_id: str, component: str = "untraced") -> "InstrumentedModel":
        self._trace_id = trace_id
        self._component = component
        return self

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        with telemetry.record_span(
            trace_id=self._trace_id, component=self._component, role=self._role,
            provider=self._provider, model=self.name,
        ) as result:
            result["request"] = _request_summary(messages)
            response = self._inner.chat(messages, tools)
            result["input_tokens"] = response.input_tokens
            result["output_tokens"] = response.output_tokens
            result["response"] = {
                "text": response.text, "tool_calls": response.tool_calls, "stop_reason": response.stop_reason,
            }
        return response

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        # No per-token usage available from any provider's streaming
        # path today (see telemetry.py's module docstring) -- this
        # still records duration, success/failure, and the full
        # streamed text (accumulated as it's re-yielded, since the
        # inner provider only ever hands us one chunk at a time).
        with telemetry.record_span(
            trace_id=self._trace_id, component=self._component, role=self._role,
            provider=self._provider, model=self.name, streamed=True,
        ) as result:
            result["request"] = _request_summary(messages)
            chunks: list[str] = []
            for chunk in self._inner.chat_stream(messages):
                chunks.append(chunk)
                yield chunk
            result["response"] = {"text": "".join(chunks)}

    def chat_stream_tools(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[StreamEvent]:
        # Must be forwarded explicitly: without this, Model's default
        # would call self.chat() and silently turn every provider's real
        # token streaming into one non-streaming call.
        with telemetry.record_span(
            trace_id=self._trace_id, component=self._component, role=self._role,
            provider=self._provider, model=self.name, streamed=True,
        ) as result:
            result["request"] = _request_summary(messages)
            for event in self._inner.chat_stream_tools(messages, tools):
                if event.kind == "done" and event.response is not None:
                    response = event.response
                    result["input_tokens"] = response.input_tokens
                    result["output_tokens"] = response.output_tokens
                    result["response"] = {
                        "text": response.text, "tool_calls": response.tool_calls, "stop_reason": response.stop_reason,
                    }
                yield event
