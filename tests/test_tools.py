from datetime import datetime, timedelta, timezone
import pytest
from escanor.core.agent import Agent
from escanor.core.events import ModelSwitched, ToolFinished, TurnDone
from escanor.models.base import ModelResponse, ToolCall
from escanor.models.mock_provider import MockModel
from escanor.tools.base import Tool, ToolResult
from escanor.tools.registry import ToolRegistry
from escanor.tools.system_tools import EnableToolsTool, GetTimeTool, SwitchModelTool


class Named(Tool):
    input_schema = {"type": "object"}

    def __init__(self, name, group="core"):
        self.name, self.description, self.group = name, name, group

    def run(self, **kwargs):
        return ToolResult(ok=True, output=self.name)


# ---------- registry groups ----------

def test_should_offer_only_enabled_groups_when_listing_schemas():
    registry = ToolRegistry([Named("a"), Named("play", group="music")])
    assert [s["name"] for s in registry.schemas()] == ["a"]
    assert registry.enable("music") is True
    assert [s["name"] for s in registry.schemas()] == ["a", "play"]


def test_should_refuse_call_when_tool_group_disabled():
    registry = ToolRegistry([Named("play", group="music")])
    result = registry.dispatch(ToolCall("c", "play", {}))
    assert result.ok is False and "enable_tools" in result.output


def test_should_return_false_when_enabling_unknown_group():
    assert ToolRegistry([Named("a")]).enable("nope") is False


def test_should_disable_extra_groups_when_reset():
    registry = ToolRegistry([Named("play", group="music")])
    registry.enable("music")
    registry.reset()
    assert registry.enabled_groups() == {"core"}


def test_should_reject_duplicate_tool_names():
    with pytest.raises(ValueError, match="Duplicate"):
        ToolRegistry([Named("a"), Named("a")])


def test_should_list_groups_in_description_and_enable_on_run():
    registry = ToolRegistry([Named("a"), Named("play", group="music")])
    tool = EnableToolsTool(registry, {"music": "control YouTube Music"})
    registry.add(tool)
    assert "music: control YouTube Music" in tool.to_schema()["description"]
    assert tool.run(group="music").ok and "music" in registry.enabled_groups()
    assert tool.run(group="nope").ok is False


def test_should_say_no_groups_when_only_core_exists():
    registry = ToolRegistry([Named("a")])
    assert "No extra groups" in EnableToolsTool(registry).description


# ---------- get_time ----------

def test_should_format_local_time_when_asked():
    ist = timezone(timedelta(hours=5, minutes=30), "IST")
    tool = GetTimeTool(now=lambda: datetime(2026, 10, 17, 21, 5, tzinfo=ist))
    assert tool.run().output == "Saturday 17 October 2026, 21:05 (IST, 2026-10-17T21:05:00+05:30)"


# ---------- switch_model ----------

async def test_should_switch_model_only_after_turn_when_tool_called(store):
    first = MockModel(script=[
        ModelResponse(tool_calls=[ToolCall("c1", "switch_model", {"model": "other"})]),
        ModelResponse(text="Done, switching."),
    ])
    second = MockModel(script=[ModelResponse(text="hello from second")], name="second")
    registry = ToolRegistry()
    agent = Agent(first, "mock:first", tools=registry)

    def request(name):
        agent.request_model_switch(second, "mock:second")
        return "mock:second"
    registry.add(SwitchModelTool(request, {"other": "mock:second"}))

    session = store.create()
    events = [e async for e in agent.run_turn(session, "use the other model")]
    assert events[-1] == ModelSwitched("mock:second")
    assert any(isinstance(e, TurnDone) for e in events)
    assert len(first.calls) == 2  # the rest of the turn stayed on the first model
    assert agent.model_spec == "mock:second"

    await_events = [e async for e in agent.run_turn(session, "hi")]
    assert "hello from second" in "".join(getattr(e, "text", "") for e in await_events)


async def test_should_report_error_and_keep_model_when_switch_target_unknown(store):
    model = MockModel(script=[ModelResponse(tool_calls=[ToolCall("c1", "switch_model", {"model": "bogus"})]),
                              ModelResponse(text="Couldn't.")])
    registry = ToolRegistry()
    agent = Agent(model, "mock:first", tools=registry)

    def request(name):
        raise ValueError(f"Unknown model '{name}'.")
    registry.add(SwitchModelTool(request, {}))
    events = [e async for e in agent.run_turn(store.create(), "switch to bogus")]
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.result.ok is False and "Unknown model" in finished.result.output
    assert not any(isinstance(e, ModelSwitched) for e in events)
    assert agent.model_spec == "mock:first"
