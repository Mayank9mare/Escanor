import base64
import pytest
from fastapi.testclient import TestClient
from escanor import config
from escanor.app import build_app, build_model
from escanor.models.base import ModelResponse, ToolCall
from escanor.models.mock_provider import MockModel
from escanor.server.app import create_server
from escanor.tools.base import EXTERNAL, Tool, ToolResult
from escanor.voice.audio_out import AudioOut, NullPlayer
from escanor.voice.listen import to_wav


class FakeSpeech:
    def __init__(self, transcript="what time is it"):
        self.transcript = transcript

    def transcribe(self, wav: bytes) -> str:
        return self.transcript

    def synthesize(self, text: str) -> bytes:
        return to_wav([])


class SendTool(Tool):
    name = "send_message"
    description = "send"
    input_schema = {"type": "object"}
    side_effect = EXTERNAL

    def __init__(self):
        self.sent = 0

    def run(self, **kwargs):
        self.sent += 1
        return ToolResult(ok=True, output="sent")


def _client(store, script, speech=None, audio=None):
    settings = config.load_settings()
    settings["brain"]["embedder"] = "none"
    settings["skills"]["dirs"] = []
    model, spec = build_model("mock", settings)
    app = build_app(settings, model, spec)
    app.agent.set_model(MockModel(script=script), "mock:scripted")
    tool = SendTool()
    app.agent.tools.add(tool)
    return TestClient(create_server(app, store, speech, audio)), app, tool


def _until(ws, kind):
    seen = []
    while True:
        message = ws.receive_json()
        seen.append(message)
        if message["type"] == kind:
            return message, seen


def test_should_serve_index_and_models(store):
    client, app, _ = _client(store, [])
    assert "Escanor" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/models").json()["current"] == "mock:scripted"


def test_should_stream_reply_when_text_sent(store):
    client, _, _ = _client(store, [ModelResponse(text="Hello there friend")])
    with client.websocket_connect("/ws?conversation=new") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["speech"] == "off"
        ws.send_json({"type": "text", "text": "hi"})
        done, seen = _until(ws, "done")
        assert seen[0] == {"type": "user", "text": "hi"}
        assert "".join(m["text"] for m in seen if m["type"] == "text") == "Hello there friend"


def test_should_transcribe_audio_and_speak_reply_locally(store):
    player = NullPlayer()
    speech = FakeSpeech("what time is it")
    audio = AudioOut(speech.synthesize, player)
    client, _, _ = _client(store, [ModelResponse(text="It is half past nine in the evening.")], speech, audio)
    with client.websocket_connect("/ws?conversation=new&speech=local") as ws:
        ws.receive_json()
        ws.send_json({"type": "audio", "data": base64.b64encode(to_wav([])).decode()})
        _, seen = _until(ws, "done")
        assert {"type": "user", "text": "what time is it"} in seen


def test_should_ask_in_page_and_run_tool_when_allowed(store):
    script = [ModelResponse(tool_calls=[ToolCall("c1", "send_message", {"to": "mom"})]), ModelResponse(text="Sent it.")]
    client, _, tool = _client(store, script)
    with client.websocket_connect("/ws?conversation=new") as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "text mom"})
        request, _ = _until(ws, "confirm_request")
        assert "send_message" in request["summary"]
        ws.send_json({"type": "confirm", "id": request["id"], "approved": True})
        _until(ws, "done")
    assert tool.sent == 1


def test_should_not_run_tool_when_denied(store):
    script = [ModelResponse(tool_calls=[ToolCall("c1", "send_message", {})]), ModelResponse(text="Okay, not sending.")]
    client, _, tool = _client(store, script)
    with client.websocket_connect("/ws?conversation=new") as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "text mom"})
        request, _ = _until(ws, "confirm_request")
        ws.send_json({"type": "confirm", "id": request["id"], "approved": False})
        finished, _ = _until(ws, "tool_finished")
        assert finished["ok"] is False
        _until(ws, "done")
    assert tool.sent == 0


def test_should_report_interrupted_when_new_message_arrives_mid_confirmation(store):
    script = [ModelResponse(tool_calls=[ToolCall("c1", "send_message", {})]), ModelResponse(text="Second answer here.")]
    client, _, tool = _client(store, script)
    with client.websocket_connect("/ws?conversation=new") as ws:
        ws.receive_json()
        ws.send_json({"type": "text", "text": "text mom"})
        _until(ws, "confirm_request")
        ws.send_json({"type": "text", "text": "never mind, what's up?"})
        interrupted, _ = _until(ws, "done")
        assert interrupted["stop_reason"] == "interrupted"
        _, seen = _until(ws, "done")
        assert "Second answer here." in "".join(m.get("text", "") for m in seen if m["type"] == "text")
    assert tool.sent == 0


def test_should_switch_model_and_report_bad_names(store):
    client, app, _ = _client(store, [])
    with client.websocket_connect("/ws?conversation=new") as ws:
        ws.receive_json()
        ws.send_json({"type": "model", "name": "mock:other"})
        assert ws.receive_json() == {"type": "model_switched", "model": "mock:other"}
        ws.send_json({"type": "model", "name": "bogus"})
        assert ws.receive_json()["type"] == "error"
    assert app.agent.model_spec == "mock:other"


def test_should_resume_history_when_reconnecting(store):
    client, _, _ = _client(store, [ModelResponse(text="First reply text.")])
    with client.websocket_connect("/ws?conversation=new") as ws:
        conversation = ws.receive_json()["conversation"]
        ws.send_json({"type": "text", "text": "hello"})
        _until(ws, "done")
    with client.websocket_connect(f"/ws?conversation={conversation}") as ws:
        history = ws.receive_json()["history"]
    assert history == [{"role": "user", "text": "hello"}, {"role": "assistant", "text": "First reply text."}]


def test_should_refuse_audio_when_server_is_text_only(store):
    client, _, _ = _client(store, [])
    with client.websocket_connect("/ws?conversation=new") as ws:
        ws.receive_json()
        ws.send_json({"type": "audio", "data": ""})
        assert ws.receive_json()["type"] == "error"


def test_should_send_lip_sync_envelope_when_speaking_locally(store):
    speech = FakeSpeech("hi")
    audio = AudioOut(speech.synthesize, NullPlayer())
    client, _, _ = _client(store, [ModelResponse(text="Behold the sun, at its zenith today.")], speech, audio)
    with client.websocket_connect("/ws?conversation=new&speech=local") as ws:
        ws.receive_json()
        ws.send_json({"type": "audio", "data": base64.b64encode(to_wav([])).decode()})
        envelope, _ = _until(ws, "envelope")
        assert envelope["step"] == 0.04 and isinstance(envelope["values"], list)


def test_should_report_persona_style(store):
    client, _, _ = _client(store, [])
    assert client.get("/api/persona").json() == {"style": "escanor"}


# ---------- B22: cross-site protection + menubar API ----------

from escanor.server.app import origin_allowed  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402


@pytest.mark.parametrize("origin,host,ok", [
    (None, "127.0.0.1:8770", True),                      # menubar / curl
    ("http://127.0.0.1:8770", "127.0.0.1:8770", True),   # Escanor's own page
    ("http://localhost:8770", "localhost:8770", True),
    ("https://evil.example", "127.0.0.1:8770", False),
    ("http://127.0.0.1:9999", "127.0.0.1:8770", False),  # another local app's page
    ("http://evil.localhost.example:8770", "evil.localhost.example:8770", False),
])
def test_origin_rules(origin, host, ok):
    assert origin_allowed(origin, host) is ok


def test_should_refuse_websocket_from_another_site(store):
    client, _, _ = _client(store, [])
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_should_refuse_cross_site_control_requests(store):
    client, app, _ = _client(store, [])
    assert client.post("/api/model?name=mock:x", headers={"origin": "https://evil.example"}).status_code == 403
    assert app.agent.model_spec == "mock:scripted"


def test_menubar_status_and_controls(store):
    client, app, _ = _client(store, [])
    status = client.get("/api/status").json()
    assert status["state"] == "idle" and status["model"] == "mock:scripted"
    assert client.post("/api/model?name=mock:other").json() == {"ok": True, "model": "mock:other"}
    assert client.post("/api/model?name=bogus").status_code == 400
    assert client.post("/api/interrupt").json() == {"ok": True}
    assert client.post("/api/quiet?minutes=30").status_code == 409  # proactive off in tests
