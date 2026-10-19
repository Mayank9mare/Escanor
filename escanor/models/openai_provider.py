"""
models/openai_provider.py

Talks to OpenAI's chat models via the Chat Completions API. Like
anthropic_provider.py, this file is the only place that knows OpenAI's
specific message format.

Also doubles as the provider for any other OpenAI-compatible chat
completions endpoint (e.g. OpenCode Zen, Gemini, Sarvam -- see
registry.py's "opencode"/"gemini"/"sarvam" branches) by passing a
different `base_url` -- the request/response shape is the same, only
the host and model names differ. Sarvam additionally needs a
non-standard auth header instead of `Authorization: Bearer`, hence
`extra_headers` below.
"""
from __future__ import annotations
import json
from typing import Iterator
from .base import Model, ModelResponse, StreamEvent, ToolCall
from .images import FOLLOW_UP_TEXT, tool_images


class OpenAIModel(Model):
    def __init__(
        self, model_name: str, api_key: str, base_url: str | None = None,
        extra_headers: dict[str, str] | None = None, timeout: float | None = None,
    ):
        try:
            import openai
        except ImportError as e:
            raise RuntimeError("Run `pip install openai` to use --provider openai.") from e
        self._openai = openai
        # timeout: longest wait for the response to start and between streamed
        # chunks. A stuck free-tier queue then fails over instead of hanging.
        kwargs = {"timeout": openai.Timeout(timeout, connect=10.0), "max_retries": 0} if timeout else {}
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, default_headers=extra_headers, **kwargs)
        self.name = model_name
        # Token usage in streams needs stream_options, which only OpenAI's own
        # endpoint is known to accept; compatible endpoints may reject it (B5).
        self._stream_usage = base_url is None

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        openai_messages = self._to_openai_messages(messages)
        openai_tools = self._to_openai_tools(tools) if tools else None

        try:
            response = self._client.chat.completions.create(
                model=self.name,
                messages=openai_messages,
                tools=openai_tools,
            )
        except self._openai.OpenAIError as e:
            # Same contract as ollama_provider.py: a provider failure
            # (network, rate limit, upstream 5xx) surfaces as a
            # RuntimeError -- what cli.py and chatbox/server.py already
            # catch to report gracefully instead of crashing.
            raise RuntimeError(f"OpenAI-compatible request failed: {e}") from e
        choice = response.choices[0]
        text = choice.message.content or ""
        tool_calls = [
            ToolCall(
                id=tc.id, name=tc.function.name, arguments=json.loads(tc.function.arguments or "{}"),
                # tc.model_extra carries fields outside the OpenAI spec
                # that a compatible provider still sent back (e.g.
                # Gemini's thought_signature) -- see ToolCall's
                # docstring in base.py for why this has to round-trip.
                provider_extra=tc.model_extra or None,
            )
            for tc in (choice.message.tool_calls or [])
        ]
        usage = response.usage
        return ModelResponse(
            text=text, tool_calls=tool_calls, stop_reason=choice.finish_reason or "end_turn",
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        openai_messages = self._to_openai_messages(messages)
        try:
            stream = self._client.chat.completions.create(model=self.name, messages=openai_messages, stream=True)
            for chunk in stream:
                # Some OpenAI-compatible providers (confirmed: Sarvam)
                # send a final chunk with an empty choices list -- a
                # trailing usage summary, not a text delta.
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except self._openai.OpenAIError as e:
            raise RuntimeError(f"OpenAI-compatible streaming request failed: {e}") from e

    def chat_stream_tools(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[StreamEvent]:
        """Streaming with tool calls. Tool calls arrive as fragments keyed
        by `index`: the first fragment for an index carries id + name, the
        rest append to `arguments` (a JSON string, complete only at the
        end). Provider-specific extras on a fragment (Gemini's
        thought_signature) are merged into provider_extra so they
        round-trip like chat()'s do."""
        request: dict = {"model": self.name, "messages": self._to_openai_messages(messages), "stream": True}
        if tools:
            request["tools"] = self._to_openai_tools(tools)
        if self._stream_usage:
            request["stream_options"] = {"include_usage": True}
        usage = None

        text_parts: list[str] = []
        calls: dict[int, dict] = {}
        finish_reason = None
        try:
            for chunk in self._client.chat.completions.create(**request):
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    text_parts.append(delta.content)
                    yield StreamEvent("text", text=delta.content)
                for fragment in delta.tool_calls or []:
                    slot = calls.setdefault(fragment.index, {"id": None, "name": "", "arguments": "", "extra": {}})
                    if fragment.id:
                        slot["id"] = fragment.id
                    if fragment.function and fragment.function.name:
                        slot["name"] = fragment.function.name
                    if fragment.function and fragment.function.arguments:
                        slot["arguments"] += fragment.function.arguments
                    if fragment.model_extra:
                        slot["extra"].update(fragment.model_extra)
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        except self._openai.OpenAIError as e:
            raise RuntimeError(f"OpenAI-compatible streaming request failed: {e}") from e

        tool_calls = []
        for index in sorted(calls):
            slot = calls[index]
            try:
                arguments = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {"_unparseable_arguments": slot["arguments"]}
            tool_calls.append(ToolCall(
                id=slot["id"] or f"call_{index}", name=slot["name"], arguments=arguments,
                provider_extra=slot["extra"] or None,
            ))
        response = ModelResponse(
            text="".join(text_parts), tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else (finish_reason or "end_turn"),
            input_tokens=getattr(usage, "prompt_tokens", None), output_tokens=getattr(usage, "completion_tokens", None),
        )
        yield StreamEvent("done", response=response)

    @staticmethod
    def _to_openai_tools(tools: list[dict]) -> list[dict]:
        return [
            {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in tools
        ]

    @staticmethod
    def _to_openai_messages(messages: list[dict]) -> list[dict]:
        openai_messages: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                openai_messages.append({"role": "system", "content": m["content"]})

            elif m["role"] == "user":
                openai_messages.append({"role": "user", "content": m["content"]})

            elif m["role"] == "assistant":
                entry: dict = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    entry["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                            **(call.provider_extra or {}),
                        }
                        for call in m["tool_calls"]
                    ]
                openai_messages.append(entry)

            elif m["role"] == "tool":
                openai_messages.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        return _with_image_follow_ups(messages, openai_messages)


def _with_image_follow_ups(messages: list[dict], converted: list[dict]) -> list[dict]:
    """OpenAI-style tool messages are text-only: after each run of tool
    results, add one user message carrying their images (models/images.py).
    `converted` has exactly one entry per message in `messages`, so the two
    are walked side by side."""
    if not any(m.get("images") for m in messages if m["role"] == "tool"):
        return converted
    out: list[dict] = []
    pending: list[tuple[str, str]] = []
    for original, entry in zip(messages, converted):
        if original["role"] != "tool" and pending:
            out.append(_image_message(pending))
            pending = []
        out.append(entry)
        if original["role"] == "tool":
            pending += tool_images([original])
    if pending:
        out.append(_image_message(pending))
    return out


def _image_message(images: list[tuple[str, str]]) -> dict:
    content: list[dict] = [{"type": "text", "text": FOLLOW_UP_TEXT}]
    content += [{"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}} for media, data in images]
    return {"role": "user", "content": content}
