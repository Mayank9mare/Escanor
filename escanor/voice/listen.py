"""
voice/listen.py

`escanor listen` -- hands-free Jarvis mode on this Mac:

    wait for the wake word ("hey jarvis")  ->  chime  ->  record until you stop talking
    ->  transcribe  ->  answer aloud (sentence by sentence)  ->  back to waiting

Without openWakeWord installed, press Enter instead of saying the wake
word. Barge-in (talking over Escanor to interrupt it) is on only with
`[voice] barge_in = "headphones"`: through laptop speakers the mic hears
Escanor itself and would keep interrupting it (IMPLEMENTATION_PLAN.md
Phase 0 echo test).

The detection logic here is device-free: frames come from any async
iterator of 16 kHz int16 numpy arrays, so tests drive it with synthetic
audio. MicFrames is the real microphone (sounddevice).
"""
from __future__ import annotations
import asyncio
import io
import logging
import subprocess
import wave
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Protocol
import numpy as np

log = logging.getLogger(__name__)

RATE = 16_000
FRAME = 1280  # 80 ms, what openWakeWord expects


class VAD(Protocol):
    def is_speech(self, frame: np.ndarray) -> bool: ...


class EnergyVAD:
    """Speech = frame loudness well above a slowly adapting noise floor.
    Crude but dependency-free and good enough for turn-taking."""

    def __init__(self, ratio: float = 3.0, min_rms: float = 300.0):
        self._ratio = ratio
        self._min_rms = min_rms
        self._floor = 200.0

    def is_speech(self, frame: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) if len(frame) else 0.0
        speech = rms > max(self._min_rms, self._floor * self._ratio)
        if not speech:  # learn the room's noise only from non-speech frames
            self._floor = 0.95 * self._floor + 0.05 * max(rms, 50.0)
        return speech


class WakeWord(Protocol):
    def detected(self, frame: np.ndarray) -> bool: ...


class OpenWakeWord:
    """openWakeWord's stock "hey jarvis" model. It's known to fire on
    partial phrases, hence the threshold + a chime so you know it heard."""

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.6):
        try:
            from openwakeword.model import Model
        except ImportError as e:
            raise RuntimeError("Install the listen extra (`uv pip install -e '.[listen]'`) for wake-word mode.") from e
        self._model = Model(wakeword_models=[model])
        self._threshold = threshold

    def detected(self, frame: np.ndarray) -> bool:
        scores = self._model.predict(frame)
        if max(scores.values(), default=0.0) >= self._threshold:
            self._model.reset()
            return True
        return False


def to_wav(frames: list[np.ndarray], rate: int = RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(np.concatenate(frames).astype(np.int16).tobytes() if frames else b"")
    return buffer.getvalue()


@dataclass
class UtteranceSettings:
    start_timeout: float = 5.0     # give up if you don't start talking within this
    end_silence: float = 0.8       # this much quiet after speech ends the utterance
    max_seconds: float = 15.0
    min_speech: float = 0.25       # shorter bursts (a cough) don't count


async def record_utterance(frames: AsyncIterator[np.ndarray], vad: VAD,
                           settings: UtteranceSettings | None = None) -> list[np.ndarray] | None:
    """Collect frames from the first speech until `end_silence` of quiet.
    Returns None if nobody spoke."""
    s = settings or UtteranceSettings()
    seconds_per_frame = FRAME / RATE
    collected: list[np.ndarray] = []
    pre_roll: list[np.ndarray] = []
    waited = speech = silence = 0.0
    started = False
    async for frame in frames:
        if not started:
            pre_roll = (pre_roll + [frame])[-3:]  # keep the start of the first word
            waited += seconds_per_frame
            if vad.is_speech(frame):
                started = True
                collected = list(pre_roll)
                speech = seconds_per_frame
            elif waited >= s.start_timeout:
                return None
            continue
        collected.append(frame)
        if vad.is_speech(frame):
            speech += seconds_per_frame
            silence = 0.0
        else:
            silence += seconds_per_frame
        duration = len(collected) * seconds_per_frame
        if silence >= s.end_silence or duration >= s.max_seconds:
            break
    if not started or speech < s.min_speech:
        return None
    return collected


async def wait_for_speech(frames: AsyncIterator[np.ndarray], vad: VAD, min_seconds: float = 0.3) -> None:
    """Return once someone has been talking for `min_seconds` (barge-in)."""
    run = 0.0
    async for frame in frames:
        run = run + FRAME / RATE if vad.is_speech(frame) else 0.0
        if run >= min_seconds:
            return


class MicFrames:
    """The default microphone as an async iterator of 80 ms int16 frames."""

    def __init__(self) -> None:
        try:
            import sounddevice
        except ImportError as e:
            raise RuntimeError("Install the listen extra (`uv pip install -e '.[listen]'`) for microphone input.") from e
        self._sd = sounddevice
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=200)
        self._stream = None

    def __aiter__(self) -> "MicFrames":
        return self

    async def __anext__(self) -> np.ndarray:
        if self._stream is None:
            loop = asyncio.get_running_loop()

            def callback(data, frames, time_info, status) -> None:
                frame = data[:, 0].copy()
                loop.call_soon_threadsafe(self._put, frame)

            self._stream = self._sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, callback=callback)
            self._stream.start()
        return await self._queue.get()

    def _put(self, frame: np.ndarray) -> None:
        if self._queue.full():
            self._queue.get_nowait()  # drop the oldest rather than fall behind
        self._queue.put_nowait(frame)

    def drain(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


def chime() -> None:
    try:
        subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class ListenLoop:
    """The hands-free loop. Everything is injected so tests can run it."""

    def __init__(self, frames: AsyncIterator[np.ndarray], vad: VAD, wake: WakeWord | None,
                 transcribe: Callable[[bytes], str], respond: Callable[[str], Awaitable[None]],
                 audio, barge_in: bool = False, wait_enter: Callable[[], Awaitable[None]] | None = None,
                 on_status: Callable[[str], None] | None = None, settings: UtteranceSettings | None = None):
        self._frames = frames
        self._vad = vad
        self._wake = wake
        self._transcribe = transcribe
        self._respond = respond
        self._audio = audio
        self._barge_in = barge_in
        self._wait_enter = wait_enter
        self._status = on_status or (lambda s: None)
        self._settings = settings
        self.chime_seconds = 0.35

    async def wait_for_wake(self) -> None:
        if self._wake is None:
            if self._wait_enter is None:
                raise RuntimeError("No wake word engine and no Enter key handler.")
            self._status("Press Enter and speak.")
            await self._wait_enter()
            return
        self._status("Say 'hey Jarvis'.")
        async for frame in self._frames:
            if self._wake.detected(frame):
                return

    async def hear(self) -> str:
        """Record one utterance and transcribe it ('' if nothing was said)."""
        self._audio.hold()
        try:
            self._status("Listening…")
            frames = await record_utterance(self._frames, self._vad, self._settings)
        finally:
            self._audio.release()
        if not frames:
            return ""
        self._status("Transcribing…")
        return (await asyncio.to_thread(self._transcribe, to_wav(frames))).strip()

    async def once(self) -> str:
        """One wake → hear → respond cycle. Returns what was heard."""
        await self.wait_for_wake()
        chime()
        # Let the chime finish and drop what the mic heard of it: otherwise it
        # counts as the start of speech, and 0.8 s of silence before you
        # begin ends the "utterance" (B15).
        await asyncio.sleep(self.chime_seconds)
        drain = getattr(self._frames, "drain", None)
        if drain:
            drain()
        text = await self.hear()
        if not text:
            self._status("Didn't catch anything.")
            return ""
        self._status(f"You: {text}")
        turn = asyncio.ensure_future(self._respond(text))
        if self._barge_in:
            barge = asyncio.ensure_future(wait_for_speech(self._frames, self._vad))
            done, _ = await asyncio.wait({turn, barge}, return_when=asyncio.FIRST_COMPLETED)
            if barge in done and not turn.done():
                self._audio.interrupt()
                turn.cancel()
                self._status("(interrupted)")
            else:
                barge.cancel()
        try:
            await turn
        except asyncio.CancelledError:
            pass
        await self._audio.wait_idle()
        # Frames captured while Escanor was talking are its own voice; drop
        # them so they can't trigger the wake word or start the next utterance.
        drain = getattr(self._frames, "drain", None)
        if drain:
            drain()
        return text

    async def run(self) -> None:
        while True:
            await self.once()
