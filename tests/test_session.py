from escanor.core.session import CANCELLED_RESULT
from escanor.models.base import ModelResponse, ToolCall
from escanor.tools.base import ToolResult

HAIKU = "anthropic:claude-haiku-5-5"
GEMINI = "gemini:gemini-3.6-flash"


def _tool_turn(session, model_spec=HAIKU, call_id="t1", raw=None):
    call = ToolCall(call_id, "web_search", {"query": "weather"})
    session.append_assistant(ModelResponse(text="Looking.", tool_calls=[call], raw=raw), model_spec)
    session.append_tool_result(call, ToolResult(ok=True, output="Sunny, 24C"))
    session.append_assistant(ModelResponse(text="It's sunny.", raw=raw), model_spec)


def test_should_round_trip_messages_and_tool_calls_when_reloaded(store):
    session = store.create()
    session.append_user("weather?")
    _tool_turn(session, raw={"provider": "anthropic", "model": "claude-haiku-5-5", "content": []})
    reloaded = store.load(session.id)
    assert reloaded.messages == session.messages
    assert isinstance(reloaded.messages[1]["tool_calls"][0], ToolCall)
    assert reloaded.title == "weather?"


def test_should_title_conversation_from_first_user_message_only(store):
    session = store.create()
    session.append_user("first question")
    session.append_user("second question")
    assert store.load(session.id).title == "first question"


def test_should_add_cancelled_result_when_tool_call_unanswered(store):
    session = store.create()
    session.append_user("go")
    calls = [ToolCall("a", "x", {}), ToolCall("b", "y", {})]
    session.append_assistant(ModelResponse(tool_calls=calls), HAIKU)
    session.append_tool_result(calls[0], ToolResult(ok=True, output="done"))
    session.repair()
    assert session.pending_tool_calls() == []
    last = store.load(session.id).messages[-1]
    assert last == {"role": "tool", "tool_call_id": "b", "content": CANCELLED_RESULT, "is_error": True}


def test_should_do_nothing_when_repairing_consistent_session(store):
    session = store.create()
    session.append_user("go")
    _tool_turn(session)
    before = list(session.messages)
    session.repair()
    assert session.messages == before


def test_should_collapse_other_models_tool_turns_when_switching(store):
    session = store.create()
    session.append_user("weather?")
    _tool_turn(session, raw={"provider": "anthropic", "model": "claude-haiku-5-5", "content": [{"type": "text"}]})
    changed = session.collapse_tool_history(GEMINI)
    assert changed
    roles = [m["role"] for m in session.messages]
    assert roles == ["user", "assistant"]  # tool turn + result + answer folded into one assistant message
    text = session.messages[1]["content"]
    assert "Looking." in text and "[used web_search" in text and "Sunny, 24C" in text and "It's sunny." in text
    assert session.messages[1]["raw"] is None
    assert store.load(session.id).messages == session.messages  # persisted


def test_should_leave_own_turns_untouched_when_switching_back(store):
    session = store.create()
    session.append_user("weather?")
    _tool_turn(session, model_spec=GEMINI)
    before = [dict(m) for m in session.messages]
    assert session.collapse_tool_history(GEMINI) is False
    assert session.messages == before


def test_should_not_change_plain_text_history_when_switching(store):
    session = store.create()
    session.append_user("hi")
    session.append_assistant(ModelResponse(text="hello"), HAIKU)
    assert session.collapse_tool_history(GEMINI) is False
