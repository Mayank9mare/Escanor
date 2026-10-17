"""
voice/web_player.py

A Player (see audio_out.py) that plays through a browser instead of the
Mac's speaker: it sends each WAV over the WebSocket and waits for the
clip's duration (or a stop). Used when the web UI is opened from another
device; on the Mac itself speech plays locally so it also works with no
browser open.
"""
from __future__ import annotations
import asyncio
import base64
import io
import threading
import wave
from typing import Awaitable, Callable

Send = Callable[[dict], Awaitable[None]]


def wav_duration(wav: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate() or 1)
    except (wave.Error, EOFError):
        return 0.0


class WebSocketPlayer:
    def __init__(self, send: Send, loop: asyncio.AbstractEventLoop):
        self._send = send
        self._loop = loop

    def play(self, wav: bytes, stop: threading.Event) -> None:
        message = {"type": "audio", "data": base64.b64encode(wav).decode()}
        asyncio.run_coroutine_threadsafe(self._send(message), self._loop).result(timeout=10)
        if stop.wait(wav_duration(wav) + 0.1):
            asyncio.run_coroutine_threadsafe(self._send({"type": "stop_audio"}), self._loop).result(timeout=10)
