"""
models/anthropic_provider.py

Talks to Anthropic's Claude models. This file is the ONLY place in
Escanor that knows Anthropic's specific message format -- everywhere
else uses the generic Model / ModelResponse shape from base.py.

Three Claude-specific details handled here:

1. Thinking blocks must round-trip. Current Claude models (Haiku 5.5,
   Sonnet 5.5) think by default, and in a tool-calling loop the
   assistant turn's thinking blocks have to be sent back unchanged on the
   next request. The generic message shape can't hold them, so every
   ModelResponse carries `raw` (the exact reply blocks), and
   _to_claude_messages() replays `raw` whenever the stored turn came from
   this same model. Turns from another model use the generic shape --
   Claude ignores foreign thinking anyway.

2. Parallel tool results go in ONE user message. Claude may ask for
   several tools in one turn; all their results must come back together
   in the next user message (splitting them teaches the model to stop
   calling tools in parallel).

3. Prompt caching via top-level `cache_control`, so the growing
   conversation prefix is reused turn to turn (cheaper and faster,
   never changes what the model says).
"""
from __future__ import annotations
from typing import Any, Iterator
from .base import Model, ModelResponse, StreamEvent, ToolCall


class AnthropicModel(Model):
    def __init__(self, model_name: str, api_key: str, max_tokens: int = 16000, effort: str | None = None):
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("Run `uv pip install anthropic` to use the anthropic provider.") from e
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self.name = model_name
        self.max_tokens = max_tokens
        # low | medium | high | xhigh | max. None = the model's own default
        # (medium on Haiku 5.5). Lower = faster, terser, cheaper.
        self.effort = effort

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        try:
            message = self._client.messages.create(**self._request(messages, tools))
        except self._anthropic.APIError as e:
            raise RuntimeError(f"Anthropic request failed: {e}") from e
        return self._from_message(message)

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        for event in self.chat_stream_tools(messages):
            if event.kind == "text":
                yield event.text

    def chat_stream_tools(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[StreamEvent]:
        try:
            with self._client.messages.stream(**self._request(messages, tools)) as stream:
                for text in stream.text_stream:
                    yield StreamEvent("text", text=text)
                message = stream.get_final_message()
        except self._anthropic.APIError as e:
            raise RuntimeError(f"Anthropic request failed: {e}") from e
        yield StreamEvent("done", response=self._from_message(message))

    def _request(self, messages: list[dict], tools: list[dict] | None) -> dict[str, Any]:
        system_text = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        kwargs: dict[str, Any] = {
            "model": self.name,
            "max_tokens": self.max_tokens,
            "messages": self._to_claude_messages(messages),
            "cache_control": {"type": "ephemeral"},
        }
        if system_text:
            kwargs["system"] = system_text
        if tools:
            kwargs["tools"] = tools
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        return kwargs

    def _from_message(self, message: Any) -> ModelResponse:
        text = ""
        tool_calls: list[ToolCall] = []
        raw_blocks: list[dict] = []
        for block in message.content:
            if block.type == "text":
                text += block.text
                raw_blocks.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
                raw_blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
            elif block.type == "thinking":
                raw_blocks.append({"type": "thinking", "thinking": block.thinking, "signature": block.signature})
            elif block.type == "redacted_thinking":
                raw_blocks.append({"type": "redacted_thinking", "data": block.data})

        usage = message.usage
        return ModelResponse(
            text=text, tool_calls=tool_calls, stop_reason=message.stop_reason or "end_turn",
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            raw={"provider": "anthropic", "model": self.name, "content": raw_blocks},
        )

    def _to_claude_messages(self, messages: list[dict]) -> list[dict]:
        """
        Convert the generic message list into Claude's format, where tool
        calls and tool results are content *blocks* inside a message
        rather than separate roles. Consecutive tool results are merged
        into one user message (see the module docstring, point 2).
        """
        claude_messages: list[dict] = []
        for m in messages:
            role = m["role"]
            if role == "system":
                continue  # sent separately, via the `system` kwarg

            if role == "user":
                claude_messages.append({"role": "user", "content": m["content"]})

            elif role == "assistant":
                raw = m.get("raw")
                if raw and raw.get("provider") == "anthropic" and raw.get("model") == self.name and raw.get("content"):
                    content = [dict(block) for block in raw["content"]]
                else:
                    content = []
                    if m.get("content"):
                        content.append({"type": "text", "text": m["content"]})
                    for call in m.get("tool_calls") or []:
                        content.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
                if not content:
                    content = [{"type": "text", "text": "(no reply)"}]
                claude_messages.append({"role": "assistant", "content": content})

            elif role == "tool":
                result = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if m.get("is_error"):
                    result["is_error"] = True
                previous = claude_messages[-1] if claude_messages else None
                if (
                    previous and previous["role"] == "user" and isinstance(previous["content"], list)
                    and all(b.get("type") == "tool_result" for b in previous["content"])
                ):
                    previous["content"].append(result)
                else:
                    claude_messages.append({"role": "user", "content": [result]})
        return claude_messages
