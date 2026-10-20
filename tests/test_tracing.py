"""Turns as traces: the receipt, the timeline, the views, and OTLP export."""
import asyncio
from escanor import telemetry
from escanor.core.agent import Agent
from escanor.core.failover import Failover
from escanor.export import OTLPExporter, to_otlp
from escanor.models.base import Model, ModelResponse, ToolCall
from escanor.models.instrumented import InstrumentedModel
from escanor.models.mock_provider import MockModel
from escanor.tools.base import READ, Tool, ToolResult
from escanor.tools.registry import ToolRegistry
from escanor.trace_view import format_timeline, format_turns
from escanor.voice.audio_out import AudioOut, NullPlayer
from escanor.voice.conversation import speak_turn


class Clock(Tool):
    name = "get_time"
    description = "time"
    input_schema = {"type": "object"}
    side_effect = READ

    def run(self):
        return ToolResult(ok=True, output="09:30")


def traced_agent(script, **kw):
    model = InstrumentedModel(MockModel(script=script), provider="mock", role="chat")
    return Agent(model, "mock:m", tools=ToolRegistry([Clock()]), **kw)


async def run(agent, session, text, **kw):
    return [e async for e in agent.run_turn(session, text, **kw)]


async def test_a_turn_is_one_trace_with_receipt_and_timeline(store):
    agent = traced_agent([ModelResponse(tool_calls=[ToolCall("c1", "get_time", {})], input_tokens=100, output_tokens=5),
                          ModelResponse(text="Half nine.", input_tokens=120, output_tokens=4)])
    await run(agent, store.create(), "what time is it?", source="hud", trace_id="t1")
    db = telemetry.get_store()
    turn = db.turn("t1")
    assert turn["source"] == "hud" and turn["steps"] == 2 and turn["tools"] == 1 and turn["outcome"] == "end_turn"
    assert turn["input_tokens"] == 220 and turn["output_tokens"] == 9 and turn["ttft_ms"] is not None
    kinds = [(i["kind"], i["name"]) for i in db.timeline("t1")]
    assert kinds == [("model", "mock/mock-model"), ("tool", "get_time"), ("model", "mock/mock-model")]
    assert db.timeline("t1")[0]["detail"]["asked_for"] == ["get_time"]


async def test_failover_and_interruption_are_recorded(store):
    class Limited(Model):
        name = "limited"

        def chat(self, messages, tools=None):
            raise AssertionError

        def chat_stream(self, messages):
            raise AssertionError

        def chat_stream_tools(self, messages, tools=None):
            raise RuntimeError("429 rate limit")

    backup = InstrumentedModel(MockModel(script=[ModelResponse(text="ok")]), provider="b", role="chat")
    agent = Agent(InstrumentedModel(Limited(), provider="a", role="chat"), "a:limited")
    agent.failover = Failover(lambda name: (backup, "b:backup"), ["b:backup"])
    await run(agent, store.create(), "hi", trace_id="t2")
    timeline = telemetry.get_store().timeline("t2")
    assert [i["kind"] for i in timeline] == ["model", "failover", "model"]
    assert timeline[0]["status"] == "error" and "a:limited → b:backup" in timeline[1]["name"]

    slow = traced_agent([ModelResponse(text="never")])

    async def stalled(*a, **k):
        await asyncio.sleep(10)
        yield None
    task = asyncio.ensure_future(run(slow, store.create(), "stop me", trace_id="t3"))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    turn = telemetry.get_store().turn("t3")
    assert turn is not None and turn["outcome"] in ("interrupted", "end_turn")


async def test_concurrent_turns_never_mix_traces(store):
    shared = InstrumentedModel(MockModel(script=[ModelResponse(text="a"), ModelResponse(text="b")]), "mock", "chat")
    a, b = shared.traced("ta", "hud"), shared.traced("tb", "hotkey")
    list(a.chat_stream_tools([{"role": "user", "content": "x"}]))
    list(b.chat_stream_tools([{"role": "user", "content": "y"}]))
    db = telemetry.get_store()
    assert len(db.timeline("ta")) == 1 and len(db.timeline("tb")) == 1 and shared._trace_id is None


async def test_spoken_turn_records_speech_and_first_sound(store):
    audio = AudioOut(lambda text: b"wav", NullPlayer())
    agent = traced_agent([ModelResponse(text="The sun is high. All is well.")])
    await speak_turn(agent, store.create(), "how are you?", audio, source="voice", trace_id="t4")
    await audio.wait_idle()
    db = telemetry.get_store()
    assert db.turn("t4")["first_audio_ms"] is not None
    assert [i["kind"] for i in db.timeline("t4")].count("tts") == 2
    await audio.close()


def test_views_show_slow_spans_and_handle_empty():
    turn = {"trace_id": "abc123def456", "started_at": "2026-10-21T10:00:00+00:00", "source": "hud", "model": "groq:x",
            "duration_ms": 41000.0, "ttft_ms": 39800.0, "first_audio_ms": None, "steps": 3, "tools": 2,
            "input_tokens": 9000, "output_tokens": 300, "usd": None, "outcome": "stop", "error": None,
            "text": "help me plan the first trip"}
    items = [{"kind": "model", "name": "groq/gpt-oss", "started_at": "2026-10-21T10:00:01+00:00", "duration_ms": 28400.0,
              "status": "ok", "detail": {"in": 4000, "out": 86, "asked_for": ["web_search"]}}]
    text = format_timeline(turn, items)
    assert "← slow" in text and "→ web_search" in text and "+  1.0s" in text
    assert "abc123de" in format_turns([turn]) and "No turns" in format_turns([])


def test_otlp_shape_and_privacy():
    telemetry.record_event("t5", "tool", "web_search", 800, detail={"arguments": '{"query": "my salary"}'})
    turn = {"trace_id": "t5", "started_at": "2026-10-21T10:00:00+00:00", "source": "hud", "model": "groq:x",
            "duration_ms": 1200.0, "outcome": "stop", "text": "what's my salary?"}
    body = to_otlp(turn, telemetry.get_store().timeline("t5"))
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root, child = spans
    assert len(root["traceId"]) == 32 and len(root["spanId"]) == 16 and child["parentSpanId"] == root["spanId"]
    flat = str(body)
    assert "my salary" not in flat                                     # private by default
    assert "my salary" in str(to_otlp(turn, telemetry.get_store().timeline("t5"), include_text=True))
    sent = []
    OTLPExporter("http://collector/v1/traces", {"Authorization": "env:NOPE"},
                 post=lambda url, data, headers: sent.append((url, headers)), background=False)(turn)
    assert sent[0][0] == "http://collector/v1/traces" and sent[0][1]["Content-Type"] == "application/json"


def test_traces_api_is_local_only(store):
    from escanor.server import remote
    from tests.test_server import _client
    telemetry.record_turn({"trace_id": "t6", "started_at": "2026-10-21T10:00:00+00:00", "source": "hud", "text": "hi",
                           "duration_ms": 10.0, "outcome": "stop"})
    client, _, _ = _client(store, [])
    assert client.get("/api/traces").json()[0]["trace_id"] == "t6"
    assert client.get("/api/traces/t6").json()["turn"]["text"] == "hi"
    assert "Traces" in client.get("/traces").text
    token = remote.new_token()
    phone, _, _ = _client(store, [], base_url="https://mac.tail1234.ts.net")
    phone.cookies.set(remote.COOKIE, token)
    assert phone.get("/api/traces").status_code == 403 and phone.get("/traces").status_code == 403
