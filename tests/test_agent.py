import asyncio
import threading
import pytest
from escanor.core.agent import Agent, astream
from escanor.core.events import Error, TextDelta, ToolFinished, ToolStarted, TurnDone
from escanor.core.session import CANCELLED_RESULT
from escanor.models.base import Model, ModelResponse, StreamEvent, ToolCall
from escanor.models.mock_provider import MockModel
from escanor.tools.base import Tool, ToolResult
from escanor.tools.registry import ToolRegistry

SPEC = "mock:mock-model"


class EchoTool(Tool):
    name = "echo"
    description = "Echo the text back."
    input_schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def run(self, text: str) -> ToolResult:
        return ToolResult(ok=True, output=f"echo: {text}")


class BrokenTool(Tool):
    name = "broken"
    description = "Always fails."
    input_schema = {"type": "object"}

    def run(self) -> ToolResult:
        raise OSError("disk on fire")


async def _collect(agent, session, text):
    return [event async for event in agent.run_turn(session, text)]


async def test_should_stream_text_and_finish_when_no_tool_calls(store):
    agent = Agent(MockModel(script=[ModelResponse(text="Hello there friend", input_tokens=5, output_tokens=3)]), SPEC)
    session = store.create()
    events = await _collect(agent, session, "hi")
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hello there friend"
    assert events[-1] == TurnDone(stop_reason="end_turn", usage={"input_tokens": 5, "output_tokens": 3})
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert session.messages[1]["model"] == SPEC


async def test_should_run_tool_and_feed_result_back_when_model_calls_tool(store):
    call = ToolCall("c1", "echo", {"text": "ping"})
    model = MockModel(script=[
        ModelResponse(text="Calling.", tool_calls=[call], stop_reason="tool_use", input_tokens=1, output_tokens=1),
        ModelResponse(text="Got it.", input_tokens=2, output_tokens=2),
    ])
    agent = Agent(model, SPEC, tools=ToolRegistry([EchoTool()]))
    session = store.create()
    events = await _collect(agent, session, "go")
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("ToolStarted") < kinds.index("ToolFinished") < kinds.index("TurnDone")
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.result == ToolResult(ok=True, output="echo: ping")
    assert events[-1].usage == {"input_tokens": 3, "output_tokens": 3}
    second_request = model.calls[1]["messages"]
    assert second_request[-1] == {"role": "tool", "tool_call_id": "c1", "content": "echo: ping", "is_error": False}
    assert model.calls[0]["tools"][0]["name"] == "echo"


async def test_should_report_error_result_when_tool_raises(store):
    call = ToolCall("c1", "broken", {})
    model = MockModel(script=[ModelResponse(tool_calls=[call]), ModelResponse(text="It failed.")])
    agent = Agent(model, SPEC, tools=ToolRegistry([BrokenTool()]))
    events = await _collect(agent, store.create(), "go")
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.result.ok is False and "disk on fire" in finished.result.output
    assert isinstance(events[-1], TurnDone)


async def test_should_report_unknown_tool_when_model_invents_one(store):
    model = MockModel(script=[ModelResponse(tool_calls=[ToolCall("c1", "nope", {})]), ModelResponse(text="ok")])
    events = await _collect(Agent(model, SPEC), store.create(), "go")
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.result.output == "Unknown tool: nope"


async def test_should_stop_with_error_when_step_limit_reached(store):
    call = ToolCall("c", "echo", {"text": "again"})
    model = MockModel(script=[ModelResponse(tool_calls=[call]) for _ in range(3)])
    agent = Agent(model, SPEC, tools=ToolRegistry([EchoTool()]), max_steps=3)
    session = store.create()
    events = await _collect(agent, session, "loop")
    assert events[-1] == Error("Stopped after 3 tool steps without a final answer.")
    assert session.pending_tool_calls() == []


async def test_should_not_run_tools_when_reply_cut_off_by_max_tokens(store):
    call = ToolCall("c", "echo", {"text": "partial"})
    model = MockModel(script=[ModelResponse(text="Half", tool_calls=[call], stop_reason="max_tokens")])
    agent = Agent(model, SPEC, tools=ToolRegistry([EchoTool()]))
    session = store.create()
    events = await _collect(agent, session, "go")
    assert not any(isinstance(e, ToolStarted) for e in events)
    assert isinstance(events[-1], Error)
    assert session.messages[-1]["tool_calls"] == [] and session.pending_tool_calls() == []


async def test_should_end_turn_without_running_tools_when_model_refuses(store):
    call = ToolCall("c", "echo", {"text": "x"})
    model = MockModel(script=[ModelResponse(text="", tool_calls=[call], stop_reason="refusal")])
    agent = Agent(model, SPEC, tools=ToolRegistry([EchoTool()]))
    session = store.create()
    events = await _collect(agent, session, "go")
    assert events[-1].stop_reason == "refusal"
    assert not any(isinstance(e, ToolStarted) for e in events)


async def test_should_yield_error_when_provider_fails(store):
    class Failing(Model):
        name = "failing"
        def chat(self, messages, tools=None):
            raise RuntimeError("rate limited")
    events = await _collect(Agent(Failing(), SPEC), store.create(), "hi")
    assert events == [Error("rate limited")]


async def test_should_repair_session_next_turn_when_previous_turn_cancelled(store):
    call = ToolCall("c1", "slow", {})
    started = threading.Event()
    release = threading.Event()

    class SlowTool(Tool):
        name = "slow"
        description = "slow"
        input_schema = {"type": "object"}
        def run(self):
            started.set()
            release.wait(5)
            return ToolResult(ok=True, output="late")

    model = MockModel(script=[ModelResponse(tool_calls=[call]), ModelResponse(text="fresh answer")])
    agent = Agent(model, SPEC, tools=ToolRegistry([SlowTool()]))
    session = store.create()
    task = asyncio.ensure_future(_collect(agent, session, "first"))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert session.pending_tool_calls() == [call]

    events = await _collect(agent, session, "second")
    assert isinstance(events[-1], TurnDone)
    tool_messages = [m for m in session.messages if m["role"] == "tool"]
    assert tool_messages == [{"role": "tool", "tool_call_id": "c1", "content": CANCELLED_RESULT, "is_error": True}]


async def test_should_collapse_history_when_model_switched_between_turns(store):
    call = ToolCall("c1", "echo", {"text": "a"})
    first = MockModel(script=[ModelResponse(tool_calls=[call]), ModelResponse(text="done")])
    agent = Agent(first, "mock:first", tools=ToolRegistry([EchoTool()]))
    session = store.create()
    await _collect(agent, session, "one")
    second = MockModel(script=[ModelResponse(text="hi from second")], name="second")
    agent.set_model(second, "other:second")  # a different provider
    await _collect(agent, session, "two")
    sent = second.calls[0]["messages"]
    assert not any(m["role"] == "tool" for m in sent)
    assert not any(m.get("tool_calls") for m in sent)


async def test_should_close_iterator_when_consumer_stops_early():
    closed = threading.Event()

    def numbers():
        try:
            for i in range(1000):
                yield i
        finally:
            closed.set()

    async for value in astream(numbers):
        if value == 2:
            break
    assert await asyncio.to_thread(closed.wait, 5)


async def test_should_propagate_iterator_exception_to_async_side():
    def failing():
        yield 1
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        async for _ in astream(failing):
            pass


async def test_should_stream_events_through_instrumented_wrapper():
    from escanor.models.registry import get_model
    model = get_model("mock", "mock-model")
    events = list(model.chat_stream_tools([{"role": "user", "content": "a b c"}]))
    assert len([e for e in events if e.kind == "text"]) > 1  # word-by-word, not one blob
    assert isinstance(events[-1], StreamEvent) and events[-1].kind == "done"
