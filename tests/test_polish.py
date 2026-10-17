import plistlib
import shutil
import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import pytest
from escanor import telemetry
from escanor.core.agent import Agent
from escanor.core.costs import DailyBudget, cost, start_of_local_day_utc
from escanor.core.events import Error, ModelSwitched, ToolStarted, TurnDone
from escanor.core.router import Router
from escanor.models.base import ModelResponse, ToolCall
from escanor.models.instrumented import _request_summary
from escanor.models.mock_provider import MockModel
from escanor.models.openai_provider import OpenAIModel
from escanor.ops import CASES, EvalCase, backup_brain, run_eval, service_plist
from escanor.tools.base import Tool, ToolResult
from escanor.tools.registry import ToolRegistry


# ---------- costs & budget ----------

def test_cost_uses_prices_and_overrides():
    rows = [{"provider": "anthropic", "model": "claude-haiku-5-5", "input_tokens": 1_000_000, "output_tokens": 200_000},
            {"provider": "openai", "model": "gpt-5", "input_tokens": 1000, "output_tokens": 1000}]
    total, annotated = cost(rows)
    assert total == pytest.approx(0.20) and annotated[1]["usd"] is None
    total, _ = cost(rows, {"gpt-5": [1.0, 10.0]})
    assert total == pytest.approx(0.20 + 0.011)


def test_start_of_day_is_utc_iso():
    now = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
    assert start_of_local_day_utc(now).endswith("+00:00")


async def test_should_stop_before_calling_model_when_budget_used(store):
    spent = [{"provider": "anthropic", "model": "claude-sonnet-5-5", "input_tokens": 1_000_000, "output_tokens": 0}]
    model = MockModel(script=[ModelResponse(text="never")])
    agent = Agent(model, "mock:m")
    agent.budget = DailyBudget(1.0, lambda since: spent)
    events = [e async for e in agent.run_turn(store.create(), "hi")]
    assert isinstance(events[-1], Error) and "budget" in events[-1].message
    assert model.calls == []


def test_budget_off_when_zero():
    assert DailyBudget(0, lambda since: 1 / 0).check() is None


def test_usage_since_counts_only_ok_calls(tmp_path):
    store = telemetry.TelemetryStore(tmp_path / "t.db")
    for status in ("ok", "error"):
        store.insert_call(trace_id="t", span_id=status, component="c", role="r", provider="anthropic",
                          model="claude-haiku-5-5", started_at="2026-10-17T10:00:00+00:00", duration_ms=10,
                          status=status, error=None, streamed=True, input_tokens=100, output_tokens=5, meta=None)
    rows = store.usage_since("2026-10-17T00:00:00+00:00")
    assert rows[0]["calls"] == 1 and rows[0]["input_tokens"] == 100


def test_telemetry_keeps_only_newest_message():
    summary = _request_summary([{"role": "system", "content": "s"}, {"role": "user", "content": "secret note " * 500}])
    assert summary["messages"] == 2 and summary["last_role"] == "user" and len(summary["last"]) == 2000


def test_openai_stream_reports_usage_when_official_endpoint():
    model = OpenAIModel("gpt-5", api_key="k")
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        return iter([NS(choices=[NS(delta=NS(content="Hi", tool_calls=None), finish_reason="stop")], usage=None),
                     NS(choices=[], usage=NS(prompt_tokens=12, completion_tokens=3))])
    model._client = NS(chat=NS(completions=NS(create=create)))
    response = list(model.chat_stream_tools([{"role": "user", "content": "q"}]))[-1].response
    assert sent["stream_options"] == {"include_usage": True}
    assert (response.input_tokens, response.output_tokens) == (12, 3)
    assert OpenAIModel("x", api_key="k", base_url="https://gemini.test/")._stream_usage is False


# ---------- router ----------

def _router(strong):
    return Router(build=lambda name: (strong, "mock:strong"), escalate_to="sonnet", after_steps=2)


async def test_should_escalate_whole_turn_when_asked_to_think_hard_then_hand_back(store):
    weak = MockModel(script=[], name="weak")
    strong = MockModel(script=[ModelResponse(text="Careful answer.")], name="strong")
    agent = Agent(weak, "mock:weak")
    agent.router = _router(strong)
    events = [e async for e in agent.run_turn(store.create(), "think hard: is P = NP?")]
    assert events[0] == ModelSwitched("mock:strong") and events[-1] == ModelSwitched("mock:weak")
    assert weak.calls == [] and len(strong.calls) == 1
    assert agent.model_spec == "mock:weak"


class Noop(Tool):
    name = "noop"
    description = "noop"
    input_schema = {"type": "object"}

    def run(self):
        return ToolResult(ok=True, output="ok")


async def test_should_escalate_after_n_tool_steps_within_same_provider(store):
    call = ToolCall("c", "noop", {})
    weak = MockModel(script=[ModelResponse(tool_calls=[call]), ModelResponse(tool_calls=[call])], name="weak")
    strong = MockModel(script=[ModelResponse(text="Solved.")], name="strong")
    agent = Agent(weak, "mock:weak", tools=ToolRegistry([Noop()]))
    agent.router = _router(strong)
    events = [e async for e in agent.run_turn(store.create(), "do the multi-step thing")]
    switches = [e.model_spec for e in events if isinstance(e, ModelSwitched)]
    assert switches == ["mock:strong", "mock:weak"]
    assert len(weak.calls) == 2 and len(strong.calls) == 1 and any(isinstance(e, TurnDone) for e in events)


async def test_should_not_escalate_mid_turn_across_providers(store):
    call = ToolCall("c", "noop", {})
    weak = MockModel(script=[ModelResponse(tool_calls=[call]), ModelResponse(tool_calls=[call]),
                             ModelResponse(text="done")], name="weak")
    agent = Agent(weak, "ollama:weak", tools=ToolRegistry([Noop()]))
    agent.router = Router(build=lambda name: (MockModel(name="s"), "anthropic:claude-sonnet-5-5"), after_steps=2)
    events = [e async for e in agent.run_turn(store.create(), "do it")]
    assert not any(isinstance(e, ModelSwitched) for e in events)


def test_router_stays_put_when_escalation_model_unavailable():
    def missing(name):
        raise RuntimeError("no key")
    assert Router(build=missing).escalation() is None


# ---------- ops ----------

def test_service_plist_runs_serve_at_login(tmp_path):
    plist = plistlib.loads(service_plist("/x/escanor", tmp_path))
    assert plist["ProgramArguments"] == ["/x/escanor", "serve"] and plist["RunAtLoad"] is True
    assert plist["StandardErrorPath"].startswith(str(tmp_path))


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_backup_commits_changes_once(tmp_path):
    brain = tmp_path / "brain"
    brain.mkdir()
    (brain / "facts.md").write_text("- likes tea\n")
    assert "Backed up 1" in backup_brain(brain)
    assert backup_brain(brain) == "Nothing changed since the last backup."
    log = subprocess.run(["git", "log", "--oneline"], cwd=brain, capture_output=True, text=True).stdout
    assert "Brain backup" in log


async def test_eval_grades_tool_choice_without_running_tools(store):
    ran = []

    class Real(Tool):
        name = "get_time"
        description = "time"
        input_schema = {"type": "object"}

        def run(self):
            ran.append(1)
            return ToolResult(ok=True, output="now")

    model = MockModel(script=[ModelResponse(tool_calls=[ToolCall("c", "get_time", {})]), ModelResponse(text="Noon."),
                              ModelResponse(text="Hello!")])
    agent = Agent(model, "mock:m", tools=ToolRegistry([Real()]))
    results = await run_eval(agent, store, [EvalCase("what time is it?", ("get_time",)), EvalCase("hi", ())])
    assert [passed for _, _, passed in results] == [True, True]
    assert ran == []  # stubbed


def test_eval_cases_cover_each_capability():
    expected = {name for case in CASES for name in case.expect}
    assert {"get_time", "web_search", "remember_fact", "brain_search", "set_timer", "morning_briefing",
            "use_skill", "research_ingest"} <= expected
