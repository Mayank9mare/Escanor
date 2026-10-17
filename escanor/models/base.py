"""
models/base.py

This file defines the "shape" every AI model provider must follow.

Why do we need this?
---------------------
Escanor should work with different AI companies (Anthropic, OpenAI,
or a fake "mock" model for testing) without the rest of the program
caring which one is being used.

Think of it like a power outlet: any appliance can plug in, as long as
it has the same shape of plug. That "shape" is the Model class below.
Every provider file (anthropic_provider.py, openai_provider.py,
mock_provider.py) is a different "appliance" that fits the same plug.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class ToolCall:
    """One request from the model to run a tool, e.g. 'read_file(path=x)'.

    provider_extra -- opaque, provider-specific data that has to be
    echoed back verbatim when this call (and its result) are replayed
    into the next request, or that provider will reject the follow-up.
    Discovered with Gemini's OpenAI-compatible endpoint: it attaches a
    "thought_signature" to each tool call and requires it back on the
    next turn ("Function call is missing a thought_signature") -- there
    is no way to satisfy that from a generic {id, name, arguments}
    shape, so providers that need this populate it on the way out
    (openai_provider.py's chat()) and re-attach it on the way back in
    (_to_openai_messages()). None for every other provider today."""
    id: str
    name: str
    arguments: dict[str, Any]
    provider_extra: dict[str, Any] | None = None


@dataclass
class ModelResponse:
    """
    Everything the model said in one turn of the conversation.

    text          -- what the model said in plain language (may be empty)
    tool_calls    -- tools the model wants to run (may be empty)
    stop_reason   -- why the model stopped talking, e.g. "end_turn" or "tool_use"
    input_tokens  -- tokens the provider says the prompt cost, if it told us (see telemetry.py)
    output_tokens -- tokens the provider says the reply cost, if it told us
    """
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    input_tokens: int | None = None
    output_tokens: int | None = None
    # The provider's own reply blocks, for replaying this turn verbatim to
    # the SAME model. Needed for Claude: with tool use, its thinking
    # blocks must come back unchanged on the next request. Shape:
    # {"provider": "anthropic", "model": "<id>", "content": [<block dicts>]}.
    # None when the generic text/tool_calls shape is enough.
    raw: dict[str, Any] | None = None


@dataclass
class StreamEvent:
    """One event from Model.chat_stream_tools().

    kind="text" -- a fragment of the reply, as it's generated
    kind="done" -- the end of the reply; `response` holds the complete
                   ModelResponse (full text, tool calls, usage, raw)
    """
    kind: str
    text: str = ""
    response: ModelResponse | None = None


class Model(ABC):
    """
    The common interface every model provider must implement.

    Nothing else in Escanor needs to know which company is
    answering -- it just calls `.chat(messages, tools)` and gets back a
    ModelResponse, no matter which provider is plugged in underneath.
    """

    name: str = "base-model"

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        """
        Send a conversation (a list of {"role": ..., "content": ...}
        dicts) to the model and get back a ModelResponse.

        `tools` is an optional list of tool definitions (JSON schemas)
        the model is allowed to call during this turn.
        """
        raise NotImplementedError

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        """
        Like chat(), but yield the reply as it's generated instead of
        waiting for the whole thing -- what the chatbox UI uses so a
        response appears word by word instead of all at once.

        Text-only, no `tools` parameter: the chatbox is a plain
        conversational assistant, not the Generator role, so there's no
        tool-call streaming to handle. Providers override this for real
        token-by-token streaming; this default just falls back to one
        non-streaming call so a provider that hasn't been updated still
        works (just without the incremental effect).
        """
        yield self.chat(messages).text

    def chat_stream_tools(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[StreamEvent]:
        """
        Stream a reply that may also call tools: yields "text" events as
        the reply is generated, then exactly one "done" event carrying the
        full ModelResponse (with tool_calls). This is what the agent loop
        uses, so voice can start speaking before the model has finished.

        Default: one non-streaming chat() call, emitted as a single text
        event -- correct for any provider, just without the incremental
        effect. Providers override it for real token streaming.
        """
        response = self.chat(messages, tools)
        if response.text:
            yield StreamEvent("text", text=response.text)
        yield StreamEvent("done", response=response)

    def with_trace(self, trace_id: str, component: str = "untraced") -> "Model":
        """Attribute every call this instance makes from now on to one
        trace, for telemetry (see telemetry.py, models/instrumented.py).
        No-op by default so any Model works whether or not it came from
        registry.get_model() (which wraps it in InstrumentedModel) --
        telemetry is optional plumbing, never a requirement to
        implement a provider.

        Mutates in place rather than returning a new instance: harness
        callers reuse the same three Model objects (planner/generator/
        evaluator) across every request in a REPL session, so each new
        request re-attributes the *same* objects to its own trace_id
        instead of constructing fresh ones."""
        return self
