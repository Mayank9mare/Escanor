"""Long conversations are summed up so requests stay small (free tiers)."""
from escanor.core.agent import Agent
from escanor.core.compaction import Compactor, estimate_tokens
from escanor.core.events import TurnDone
from escanor.models.base import Model, ModelResponse, ToolCall
from escanor.models.mock_provider import MockModel
from escanor.server.app import _history
from escanor.tools.base import ToolResult


class Summariser(MockModel):
    def __init__(self, text="User plans Japan in April; budget 2.5 lakhs.", fail=False):
        super().__init__()
        self.text, self.fail, self.prompts = text, fail, []

    def chat(self, messages, tools=None):
        self.prompts.append(messages[-1]["content"])
        if self.fail:
            raise RuntimeError("429 rate limit")
        return ModelResponse(text=self.text)


def chat(session, turns, words=120):
    for i in range(turns):
        session.append_user(f"question {i} " + "word " * words)
        session.append_assistant(ModelResponse(text=f"answer {i} " + "word " * words), "mock:m")


def test_cut_keeps_recent_turns_and_starts_at_a_user_message(store):
    session = store.create()
    chat(session, 3)
    session.append_assistant(ModelResponse(tool_calls=[ToolCall("t1", "web_search", {"q": "x"})]), "mock:m")
    session.append_tool_result(ToolCall("t1", "web_search", {}), ToolResult(ok=True, output="found " * 200))
    _, live = session.live()
    cut = Compactor(max_tokens=10, keep_tokens=300).cut_point(live)
    assert live[cut]["role"] == "user" and 0 < cut < len(live)


def test_compacts_over_budget_and_model_sees_summary_plus_recent(store):
    session = store.create()
    chat(session, 12)
    before = estimate_tokens(session.messages)
    summariser = Summariser()
    assert Compactor(max_tokens=1000, keep_tokens=300).maybe_compact(session, summariser)
    summary, live = session.live()
    assert summary.startswith("User plans Japan") and estimate_tokens(live) < before / 2
    assert live[0]["role"] == "user"
    assert "question 0" in summariser.prompts[0]
    reloaded = store.load(session.id)                      # persisted
    assert reloaded.live()[0] == summary
    assert [h["text"][:10] for h in _history(reloaded)][0] == "question 0"   # the HUD still shows everything


def test_summary_rolls_and_small_or_failing_cases_change_nothing(store):
    session = store.create()
    chat(session, 2, words=5)
    assert not Compactor(max_tokens=1000).maybe_compact(session, Summariser())
    chat(session, 12)
    assert not Compactor(max_tokens=1000, keep_tokens=300).maybe_compact(session, Summariser(fail=True))
    assert session.live()[0] == ""
    compactor = Compactor(max_tokens=1000, keep_tokens=300)
    compactor.maybe_compact(session, Summariser("first summary"))
    chat(session, 12)
    second = Summariser("second summary")
    compactor.maybe_compact(session, second)
    assert "first summary" in second.prompts[0] and session.live()[0] == "second summary"
    assert [m["role"] for m in session.messages].count("summary") == 2


async def test_agent_compacts_before_the_turn_and_sends_the_summary(store):
    session = store.create()
    chat(session, 12)
    sent = []

    class Recorder(Summariser):
        def chat_stream_tools(self, messages, tools=None):
            sent.append(messages)
            self._script = [ModelResponse(text="ok")]
            return super().chat_stream_tools(messages, tools)

    agent = Agent(Recorder("Earlier: user asked twelve questions."), "mock:m")
    agent.compactor = Compactor(max_tokens=1000, keep_tokens=300)
    events = [e async for e in agent.run_turn(session, "and now?")]
    assert isinstance(events[-1], TurnDone)
    system, *rest = sent[0]
    assert "Earlier: user asked twelve questions." in system["content"]
    assert all(m["role"] != "summary" for m in rest) and rest[-1]["content"] == "and now?"
