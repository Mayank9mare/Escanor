"""escanor doctor: the per-model conformance checks, on scripted models."""
from escanor.doctor import FAIL, OK, WARN, model_checks, run
from escanor.models.base import ModelResponse, ToolCall
from escanor.models.mock_provider import MockModel
from escanor import config


def good_model():
    return MockModel(script=[
        ModelResponse(text="ready"),
        ModelResponse(tool_calls=[ToolCall("c1", "get_time", {})]),
        ModelResponse(text="It's 9:30."),
        ModelResponse(text="It's 9:30."),
    ])


def test_a_conformant_model_passes_all_four():
    checks = model_checks("m", good_model(), "mock:m")
    assert [c.name.split(": ")[1] for c in checks] == ["reply", "tool", "replay", "switch"]
    assert all(c.status == OK for c in checks)


def test_a_model_that_never_calls_tools_fails_and_stops():
    model = MockModel(script=[ModelResponse(text="ready"), ModelResponse(text="It's probably morning.")])
    checks = model_checks("m", model, "mock:m")
    assert [c.status for c in checks] == [OK, FAIL] and "no tool call" in checks[1].detail


def test_a_broken_model_reports_once():
    class Down(MockModel):
        def chat_stream_tools(self, messages, tools=None):
            raise RuntimeError("401 Incorrect API key")
    checks = model_checks("m", Down(), "mock:m")
    assert len(checks) == 1 and checks[0].status == FAIL and "401" in checks[0].detail


def test_run_without_models_lists_setup_and_exit_code(monkeypatch):
    settings = config.load_settings()
    settings["brain"]["embedder"] = "none"
    lines = []
    code = run(settings, build=None, models=False, echo=lines.append)
    text = "\n".join(lines)
    assert "API keys" in text and "server" in text and "Models" not in text
    assert code == (1 if f"{FAIL} " in text else 0)
