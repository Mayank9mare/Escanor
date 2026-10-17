"""
server/app.py

`escanor serve`: the web UI and its WebSocket, on 127.0.0.1 only.

One WebSocket per browser tab carries a whole conversation:

  browser -> server                        server -> browser
  {type: "text", text}                     {type: "hello", conversation, model, history, speech}
  {type: "audio", data: <base64 WAV>}      {type: "user", text}          (transcript)
  {type: "interrupt"}                      {type: "text", text}          (reply fragment)
  {type: "confirm", id, approved}          {type: "tool_started" / "tool_finished", ...}
  {type: "model", name}                    {type: "confirm_request", id, summary}
                                           {type: "done", stop_reason} / {type: "error", message}
                                           {type: "model_switched", model}
                                           {type: "speaking", on} / {type: "audio", data} / {type: "stop_audio"}

Speech output ("speech" query parameter) -- replies to voice input are
spoken; typed messages too with speak_typed=1:
  local   -- played on the Mac through the shared AudioOut (default)
  browser -- WAVs sent to the page (for using Escanor from another device)
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
from pathlib import Path
from typing import Any
# Module-level on purpose: with `from __future__ import annotations`, FastAPI
# resolves the `ws: WebSocket` annotation by name from module globals. Imported
# inside create_server(), it was unresolvable and FastAPI treated `ws` as a
# required query parameter, rejecting every connection (B12).
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from ..app import Escanor, build_model
from ..core.events import Error, ModelSwitched, TextDelta, ToolFinished, ToolStarted, TurnDone
from ..core.session import Session, SessionStore
from ..models.base import ToolCall
from ..voice.audio_out import CONFIRM, AudioOut
from ..voice.conversation import speak_turn
from ..voice.web_player import WebSocketPlayer

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def _history(session: Session) -> list[dict]:
    out = []
    for m in session.messages:
        if m["role"] == "user":
            out.append({"role": "user", "text": m["content"]})
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
            self._audio.say(f"Should I go ahead with {summary}?", CONFIRM)
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


def create_server(app: Escanor, store: SessionStore, speech: Any | None, local_audio: AudioOut | None):
    """`speech` has transcribe(wav)->str and synthesize(text)->wav (SpeechEngine),
    or None for a text-only server. `local_audio` speaks on this Mac."""
    web = FastAPI(title="Escanor")
    web.mount("/static", StaticFiles(directory=STATIC), name="static")

    @web.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @web.get("/api/models")
    def models() -> JSONResponse:
        return JSONResponse({"current": app.agent.model_spec, "aliases": app.settings["models"]["aliases"]})

    @web.websocket("/ws")
    async def conversation(ws: WebSocket) -> None:
        await ws.accept()
        params = ws.query_params
        mode = params.get("speech", "local" if local_audio else "off")
        if speech is None:
            mode = "off"
        wanted = params.get("conversation", "latest")
        latest = store.latest_id()
        if wanted == "new" or latest is None:
            session = store.create()
        elif wanted == "latest":
            session = store.load(latest)
        else:
            try:
                session = store.load(int(wanted))
            except (KeyError, ValueError):
                session = store.create()

        send_lock = asyncio.Lock()

        async def send(message: dict) -> None:
            async with send_lock:
                await ws.send_text(json.dumps(message, default=str))

        if mode == "browser":
            audio: AudioOut | None = AudioOut(speech.synthesize, WebSocketPlayer(send, asyncio.get_running_loop()))
        elif mode == "local":
            audio = local_audio
        else:
            audio = None
        if audio is not None:
            audio.on_speaking = lambda on: asyncio.get_running_loop().create_task(send({"type": "speaking", "on": on}))

        confirmer = WebConfirmer(send, audio)
        turn: asyncio.Task | None = None

        async def interrupt() -> None:
            nonlocal turn
            if audio is not None:
                audio.interrupt()
            if turn is not None and not turn.done():
                turn.cancel()
                try:
                    await turn
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            turn = None

        speak_typed = params.get("speak_typed") == "1"

        async def run(text: str, voice_input: bool) -> None:
            async def sink(event) -> None:
                message = _event_message(event)
                if message:
                    await send(message)
            speaker = audio if (voice_input or speak_typed) else None
            try:
                await speak_turn(app.agent, session, text, speaker, confirmer, sink)
            except asyncio.CancelledError:
                await send({"type": "done", "stop_reason": "interrupted"})
                raise
            except Exception as e:  # noqa: BLE001
                log.exception("turn failed")
                await send({"type": "error", "message": str(e)})

        await send({"type": "hello", "conversation": session.id, "model": app.agent.model_spec,
                    "history": _history(session), "speech": mode})
        try:
            while True:
                message = json.loads(await ws.receive_text())
                kind = message.get("type")
                if kind == "interrupt":
                    await interrupt()
                elif kind == "confirm":
                    confirmer.answer(int(message.get("id", 0)), bool(message.get("approved")))
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
                            await send({"type": "error", "message": "Didn't catch that. Try again?"})
                            continue
                    else:
                        text = str(message.get("text", "")).strip()
                        if not text:
                            continue
                    await send({"type": "user", "text": text})
                    turn = asyncio.create_task(run(text, voice_input=kind == "audio"))
        except WebSocketDisconnect:
            pass
        finally:
            await interrupt()
            if mode == "browser" and audio is not None:
                await audio.close()
            elif audio is not None:
                audio.on_speaking = None

    return web
