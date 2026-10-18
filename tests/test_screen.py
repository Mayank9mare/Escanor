import subprocess
from datetime import datetime, timedelta
import pytest
from escanor.assistant.screen import LookAtScreenTool, Screen
from escanor.models.anthropic_provider import AnthropicModel
from escanor.models.base import ModelResponse, ToolCall
from escanor.models.images import FOLLOW_UP_TEXT, MISSING_TEXT
from escanor.models.ollama_provider import OllamaModel
from escanor.models.openai_provider import OpenAIModel
from escanor.tools.base import EXTERNAL, ToolResult

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


def fake_runner(fail=False):
    calls = []

    def run(command):
        calls.append(command)
        if command[0] == "screencapture" and not fail:
            with open(command[-1], "wb") as f:
                f.write(JPEG)
        return subprocess.CompletedProcess(command, 1 if fail else 0, "", "not permitted" if fail else "")
    return run, calls


def test_capture_shrinks_and_keeps_newest(tmp_path):
    run, calls = fake_runner()
    times = iter(datetime(2026, 10, 18, 10, 0) + timedelta(seconds=i) for i in range(10))
    folder = tmp_path / "screens"
    screen = Screen(folder, keep=2, run=run, now=lambda: next(times))
    paths = [screen.capture() for _ in range(3)]
    assert calls[0][:2] == ["screencapture", "-x"] and calls[1][:2] == ["sips", "-Z"]
    assert sorted(p.name for p in folder.iterdir()) == [paths[1].name, paths[2].name]


def test_capture_explains_missing_permission(tmp_path):
    run, _ = fake_runner(fail=True)
    with pytest.raises(RuntimeError, match="Screen Recording"):
        Screen(tmp_path, run=run).capture()


def test_tool_asks_first_attaches_image_and_names_the_model(tmp_path):
    run, _ = fake_runner()
    tool = LookAtScreenTool(Screen(tmp_path, run=run), lambda: "gemini:gemini-3.5-flash-lite")
    assert tool.side_effect == EXTERNAL and tool.untrusted_output
    assert tool.describe_call({}) == "capture your screen and send it to gemini:gemini-3.5-flash-lite"
    result = tool.run()
    assert result.ok and len(result.data["images"]) == 1
    assert LookAtScreenTool(Screen(tmp_path, run=fake_runner(fail=True)[0])).run().ok is False


def _history(tmp_path, store):
    image = tmp_path / "shot.jpg"
    image.write_bytes(JPEG)
    session = store.create()
    session.append_user("what's on my screen?")
    call = ToolCall("c1", "look_at_screen", {})
    session.append_assistant(ModelResponse(tool_calls=[call]), "x:y")
    session.append_tool_result(call, ToolResult(ok=True, output="Screenshot attached.", data={"images": [str(image)]}))
    session.append_assistant(ModelResponse(text="An error dialog."), "x:y")
    return session, image


def test_session_stores_image_paths_not_bytes(tmp_path, store):
    session, image = _history(tmp_path, store)
    assert store.load(session.id).messages[2]["images"] == [str(image)]


def test_claude_gets_image_inside_tool_result(tmp_path, store):
    session, _ = _history(tmp_path, store)
    out = AnthropicModel("claude-haiku-5-5", api_key="k")._to_claude_messages(session.messages)
    content = out[2]["content"][0]["content"]
    assert content[0] == {"type": "text", "text": "Screenshot attached."}
    assert content[1]["type"] == "image" and content[1]["source"]["media_type"] == "image/jpeg"


def test_claude_notes_missing_image(tmp_path, store):
    session, image = _history(tmp_path, store)
    image.unlink()
    out = AnthropicModel("claude-haiku-5-5", api_key="k")._to_claude_messages(session.messages)
    assert out[2]["content"][0]["content"][1] == {"type": "text", "text": MISSING_TEXT}


def test_openai_gets_image_in_user_message_after_tool_results(tmp_path, store):
    session, _ = _history(tmp_path, store)
    out = OpenAIModel._to_openai_messages(session.messages)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "user", "assistant"]
    follow_up = out[3]["content"]
    assert follow_up[0]["text"] == FOLLOW_UP_TEXT and follow_up[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_openai_unchanged_without_images(store):
    session = store.create()
    session.append_user("hi")
    assert OpenAIModel._to_openai_messages(session.messages) == [{"role": "user", "content": "hi"}]


def test_ollama_gets_images_on_user_message(tmp_path, store):
    session, _ = _history(tmp_path, store)
    out = OllamaModel._to_ollama_messages(session.messages)
    assert out[3]["role"] == "user" and out[3]["images"] and out[3]["content"] == FOLLOW_UP_TEXT
