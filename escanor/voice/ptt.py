"""
voice/ptt.py

Push-to-talk on the Mac: hold the hotkey (⌃⌥Space by default) anywhere,
speak, let go. The menubar sees the key; the server records:

    key down  ->  POST /api/talk/start  ->  PushToTalk.start(): mic on, collect frames
    key up    ->  POST /api/talk/stop   ->  PushToTalk.stop(): mic off, frames -> transcribe -> turn

Unlike the wake word, nothing listens until you hold the key, and it
can't be fooled by the TV. Pressing it also cuts Escanor off mid-sentence,
which voice barge-in can't do through laptop speakers (it hears itself).

A missed key-up (the menubar crashed, the Mac slept) can't leave the mic
on: recording stops by itself after `max_seconds` and calls on_timeout.
"""
from __future__ import annotations
import asyncio
from typing import AsyncIterator, Awaitable, Callable, Protocol
import numpy as np
from .listen import FRAME, RATE, EnergyVAD, UtteranceSettings, record_utterance, to_wav


class Mic(Protocol):
    def __aiter__(self) -> AsyncIterator[np.ndarray]: ...
    def close(self) -> None: ...


def default_mic() -> Mic:
    from .listen import MicFrames
    return MicFrames()


class PushToTalk:
    def __init__(self, open_mic: Callable[[], Mic] = default_mic, max_seconds: float = 60.0,
                 min_seconds: float = 0.3, on_timeout: Callable[[], Awaitable[None]] | None = None):
        self._open_mic = open_mic
        self.max_seconds = max_seconds
        self.min_seconds = min_seconds
        self.on_timeout = on_timeout
        self._mic: Mic | None = None
        self._frames: list[np.ndarray] = []
        self._task: asyncio.Task | None = None

    @property
    def recording(self) -> bool:
        return self._mic is not None

    def start(self) -> None:
        """Mic on. Raises RuntimeError if there's no microphone support."""
        if self._mic is not None:
            return  # key repeat, or a second press: already recording
        self._mic = self._open_mic()
        self._frames = []
        self._task = asyncio.get_running_loop().create_task(self._collect(self._mic))

    async def _collect(self, mic: Mic) -> None:
        limit = int(self.max_seconds * RATE / FRAME)
        async for frame in mic:
            self._frames.append(frame)
            if len(self._frames) >= limit:
                break
        if self.on_timeout is not None and self._mic is mic:
            asyncio.get_running_loop().create_task(self.on_timeout())

    async def stop(self) -> bytes | None:
        """Mic off. The recording as WAV, or None if it was a tap (shorter
        than min_seconds) or nothing was recording."""
        mic, task, frames = self._mic, self._task, self._frames
        self._mic, self._task, self._frames = None, None, []
        if mic is None:
            return None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        mic.close()
        if len(frames) * FRAME / RATE < self.min_seconds:
            return None
        return to_wav(frames)


async def hear_once(open_mic: Callable[[], Mic], transcribe: Callable[[bytes], str],
                    settings: UtteranceSettings | None = None) -> str:
    """Open the mic for one spoken answer (a hands-free "yes"/"no"), then close it."""
    mic = open_mic()
    try:
        frames = await record_utterance(mic, EnergyVAD(), settings or UtteranceSettings(start_timeout=6.0))
    finally:
        mic.close()
    if not frames:
        return ""
    return (await asyncio.to_thread(transcribe, to_wav(frames))).strip()
