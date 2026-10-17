"""
models/ollama_provider.py

Talks to an Ollama-compatible HTTP API: either a real Ollama server
directly, or the ollama-wrapper project's usage_proxy sitting in front
of one (https://github.com/Mayank9mare/ollama-wrapper) -- both speak
the same /api/chat format, so this file doesn't need to know which one
it's pointed at.

Unlike the other providers, Ollama needs no API key (its HTTP API is
unauthenticated -- see config.get_ollama_base_url()), so this uses only
the standard library instead of pulling in an HTTP client dependency.
"""
from __future__ import annotations
import json
import re
import urllib.error
import urllib.request
from typing import Iterator
from .base import Model, ModelResponse, StreamEvent, ToolCall

# Some Ollama models advertise "tools" in their capabilities but don't
# reliably populate the native message.tool_calls field -- in testing,
# qwen2.5-coder:7b just prints the call as JSON text instead (fenced in
# ```json, or bare), while llama3.2:3b uses native tool_calls correctly
# for the same request. _extract_fallback_tool_calls() recovers a real
# call from that text so the Generator can still act on it, instead of
# silently treating "the model printed a tool call as prose" as "the
# model is done."
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _as_tool_call_dict(obj: object, known_names: set[str]) -> dict | None:
    if not isinstance(obj, dict):
        return None
    name, arguments = obj.get("name"), obj.get("arguments")
    if isinstance(name, str) and name in known_names and isinstance(arguments, dict):
        return {"name": name, "arguments": arguments}
    return None


def _scan_bare_json_objects(text: str) -> list[tuple[int, int, dict]]:
    """Find every syntactically balanced, top-level {...} substring in
    text (skipping braces inside string literals), left to right."""
    found = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            start, depth, in_string, escape, j = i, 0, False, False, i
            while j < n:
                c = text[j]
                if in_string:
                    if escape:
                        escape = False
                    elif c == "\\":
                        escape = True
                    elif c == '"':
                        in_string = False
                elif c == '"':
                    in_string = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[start:j + 1])
                        except json.JSONDecodeError:
                            obj = None
                        if isinstance(obj, dict):
                            found.append((start, j + 1, obj))
                        i = j
                        break
                j += 1
            else:
                break
        i += 1
    return found


def _extract_fallback_tool_calls(text: str, known_names: set[str]) -> tuple[list[ToolCall], str]:
    fenced: list[dict] = []

    def take_fence(m: re.Match) -> str:
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            return m.group(0)
        call = _as_tool_call_dict(obj, known_names)
        if call is None:
            return m.group(0)
        fenced.append(call)
        return ""

    remaining = _JSON_FENCE_RE.sub(take_fence, text)

    bare: list[dict] = []
    for start, end, obj in reversed(_scan_bare_json_objects(remaining)):
        call = _as_tool_call_dict(obj, known_names)
        if call is not None:
            bare.append(call)
            remaining = remaining[:start] + remaining[end:]
    bare.reverse()

    calls = [ToolCall(id=f"call_{i}", name=c["name"], arguments=c["arguments"]) for i, c in enumerate(fenced + bare)]
    return calls, remaining.strip()


class OllamaModel(Model):
    def __init__(self, model_name: str, base_url: str, timeout: float = 120.0):
        self.name = model_name
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        body: dict = {
            "model": self.name,
            "messages": self._to_ollama_messages(messages),
            "stream": False,
        }
        known_tool_names: set[str] = set()
        if tools:
            known_tool_names = {t["name"] for t in tools}
            body["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"], "description": t["description"], "parameters": t["input_schema"],
                }}
                for t in tools
            ]

        data = self._post("/api/chat", body)
        message = data.get("message", {})
        text = message.get("content", "") or ""
        tool_calls = [
            ToolCall(id=f"call_{i}", name=tc["function"]["name"], arguments=tc["function"].get("arguments") or {})
            for i, tc in enumerate(message.get("tool_calls") or [])
        ]

        if not tool_calls and known_tool_names:
            tool_calls, text = _extract_fallback_tool_calls(text, known_tool_names)

        stop_reason = "tool_use" if tool_calls else (data.get("done_reason") or "end_turn")
        return ModelResponse(
            text=text, tool_calls=tool_calls, stop_reason=stop_reason,
            input_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"),
        )

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        body = {"model": self.name, "messages": self._to_ollama_messages(messages), "stream": True}
        url = f"{self.base_url}/api/chat"
        request = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                # Ollama's streaming format is newline-delimited JSON:
                # one {"message": {"content": "..."}, "done": false} object
                # per line, with a final done: true object at the end.
                for raw_line in response:
                    line = raw_line.strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    delta = chunk.get("message", {}).get("content", "")
                    if delta:
                        yield delta
                    if chunk.get("done"):
                        break
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Could not reach Ollama at {url}: {e}. "
                "Is the host machine's Ollama (or its usage_proxy) actually running and reachable?"
            ) from e

    def chat_stream_tools(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[StreamEvent]:
        """Streaming with tools. Native tool calls arrive on a chunk's
        message.tool_calls. For models that print a call as JSON text
        instead (see _extract_fallback_tool_calls), text that *starts* like
        JSON or a code fence is held back until the end, so it is never
        streamed out (and never spoken aloud) before being recognised."""
        body: dict = {"model": self.name, "messages": self._to_ollama_messages(messages), "stream": True}
        known_tool_names: set[str] = set()
        if tools:
            known_tool_names = {t["name"] for t in tools}
            body["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"], "description": t["description"], "parameters": t["input_schema"],
                }}
                for t in tools
            ]

        url = f"{self.base_url}/api/chat"
        request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        text_parts: list[str] = []
        native_calls: list[dict] = []
        holding = False  # True once the reply looks like a JSON tool call
        emitted = 0      # characters of text already streamed out
        final: dict = {}
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    line = raw_line.strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    message = chunk.get("message", {})
                    native_calls.extend(message.get("tool_calls") or [])
                    delta = message.get("content", "")
                    if delta:
                        text_parts.append(delta)
                        so_far = "".join(text_parts)
                        if known_tool_names and not holding and so_far.lstrip()[:1] in ("{", "`"):
                            holding = True
                        if not holding and len(so_far) > emitted:
                            yield StreamEvent("text", text=so_far[emitted:])
                            emitted = len(so_far)
                    if chunk.get("done"):
                        final = chunk
                        break
        except urllib.error.URLError as e:
            raise RuntimeError(f"Could not reach Ollama at {url}: {e}. Is Ollama running?") from e

        text = "".join(text_parts)
        tool_calls = [
            ToolCall(id=f"call_{i}", name=tc["function"]["name"], arguments=tc["function"].get("arguments") or {})
            for i, tc in enumerate(native_calls)
        ]
        if not tool_calls and known_tool_names and holding:
            tool_calls, text = _extract_fallback_tool_calls(text, known_tool_names)
        if holding and len(text) > emitted:
            yield StreamEvent("text", text=text[emitted:])

        stop_reason = "tool_use" if tool_calls else (final.get("done_reason") or "end_turn")
        yield StreamEvent("done", response=ModelResponse(
            text=text, tool_calls=tool_calls, stop_reason=stop_reason,
            input_tokens=final.get("prompt_eval_count"), output_tokens=final.get("eval_count"),
        ))

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Could not reach Ollama at {url}: {e}. "
                "Is the host machine's Ollama (or its usage_proxy) actually running and reachable?"
            ) from e

    @staticmethod
    def _to_ollama_messages(messages: list[dict]) -> list[dict]:
        ollama_messages: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                ollama_messages.append({"role": "system", "content": m["content"]})

            elif m["role"] == "user":
                ollama_messages.append({"role": "user", "content": m["content"]})

            elif m["role"] == "assistant":
                entry: dict = {"role": "assistant", "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    entry["tool_calls"] = [
                        {"function": {"name": call.name, "arguments": call.arguments}}
                        for call in m["tool_calls"]
                    ]
                ollama_messages.append(entry)

            elif m["role"] == "tool":
                ollama_messages.append({"role": "tool", "content": m["content"]})
        return ollama_messages
