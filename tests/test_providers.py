"""Provider message conversion and streaming, with fake SDK clients (no network)."""
import io
import json
from types import SimpleNamespace as NS
import pytest
from escanor.models import ollama_provider
from escanor.models.anthropic_provider import AnthropicModel
from escanor.models.base import ToolCall
from escanor.models.openai_provider import OpenAIModel
from escanor.models.registry import get_model


# ---------- registry ----------

def test_should_raise_runtime_error_when_api_key_missing():
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        get_model("anthropic", "claude-haiku-5-5")


def test_should_pass_effort_to_claude_when_constructed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    model = get_model("anthropic", "claude-haiku-5-5", effort="low")
    assert model._inner.effort == "low"
    assert model._inner._request([{"role": "user", "content": "hi"}], None)["output_config"] == {"effort": "low"}


def test_should_raise_value_error_when_provider_unknown():
    with pytest.raises(ValueError):
        get_model("nope", "x")


# ---------- anthropic ----------

@pytest.fixture
def claude(monkeypatch):
    return AnthropicModel("claude-haiku-5-5", api_key="test-key")


def test_should_merge_parallel_tool_results_into_one_user_message(claude):
    calls = [ToolCall("t1", "a", {}), ToolCall("t2", "b", {})]
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": calls, "model": "x"},
        {"role": "tool", "tool_call_id": "t1", "content": "one"},
        {"role": "tool", "tool_call_id": "t2", "content": "two", "is_error": True},
    ]
    out = claude._to_claude_messages(messages)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in out[2]["content"]] == ["t1", "t2"]
    assert out[2]["content"][1]["is_error"] is True
    assert "is_error" not in out[2]["content"][0]


def test_should_replay_raw_blocks_when_turn_came_from_same_model(claude):
    raw = {"provider": "anthropic", "model": "claude-haiku-5-5",
           "content": [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "hi"}]}
    out = claude._to_claude_messages([{"role": "user", "content": "q"},
                                      {"role": "assistant", "content": "hi", "raw": raw}])
    assert out[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}


def test_should_use_generic_shape_when_turn_came_from_other_model(claude):
    raw = {"provider": "anthropic", "model": "claude-sonnet-5-5",
           "content": [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "hi"}]}
    out = claude._to_claude_messages([{"role": "user", "content": "q"},
                                      {"role": "assistant", "content": "hi", "raw": raw}])
    assert out[1]["content"] == [{"type": "text", "text": "hi"}]


def test_should_use_placeholder_when_assistant_turn_empty(claude):
    out = claude._to_claude_messages([{"role": "user", "content": "q"}, {"role": "assistant", "content": ""}])
    assert out[1]["content"] == [{"type": "text", "text": "(no reply)"}]


def test_should_keep_thinking_and_tool_use_in_raw_when_parsing_reply(claude):
    message = NS(
        content=[
            NS(type="thinking", thinking="", signature="sig"),
            NS(type="text", text="Let me check."),
            NS(type="tool_use", id="tu1", name="get_time", input={"tz": "IST"}),
        ],
        stop_reason="tool_use", usage=NS(input_tokens=10, output_tokens=5),
    )
    response = claude._from_message(message)
    assert response.text == "Let me check."
    assert response.tool_calls == [ToolCall("tu1", "get_time", {"tz": "IST"})]
    assert [b["type"] for b in response.raw["content"]] == ["thinking", "text", "tool_use"]
    assert response.raw["model"] == "claude-haiku-5-5"
    assert (response.input_tokens, response.output_tokens) == (10, 5)


def test_should_stream_text_then_done_when_claude_streams(claude):
    final = NS(content=[NS(type="text", text="Hello there")], stop_reason="end_turn",
               usage=NS(input_tokens=3, output_tokens=2))

    class FakeStream:
        text_stream = iter(["Hello", " there"])
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_final_message(self): return final

    captured = {}
    def fake_stream(**kwargs):
        captured.update(kwargs)
        return FakeStream()
    claude._client = NS(messages=NS(stream=fake_stream))
    events = list(claude.chat_stream_tools([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]))
    assert [e.text for e in events if e.kind == "text"] == ["Hello", " there"]
    assert events[-1].kind == "done" and events[-1].response.text == "Hello there"
    assert captured["system"] == "sys" and captured["cache_control"] == {"type": "ephemeral"}
    assert "tools" not in captured


def test_should_raise_runtime_error_when_claude_api_fails(claude):
    import anthropic

    def boom(**kwargs):
        raise anthropic.APIConnectionError(request=NS(method="POST", url="x", headers={}))
    claude._client = NS(messages=NS(stream=boom))
    with pytest.raises(RuntimeError, match="Anthropic request failed"):
        list(claude.chat_stream_tools([{"role": "user", "content": "hi"}]))


# ---------- openai / gemini ----------

def _chunk(content=None, tool_calls=None, finish=None):
    return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls), finish_reason=finish)])


def _fragment(index, id=None, name=None, arguments=None, extra=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=arguments), model_extra=extra or {})


def _openai_with_chunks(chunks):
    model = OpenAIModel("gpt-5", api_key="test-key")
    model._client = NS(chat=NS(completions=NS(create=lambda **kw: iter(chunks))))
    return model


def test_should_assemble_tool_call_fragments_and_keep_provider_extra():
    model = _openai_with_chunks([
        _chunk(content="Checking"),
        _chunk(tool_calls=[_fragment(0, id="c1", name="web_search", arguments='{"que',
                                     extra={"extra_content": {"google": {"thought_signature": "s"}}})]),
        _chunk(tool_calls=[_fragment(0, arguments='ry": "x"}')]),
        _chunk(tool_calls=[_fragment(1, id="c2", name="get_time", arguments="")]),
        NS(choices=[]),  # trailing usage-only chunk (Sarvam/Gemini style)
        _chunk(finish="tool_calls"),
    ])
    events = list(model.chat_stream_tools([{"role": "user", "content": "q"}], [
        {"name": "web_search", "description": "d", "input_schema": {"type": "object"}}]))
    response = events[-1].response
    assert [e.text for e in events if e.kind == "text"] == ["Checking"]
    assert response.tool_calls[0] == ToolCall("c1", "web_search", {"query": "x"},
                                              provider_extra={"extra_content": {"google": {"thought_signature": "s"}}})
    assert response.tool_calls[1].arguments == {}
    assert response.stop_reason == "tool_use"


def test_should_not_crash_when_streamed_arguments_are_invalid_json():
    model = _openai_with_chunks([_chunk(tool_calls=[_fragment(0, id="c1", name="t", arguments="{bad")])])
    response = list(model.chat_stream_tools([{"role": "user", "content": "q"}]))[-1].response
    assert response.tool_calls[0].arguments == {"_unparseable_arguments": "{bad"}


def test_should_report_finish_reason_when_no_tool_calls():
    model = _openai_with_chunks([_chunk(content="Hi"), _chunk(finish="stop")])
    response = list(model.chat_stream_tools([{"role": "user", "content": "q"}]))[-1].response
    assert (response.text, response.stop_reason, response.tool_calls) == ("Hi", "stop", [])


# ---------- ollama ----------

class _FakeHTTP(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _ollama_with_lines(monkeypatch, lines):
    payload = "\n".join(json.dumps(line) for line in lines).encode()
    monkeypatch.setattr(ollama_provider.urllib.request, "urlopen", lambda req, timeout: _FakeHTTP(payload))
    return ollama_provider.OllamaModel("qwen3:8b", base_url="http://x")


TOOLS = [{"name": "get_time", "description": "d", "input_schema": {"type": "object"}}]


def test_should_stream_text_when_ollama_replies_in_prose(monkeypatch):
    model = _ollama_with_lines(monkeypatch, [
        {"message": {"content": "It is "}}, {"message": {"content": "noon."}},
        {"message": {"content": ""}, "done": True, "done_reason": "stop", "prompt_eval_count": 4, "eval_count": 2},
    ])
    events = list(model.chat_stream_tools([{"role": "user", "content": "q"}], TOOLS))
    assert [e.text for e in events if e.kind == "text"] == ["It is ", "noon."]
    assert events[-1].response.stop_reason == "stop"
    assert events[-1].response.input_tokens == 4


def test_should_hold_back_json_and_extract_tool_call_when_model_prints_call(monkeypatch):
    model = _ollama_with_lines(monkeypatch, [
        {"message": {"content": '{"name": "get_time", '}},
        {"message": {"content": '"arguments": {}}'}},
        {"done": True},
    ])
    events = list(model.chat_stream_tools([{"role": "user", "content": "q"}], TOOLS))
    assert [e for e in events if e.kind == "text"] == []
    assert events[-1].response.tool_calls[0].name == "get_time"
    assert events[-1].response.stop_reason == "tool_use"


def test_should_release_held_text_when_json_is_not_a_tool_call(monkeypatch):
    model = _ollama_with_lines(monkeypatch, [{"message": {"content": "{not a call} but prose"}}, {"done": True}])
    events = list(model.chat_stream_tools([{"role": "user", "content": "q"}], TOOLS))
    assert "".join(e.text for e in events if e.kind == "text") == "{not a call} but prose"
    assert events[-1].response.tool_calls == []


def test_should_use_native_tool_calls_when_ollama_sends_them(monkeypatch):
    model = _ollama_with_lines(monkeypatch, [
        {"message": {"content": "", "tool_calls": [{"function": {"name": "get_time", "arguments": {"tz": "UTC"}}}]}},
        {"done": True},
    ])
    response = list(model.chat_stream_tools([{"role": "user", "content": "q"}], TOOLS))[-1].response
    assert response.tool_calls == [ToolCall("call_0", "get_time", {"tz": "UTC"})]


def test_should_raise_runtime_error_when_ollama_unreachable(monkeypatch):
    import urllib.error

    def refuse(req, timeout):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(ollama_provider.urllib.request, "urlopen", refuse)
    model = ollama_provider.OllamaModel("qwen3:8b", base_url="http://x")
    with pytest.raises(RuntimeError, match="Is Ollama running"):
        list(model.chat_stream_tools([{"role": "user", "content": "q"}]))
