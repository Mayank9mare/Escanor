"""
server/app.py

`escanor serve`: the web UI and its WebSocket, on 127.0.0.1 only (a phone
reaches it through Tailscale, paired by token: server/remote.py).

One WebSocket per browser tab carries a conversation:

  browser -> server                        server -> browser
  {type: "text", text}                     {type: "hello", conversation, model, history, speech}
  {type: "audio", data: <base64 WAV>}      {type: "user", text}          (transcript)
  {type: "interrupt"}                      {type: "text", text}          (reply fragment)
  {type: "confirm", id, approved}          {type: "tool_started" / "tool_finished", ...}
  {type: "model", name}                    {type: "confirm_request", id, summary}
                                           {type: "done", stop_reason} / {type: "error", message}
                                           {type: "model_switched", model}
                                           {type: "speaking", on} / {type: "audio", data} / {type: "stop_audio"}
                                           {type: "listening", on}       (push-to-talk on the Mac)

Turns also start on the Mac itself (the menubar's hotkeys, server/desk.py);
their events go to every page showing that conversation (server/hub.py).

Speech output ("speech" query parameter) -- replies to voice input are
spoken; typed messages too with speak_typed=1:
  local   -- played on the Mac through the shared AudioOut (default)
  browser -- WAVs sent to the page (the default on a phone)
  off     -- text only

Sending a new message (or pressing the mic) while Escanor is talking
interrupts it: the running turn is cancelled and speech stops.
"""
from __future__ import annotations
import asyncio
import base64
import itertools
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
# Module-level on purpose: with `from __future__ import annotations`, FastAPI
# resolves the `ws: WebSocket` annotation by name from module globals. Imported
# inside create_server(), it was unresolvable and FastAPI treated `ws` as a
# required query parameter, rejecting every connection (B12).
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from ..app import Escanor, build_model
from ..core.persona import line
from ..core.events import Error, ModelSwitched, TextDelta, ToolFinished, ToolStarted, TurnDone
from ..core.session import Session, SessionStore
from ..models.base import ToolCall
from ..voice.audio_out import CONFIRM, AudioOut
from ..voice.conversation import VoiceConfirmer, speak_turn
from ..voice.envelope import STEP, wav_envelope
from ..voice.ptt import PushToTalk, default_mic, hear_once
from ..voice.web_player import WebSocketPlayer
from . import remote
from .desk import DeskConfirmer, display_text, explain_prompt
from .hub import Hub, Viewer
from .remote import origin_allowed  # noqa: F401 -- re-exported; tests and older imports use it here

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

# What a paired phone may use besides the page and the WebSocket. The Mac's
# own controls (mic, hotkeys, menubar) stay local-only.
REMOTE_API = {"/api/models", "/api/persona", "/api/voice"}


def _history(session: Session) -> list[dict]:
    out = []
    for m in session.messages:
        if m["role"] == "user":
            out.append({"role": "user", "text": display_text(m["content"])})
        elif m["role"] == "assistant" and m.get("content"):
            out.append({"role": "assistant", "text": m["content"]})
    return out


class WebConfirmer:
    """Shows a confirm dialog in the page (and says the question aloud)."""

    def __init__(self, send, audio: AudioOut | None):
        self._send = send
        self._audio = audio
        self._ids = itertools.count(1)
        self.pending: dict[int, asyncio.Future] = {}

    async def confirm(self, call: ToolCall, summary: str) -> bool:
        request_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        await self._send({"type": "confirm_request", "id": request_id, "summary": summary})
        if self._audio is not None:
            self._audio.say(line("confirm", summary=summary), CONFIRM)
        try:
            return await future
        finally:
            self.pending.pop(request_id, None)

    def answer(self, request_id: int, approved: bool) -> None:
        future = self.pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(bool(approved))


def _event_message(event) -> dict | None:
    if isinstance(event, TextDelta):
        return {"type": "text", "text": event.text}
    if isinstance(event, ToolStarted):
        return {"type": "tool_started", "name": event.call.name, "arguments": event.call.arguments}
    if isinstance(event, ToolFinished):
        return {"type": "tool_finished", "name": event.call.name, "ok": event.result.ok,
                "output": event.result.output[:500]}
    if isinstance(event, TurnDone):
        return {"type": "done", "stop_reason": event.stop_reason, "usage": event.usage}
    if isinstance(event, ModelSwitched):
        return {"type": "model_switched", "model": event.model_spec}
    if isinstance(event, Error):
        return {"type": "error", "message": event.message}
    return None


def create_server(app: Escanor, store: SessionStore, speech: Any | None, local_audio: AudioOut | None,
                  open_mic=default_mic):
    """`speech` has transcribe(wav)->str and synthesize(text)->wav (SpeechEngine),
    or None for a text-only server. `local_audio` speaks on this Mac.
    `open_mic` opens the Mac's microphone (for push-to-talk)."""
    web = FastAPI(title="Escanor")
    web.mount("/static", StaticFiles(directory=STATIC), name="static")
    hub = Hub(store)
    web.state.hub = hub

    def schedule(message: dict) -> None:
        asyncio.get_running_loop().create_task(hub.broadcast_local(message))

    if local_audio is not None:  # one speaker on the Mac: every local page sees it talk
        local_audio.on_speaking = lambda on: schedule({"type": "speaking", "on": on})
        local_audio.on_play = lambda wav: schedule({"type": "envelope", "step": STEP, "values": wav_envelope(wav)})

    # ---- access: this Mac, or a paired device; never another site's page ----

    @web.middleware("http")
    async def guard(request: Request, call_next):
        headers = request.headers
        local = remote.is_local(headers)
        if not local:
            token = remote.load_token()
            if token is None:
                return JSONResponse({"error": "remote access is off (run `escanor pair` on the Mac)"}, status_code=403)
            if request.url.path == "/" and remote.token_matches(request.query_params.get("pair"), token):
                response = RedirectResponse("/", status_code=303)   # drop the token from the address bar
                response.set_cookie(remote.COOKIE, token, max_age=remote.COOKIE_MAX_AGE, httponly=True,
                                    samesite="strict", secure=remote.is_https(headers, request.url.scheme))
                return response
            if not remote.token_matches(request.cookies.get(remote.COOKIE), token):
                return HTMLResponse(remote.PAIR_PAGE, status_code=401)
            if request.url.path.startswith("/api/") and request.url.path not in REMOTE_API:
                return JSONResponse({"error": "only available on the Mac"}, status_code=403)
        if request.method != "GET" and not origin_allowed(headers.get("origin"), remote.request_host(headers),
                                                         remote_ok=not local):
            return JSONResponse({"error": "cross-site request refused"}, status_code=403)
        return await call_next(request)

    def websocket_allowed(ws: WebSocket) -> bool:
        local = remote.is_local(ws.headers)
        if not local and not remote.token_matches(ws.cookies.get(remote.COOKIE), remote.load_token()):
            return False
        return origin_allowed(ws.headers.get("origin"), remote.request_host(ws.headers), remote_ok=not local)

    # ---- pages ----

    @web.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @web.get("/manifest.webmanifest")
    def manifest(request: Request) -> JSONResponse:
        # A home-screen app on iOS keeps its own cookies, apart from Safari's: its
        # start URL carries the pairing so it pairs itself on first launch.
        token = remote.load_token()
        start = f"/?pair={token}" if token and not remote.is_local(request.headers) else "/"
        return JSONResponse({
            "name": "Escanor", "short_name": "Escanor", "start_url": start, "display": "standalone",
            "background_color": "#0c0703", "theme_color": "#0c0703",
            "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}],
        }, media_type="application/manifest+json")

    @web.get("/api/persona")
    def persona() -> JSONResponse:
        return JSONResponse({"style": app.settings["persona"]["style"]})

    # ---- the voice switch (HUD button, menubar) ----

    def voice_info() -> dict:
        if speech is None or not hasattr(speech, "voices"):
            return {"available": False}
        return {"available": len(speech.voices) > 1, "current": speech.current,
                "label": speech.label(), "voices": {name: speech.label(name) for name in speech.voices}}

    @web.get("/api/voice")
    def get_voice() -> JSONResponse:
        return JSONResponse(voice_info())

    @web.post("/api/voice")
    async def set_voice(choice: str = "toggle") -> JSONResponse:
        if speech is None or not hasattr(speech, "use"):
            return JSONResponse({"ok": False, "error": "voice is off on this server"}, status_code=409)
        try:
            name = await asyncio.to_thread(speech.use, choice)   # may load a voice model (a second or two)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        from .. import state
        state.set("voice", name)
        if local_audio is not None:
            local_audio.interrupt()
            local_audio.say(line("voice_switched"))   # a taste of the new voice
        return JSONResponse({"ok": True, **voice_info()})

    @web.get("/api/models")
    def models() -> JSONResponse:
        return JSONResponse({"current": app.agent.model_spec, "aliases": app.settings["models"]["aliases"]})

    # ---- one turn, wherever it came from ----

    async def run(session: Session, text: str, speaker: AudioOut | None, confirmer) -> None:
        async def sink(event) -> None:
            message = _event_message(event)
            if message:
                await hub.broadcast(session.id, message)
        try:
            await speak_turn(app.agent, session, text, speaker, confirmer, sink)
        except asyncio.CancelledError:
            await hub.broadcast(session.id, {"type": "done", "stop_reason": "interrupted"})
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("turn failed")
            await hub.broadcast(session.id, {"type": "error", "message": str(e)})

    # ---- the Mac's controls: menubar and hotkeys (local only) ----

    talk = PushToTalk(open_mic)
    talk_state = {"listening": False}

    async def voice_confirm(call: ToolCall, summary: str) -> bool:
        listen = lambda: hear_once(open_mic, speech.transcribe)  # noqa: E731
        return await VoiceConfirmer(local_audio, listen).confirm(call, summary)

    def desk_confirmer(session: Session) -> DeskConfirmer:
        voice = voice_confirm if (speech is not None and local_audio is not None) else None
        return DeskConfirmer(hub, session.id, voice)

    @web.get("/api/status")
    def status() -> JSONResponse:
        proactive = app.services.get("proactive")
        dj = app.services.get("dj")
        paused = getattr(proactive, "paused_until", None)
        speaking = bool(local_audio is not None and local_audio.speaking)
        state = ("listening" if talk.recording else "speaking" if speaking
                 else "thinking" if hub.busy else "idle")
        return JSONResponse({
            "state": state, "model": app.agent.model_spec,
            "quiet_until": paused.strftime("%H:%M") if paused and paused > datetime.now() else None,
            "dj": dj.status() if dj is not None else None,
            "voice": voice_info(),
        })

    @web.post("/api/interrupt")
    def interrupt_speech() -> JSONResponse:
        if local_audio is not None:
            local_audio.interrupt()
        return JSONResponse({"ok": True})

    @web.post("/api/talk/start")
    async def talk_start() -> JSONResponse:
        """Hotkey down: stop Escanor mid-sentence and start recording."""
        if speech is None or local_audio is None:
            return JSONResponse({"ok": False, "error": "voice is off on this server"}, status_code=409)
        local_audio.interrupt()
        await hub.cancel(hub.session("latest").id)
        try:
            talk.start()
        except RuntimeError as e:  # no microphone support installed
            return JSONResponse({"ok": False, "error": str(e)}, status_code=409)
        if not talk_state["listening"]:
            talk_state["listening"] = True
            local_audio.hold()     # music down while you talk
            await hub.broadcast_local({"type": "listening", "on": True})
        return JSONResponse({"ok": True})

    async def finish_talk() -> dict:
        wav = await talk.stop()
        if talk_state["listening"]:
            talk_state["listening"] = False
            local_audio.release()
            await hub.broadcast_local({"type": "listening", "on": False})
        if wav is None:  # a tap: that just stops Escanor talking
            return {"ok": True, "heard": ""}
        text = (await asyncio.to_thread(speech.transcribe, wav)).strip()
        if not text:
            return {"ok": True, "heard": ""}
        session = hub.session("latest")
        await hub.broadcast(session.id, {"type": "user", "text": text})
        await hub.start(session.id, run(session, text, local_audio, desk_confirmer(session)))
        return {"ok": True, "heard": text}

    talk.on_timeout = finish_talk

    @web.post("/api/talk/stop")
    async def talk_stop() -> JSONResponse:
        """Hotkey up: transcribe what was said and answer it aloud."""
        if speech is None or local_audio is None:
            return JSONResponse({"ok": False, "error": "voice is off on this server"}, status_code=409)
        return JSONResponse(await finish_talk())

    @web.post("/api/explain")
    async def explain(request: Request) -> JSONResponse:
        """⌃⌥E: explain the selection in the HUD. If no page shows the
        conversation, the menubar opens one; the turn waits for it a moment
        so the whole answer streams in."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        text, path = str(body.get("text") or ""), str(body.get("path") or "")
        if not text.strip() and not path.strip():
            return JSONResponse({"ok": False, "error": "nothing selected"}, status_code=400)
        session = hub.session("latest")
        viewers = len(hub.viewers_of(session.id))
        prompt = explain_prompt(text, path)
        aloud = bool(app.settings["hotkeys"].get("explain_aloud")) and local_audio is not None

        async def go() -> None:
            if not viewers:
                await hub.wait_for_viewer(session.id, timeout=6.0)
            await hub.broadcast(session.id, {"type": "user", "text": display_text(prompt)})
            await run(session, prompt, local_audio if aloud else None, desk_confirmer(session))

        await hub.start(session.id, go())
        return JSONResponse({"ok": True, "viewers": viewers})

    @web.post("/api/quiet")
    def quiet(minutes: float = 60) -> JSONResponse:
        proactive = app.services.get("proactive")
        if proactive is None:
            return JSONResponse({"ok": False, "error": "proactive mode is off"}, status_code=409)
        until = proactive.pause(minutes) if minutes > 0 else None
        if until is None:
            proactive.paused_until = None
        return JSONResponse({"ok": True, "quiet_until": until.strftime("%H:%M") if until else None})

    @web.post("/api/model")
    def switch_model(name: str) -> JSONResponse:
        try:
            model, spec = build_model(name, app.settings)
        except (ValueError, RuntimeError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        app.agent.set_model(model, spec)
        return JSONResponse({"ok": True, "model": spec})

    # ---- the page's conversation ----

    @web.websocket("/ws")
    async def conversation(ws: WebSocket) -> None:
        if not websocket_allowed(ws):
            await ws.close(code=1008)  # policy violation: another site's page, or an unpaired device
            return
        await ws.accept()
        params = ws.query_params
        if remote.is_local(ws.headers):
            default_mode = "local" if local_audio else "off"
        else:
            default_mode = "browser"   # a phone: speech plays on the phone
        mode = params.get("speech", default_mode)
        if speech is None:
            mode = "off"
        session = hub.session(params.get("conversation", "latest"))

        send_lock = asyncio.Lock()

        async def send(message: dict) -> None:
            async with send_lock:
                await ws.send_text(json.dumps(message, default=str))

        if mode == "browser":
            audio: AudioOut | None = AudioOut(speech.synthesize, WebSocketPlayer(send, asyncio.get_running_loop()))
            audio.on_speaking = lambda on: asyncio.get_running_loop().create_task(send({"type": "speaking", "on": on}))
        elif mode == "local":
            audio = local_audio
        else:
            audio = None

        viewer = Viewer(session.id, send, local_speech=mode == "local")
        viewer.confirmer = WebConfirmer(send, audio)
        speak_typed = params.get("speak_typed") == "1"

        async def interrupt() -> None:
            if audio is not None:
                audio.interrupt()
            await hub.cancel(session.id)

        await send({"type": "hello", "conversation": session.id, "model": app.agent.model_spec,
                    "history": _history(session), "speech": mode})
        hub.join(viewer)
        try:
            while True:
                message = json.loads(await ws.receive_text())
                kind = message.get("type")
                if kind == "interrupt":
                    await interrupt()
                elif kind == "confirm":
                    viewer.confirmer.answer(int(message.get("id", 0)), bool(message.get("approved")))
                elif kind == "model":
                    try:
                        model, spec = build_model(str(message.get("name", "")), app.settings)
                    except (ValueError, RuntimeError) as e:
                        await send({"type": "error", "message": str(e)})
                    else:
                        app.agent.set_model(model, spec)
                        await send({"type": "model_switched", "model": spec})
                elif kind in ("text", "audio"):
                    await interrupt()
                    if kind == "audio":
                        if speech is None:
                            await send({"type": "error", "message": "Voice input isn't enabled on this server."})
                            continue
                        await send({"type": "transcribing"})
                        wav = base64.b64decode(message.get("data", ""))
                        text = (await asyncio.to_thread(speech.transcribe, wav)).strip()
                        if not text:
                            await send({"type": "error", "message": line("not_heard")})
                            continue
                    else:
                        text = str(message.get("text", "")).strip()
                        if not text:
                            continue
                    await hub.broadcast(session.id, {"type": "user", "text": text})
                    speaker = audio if (kind == "audio" or speak_typed) else None
                    await hub.start(session.id, run(session, text, speaker, viewer.confirmer), owner=viewer)
        except WebSocketDisconnect:
            pass
        finally:
            hub.leave(viewer)
            if await hub.cancel_owned_by(viewer) and audio is not None:
                audio.interrupt()   # this page's own reply: stop saying it
            if mode == "browser" and audio is not None:
                await audio.close()

    return web
