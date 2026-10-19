"""Free-tier failover: a rate-limited model hands the step to the next one."""
import pytest
from escanor import config
from escanor.core.agent import Agent
from escanor.core.events import Error, ModelSwitched, TextDelta, TurnDone
from escanor.core.failover import Failover, is_limit_error
from escanor.core.session import SessionStore
from escanor.models.base import Model, ModelResponse, ToolCall
from escanor.models.mock_provider import MockModel
from escanor.models.registry import get_model
from escanor.tools.base import READ, Tool, ToolResult
from escanor.tools.registry import ToolRegistry


class Limited(Model):
    """Fails every call with `message` (like a free tier that's run out)."""

    def __init__(self, message="Error code: 429 - rate limit reached for requests per day"):
        self.message = message
        self.calls = 0

    def chat(self, messages, tools=None):
        raise AssertionError("not used")

    def chat_stream(self, messages):
        raise AssertionError("not used")

    def chat_stream_tools(self, messages, tools=None):
        self.calls += 1
        raise RuntimeError(f"OpenAI-compatible streaming request failed: {self.message}")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def builder(models):
    def build(name):
        if name not in models:
            raise RuntimeError(f"no key for {name}")
        return models[name], name
    return build


async def collect(agent, session, text):
    return [e async for e in agent.run_turn(session, text)]


@pytest.mark.parametrize("message,limited", [
    ("Error code: 429 - Rate limit reached", True),
    ("RESOURCE_EXHAUSTED: quota exceeded", True),
    ("503 The model is overloaded", True),
    ("400 Invalid tool schema", False),
    ("401 Incorrect API key", False),
])
def test_only_limit_errors_fail_over(message, limited):
    assert is_limit_error(message) is limited


def test_daily_quota_rests_longer_and_keyless_fallbacks_are_skipped():
    clock = Clock()
    failover = Failover(builder({"g:b": MockModel()}), ["g:none", "g:b"], cooldown_seconds=60,
                        daily_cooldown_seconds=3600, clock=clock)
    failover.mark("g:a", "429 too many requests")
    failover.mark("g:c", "quota exceeded: requests per day")
    clock.now = 61
    assert not failover.resting("g:a") and failover.resting("g:c")
    assert failover.next("g:a")[1] == "g:b"
    assert failover.next("g:b") is None          # the only fallback is the current model


async def test_rate_limited_step_moves_to_the_next_model(store):
    limited = Limited()
    backup = MockModel(script=[ModelResponse(text="Backup answering.")])
    agent = Agent(limited, "a:limited")
    agent.failover = Failover(builder({"b:backup": backup}), ["b:backup"])
    session = store.create()
    events = await collect(agent, session, "hi")
    assert ModelSwitched("b:backup") in events
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Backup answering."
    assert isinstance(events[-1], TurnDone) and not any(isinstance(e, Error) for e in events)
    assert agent.model_spec == "b:backup"         # stays there after the turn
    assert agent.failover.preferred == "a:limited"


async def test_returns_to_chosen_model_after_it_rests_and_skips_it_while_resting(store):
    clock = Clock()
    limited = Limited("429 rate limit")
    backup = MockModel(script=[ModelResponse(text="one"), ModelResponse(text="two")])
    home = MockModel(script=[ModelResponse(text="home again")])
    models = {"b:backup": backup, "a:limited": home}
    agent = Agent(limited, "a:limited")
    agent.failover = Failover(builder(models), ["b:backup"], cooldown_seconds=60, clock=clock)
    session = store.create()
    await collect(agent, session, "first")
    await collect(agent, session, "second")       # still resting: no retry on the limited model
    assert limited.calls == 1
    clock.now = 61
    events = await collect(agent, session, "third")
    assert events[0] == ModelSwitched("a:limited") and agent.failover.preferred is None
    assert "home again" in "".join(e.text for e in events if isinstance(e, TextDelta))


async def test_non_limit_errors_and_exhausted_chain_end_the_turn(store):
    agent = Agent(Limited("400 bad request"), "a:x")
    agent.failover = Failover(builder({"b:y": MockModel()}), ["b:y"])
    events = await collect(agent, store.create(), "hi")
    assert isinstance(events[-1], Error) and agent.model_spec == "a:x"
    agent = Agent(Limited(), "a:x")
    agent.failover = Failover(builder({"b:y": Limited()}), ["b:y"])
    events = await collect(agent, store.create(), "hi")
    assert isinstance(events[-1], Error) and "429" in events[-1].message


class Clock2(Tool):
    name = "get_time"
    description = "time"
    input_schema = {"type": "object"}
    side_effect = READ

    def run(self):
        return ToolResult(ok=True, output="09:30")


async def test_mid_turn_failover_collapses_the_other_providers_tool_calls(store):
    first = MockModel(script=[ModelResponse(tool_calls=[ToolCall("c1", "get_time", {})])])

    class ThenLimited(Model):
        def __init__(self):
            self.inner = first
            self.used = 0

        def chat(self, messages, tools=None):
            raise AssertionError

        def chat_stream(self, messages):
            raise AssertionError

        def chat_stream_tools(self, messages, tools=None):
            self.used += 1
            if self.used == 1:
                return self.inner.chat_stream_tools(messages, tools)
            raise RuntimeError("429 rate limit")

    seen = []

    class Backup(MockModel):
        def chat_stream_tools(self, messages, tools=None):
            seen.extend(messages)
            return super().chat_stream_tools(messages, tools)

    agent = Agent(ThenLimited(), "a:first", tools=ToolRegistry([Clock2()]))
    agent.failover = Failover(builder({"b:backup": Backup(script=[ModelResponse(text="It's half nine.")])}), ["b:backup"])
    events = await collect(agent, store.create(), "time?")
    assert isinstance(events[-1], TurnDone)
    assert not any(m.get("tool_calls") for m in seen if m["role"] == "assistant")   # replayed as text
    assert any("[used get_time" in str(m.get("content")) for m in seen)


def test_groq_and_openrouter_need_their_own_keys(monkeypatch):
    settings = config.load_settings()
    assert config.resolve_model("groq", settings) == ("groq", "openai/gpt-oss-120b")
    assert config.resolve_model("free", settings) == ("openrouter", "openrouter/free")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY.*console.groq.com"):
        get_model("groq", "openai/gpt-oss-120b")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    model = get_model("groq", "openai/gpt-oss-120b")
    assert str(model._inner._client.base_url).startswith("https://api.groq.com/openai/v1")


async def test_provider_failing_before_its_first_chunk_reports_instead_of_hanging():
    """B25: astream built the iterator outside its error handling."""
    import asyncio
    from escanor.core.agent import astream

    def boom():
        raise RuntimeError("connection refused")

    async def drain():
        return [x async for x in astream(boom)]
    with pytest.raises(RuntimeError, match="connection refused"):
        await asyncio.wait_for(drain(), 2)


def test_timeout_fails_over_with_a_short_rest():
    clock = Clock()
    failover = Failover(builder({}), [], cooldown_seconds=900, clock=clock)
    assert is_limit_error("OpenAI-compatible streaming request failed: Request timed out.")
    failover.mark("groq:x", "Request timed out.")
    clock.now = 121
    assert not failover.resting("groq:x")


def test_compatible_providers_give_up_after_15_seconds(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    client = get_model("groq", "openai/gpt-oss-120b")._inner._client
    assert client.timeout.read == 15.0 and client.max_retries == 0


@pytest.mark.parametrize("message,rest", [
    ("429 on tokens per minute (TPM): Limit 8000, Used 5808. Please try again in 7.3s.", 9.3),
    ("429 tokens per minute: try again in 1m2.5s", 64.5),
    ("429 rate limit: try again in 450ms", 2.45),
    ("429 requests per minute (RPM) exceeded", 60.0),
])
def test_per_minute_limits_rest_as_long_as_the_provider_says(message, rest):
    clock = Clock()
    failover = Failover(builder({}), [], cooldown_seconds=900, clock=clock)
    failover.mark("groq:x", message)
    clock.now = rest - 0.01
    assert failover.resting("groq:x")
    clock.now = rest + 0.01
    assert not failover.resting("groq:x")


async def test_request_after_mid_turn_switch_never_ends_on_the_models_turn(store):
    """B27: the folded tool history ended on an assistant message; Gemini refused it."""
    from escanor.core.prompts import CONTINUE
    first = MockModel(script=[ModelResponse(tool_calls=[ToolCall("c1", "get_time", {})])])
    calls = {"n": 0}

    class ThenLimited(Model):
        def chat(self, messages, tools=None):
            raise AssertionError

        def chat_stream(self, messages):
            raise AssertionError

        def chat_stream_tools(self, messages, tools=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return first.chat_stream_tools(messages, tools)
            raise RuntimeError("429 rate limit")

    sent = []

    class Strict(MockModel):
        def chat_stream_tools(self, messages, tools=None):
            sent.append(messages)
            if messages[-1]["role"] == "assistant":
                raise RuntimeError("400 Requests ending with a model turn are not supported.")
            return super().chat_stream_tools(messages, tools)

    agent = Agent(ThenLimited(), "a:first", tools=ToolRegistry([Clock2()]))
    agent.failover = Failover(builder({"b:strict": Strict(script=[ModelResponse(text="Half nine.")])}), ["b:strict"])
    session = store.create()
    events = await collect(agent, session, "time?")
    assert isinstance(events[-1], TurnDone)
    assert sent[0][-1] == {"role": "user", "content": CONTINUE}
    assert all(m.get("content") != CONTINUE for m in session.messages)    # never saved
