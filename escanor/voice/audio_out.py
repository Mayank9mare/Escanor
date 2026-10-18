"""
voice/audio_out.py

The single owner of Escanor's speaker. Everything that talks -- replies,
confirmation questions, timers, DJ patter -- goes through AudioOut.say(),
so two things never speak over each other.

- Priority: CONFIRM > REPLY > TIMER > DJ. A more important utterance
  arriving while a less important one plays cuts it off ("a conversation
  always wins"); otherwise utterances play in order.
- Pipelining: synthesis starts the moment an utterance is queued, so the
  next sentence is ready by the time the current one finishes.
- Ducking: while anything is being said, or while Escanor is listening
  (hold()/release()), the music is turned down via a Ducker (the YouTube
  Music player, Phase 5) and restored afterwards -- in a `finally`, so a
  crash mid-sentence never leaves your music quiet.
- interrupt(): barge-in. Stops what's playing and drops queued speech.

Playback is afplay (built into macOS) by default: no audio library,
and stopping is just terminating the process.
"""
from __future__ import annotations
import asyncio
import heapq
import itertools
import logging
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

log = logging.getLogger(__name__)

CONFIRM, REPLY, TIMER, DJ = 0, 1, 2, 3


class Player(Protocol):
    def play(self, wav: bytes, stop: threading.Event) -> None:
        """Play a WAV, returning when it ends or `stop` is set."""


class Ducker(Protocol):
    def duck(self) -> None: ...
    def restore(self) -> None: ...


class NoDucker:
    def duck(self) -> None:
        pass

    def restore(self) -> None:
        pass


class AfplayPlayer:
    """macOS `afplay`. Killing the process is an instant stop."""

    def play(self, wav: bytes, stop: threading.Event) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav)
            path = Path(f.name)
        try:
            process = subprocess.Popen(["afplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            while process.poll() is None:
                if stop.wait(0.02):
                    process.terminate()
                    process.wait(timeout=2)
                    break
        finally:
            path.unlink(missing_ok=True)


class NullPlayer:
    """Plays nothing; records what it was given. For tests and --mute."""

    def __init__(self, seconds_per_item: float = 0.0):
        self.played: list[bytes] = []
        self.stopped = 0
        self._seconds = seconds_per_item

    def play(self, wav: bytes, stop: threading.Event) -> None:
        if stop.wait(self._seconds):
            self.stopped += 1
            return
        self.played.append(wav)


@dataclass(order=True)
class _Item:
    priority: int
    seq: int
    text: str = field(compare=False)
    audio: asyncio.Future = field(compare=False)
    done: asyncio.Future = field(compare=False)


class AudioOut:
    def __init__(self, synthesize: Callable[[str], bytes], player: Player | None = None,
                 ducker: Ducker | None = None):
        self._synthesize_lock = threading.Lock()  # TTS engines aren't assumed thread-safe
        self._synthesize_fn = synthesize
        self.player: Player = player or AfplayPlayer()
        self.ducker: Ducker = ducker or NoDucker()
        self._queue: list[_Item] = []
        self._seq = itertools.count()
        self._wakeup: asyncio.Event | None = None
        self._stop_current = threading.Event()
        self._current: _Item | None = None
        self._holds = 0
        self._ducked = False
        self._ducker_warned = False
        self._task: asyncio.Task | None = None
        self.on_speaking: Callable[[bool], None] | None = None

    # ---------- public ----------

    def start(self) -> None:
        if self._task is None:
            self._wakeup = asyncio.Event()
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def close(self) -> None:
        self.interrupt()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._set_ducked(False)

    def say(self, text: str, priority: int = REPLY) -> asyncio.Future:
        """Queue `text`; returns a future done when it has been spoken (or dropped)."""
        loop = asyncio.get_running_loop()
        self.start()
        audio = loop.run_in_executor(None, self._synthesize, text)
        done = loop.create_future()
        item = _Item(priority, next(self._seq), text, audio, done)
        heapq.heappush(self._queue, item)
        if self._current is not None and priority < self._current.priority:
            self._stop_current.set()  # more important: cut the current one off
        self._wakeup.set()
        return done

    def interrupt(self, below: int = CONFIRM) -> None:
        """Barge-in: stop playback and drop queued speech with priority >= `below`
        (default: everything, including confirmations)."""
        kept = []
        for item in self._queue:
            if item.priority >= below:
                if not item.done.done():
                    item.done.set_result(False)
            else:
                kept.append(item)
        self._queue = kept
        heapq.heapify(self._queue)
        if self._current is not None and self._current.priority >= below:
            self._stop_current.set()

    @property
    def speaking(self) -> bool:
        return self._current is not None or bool(self._queue)

    def hold(self) -> None:
        """Keep the music ducked while listening, even if nothing is said."""
        self._holds += 1
        self._set_ducked(True)

    def release(self) -> None:
        self._holds = max(0, self._holds - 1)
        if not self.speaking and self._holds == 0:
            self._set_ducked(False)

    async def wait_idle(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.speaking:
            if deadline is not None and time.monotonic() > deadline:
                return
            await asyncio.sleep(0.02)

    # ---------- internals ----------

    def _synthesize(self, text: str) -> bytes:
        with self._synthesize_lock:
            return self._synthesize_fn(text)

    def _set_ducked(self, ducked: bool) -> None:
        if ducked == self._ducked:
            return
        try:
            self.ducker.duck() if ducked else self.ducker.restore()
        except Exception as e:  # noqa: BLE001 -- music control failing must never stop speech
            if not self._ducker_warned:  # once, one line: this runs on every utterance (B19)
                self._ducker_warned = True
                log.warning("Couldn't adjust the music volume (%s: %s); speech continues.", type(e).__name__, e)
        self._ducked = ducked

    async def _run(self) -> None:
        try:
            while True:
                if not self._queue:
                    if self._holds == 0:
                        self._set_ducked(False)
                    if self.on_speaking:
                        self.on_speaking(False)
                    self._wakeup.clear()
                    await self._wakeup.wait()
                    continue
                item = heapq.heappop(self._queue)
                self._current = item
                self._stop_current.clear()
                try:
                    wav = await item.audio
                except Exception:  # noqa: BLE001
                    log.exception("speech synthesis failed for %r", item.text[:60])
                    item.done.set_result(False)
                    self._current = None
                    continue
                if self._stop_current.is_set():  # interrupted while synthesizing
                    item.done.set_result(False)
                    self._current = None
                    continue
                self._set_ducked(True)
                if self.on_speaking:
                    self.on_speaking(True)
                try:
                    await asyncio.to_thread(self.player.play, wav, self._stop_current)
                    if not item.done.done():
                        item.done.set_result(not self._stop_current.is_set())
                except Exception:  # noqa: BLE001
                    log.exception("playback failed")
                    if not item.done.done():
                        item.done.set_result(False)
                finally:
                    self._current = None
        finally:
            self._set_ducked(False)
