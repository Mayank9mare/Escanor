"""
music/dj.py

DJ mode: Escanor curates YouTube Music for a mood and talks between songs.

Every couple of seconds the DJ looks at what's playing:
- When the current song is near its end and nothing of ours is queued
  next, it picks the next track -- the model chooses from YouTube Music's
  radio for the current song plus your liked songs, avoiding repeats --
  queues it "up next", and (every `patter_every` tracks) writes a short
  intro for it.
- When that track starts, the intro is spoken over its opening seconds,
  radio-style ("talk_over", v2): the intro was synthesised in advance so
  it starts at once, and AudioOut's ducker dips the music under it. The
  v1 style ("pause": pause -> speak -> play) is still available.
  Patter goes through AudioOut at the lowest priority: if you start
  talking to Escanor, the conversation wins.
- No mood given? It picks one from the time of day (auto_mood).

The DJ makes model calls on its own, so a SpendGuard caps them per day;
past the cap it keeps playing (first unplayed radio track) but stops
talking.
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from ..core.persona import dj_voice
from ..models.base import Model
from .library import MusicLibrary, Track
from .player import PlayerNotAuthorized, PlayerUnavailable, Song, YouTubeMusicPlayer

log = logging.getLogger(__name__)


class SpendGuard:
    """Counts the DJ's model calls per day, persisted so restarts don't reset it."""

    def __init__(self, path: Path, daily_cap: int, today: Callable[[], date] = date.today):
        self._path = path
        self._cap = daily_cap
        self._today = today

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}

    def used(self) -> int:
        data = self._load()
        return int(data.get("count", 0)) if data.get("date") == self._today().isoformat() else 0

    def allow(self) -> bool:
        """Record one call if under the cap. False when the cap is reached."""
        used = self.used()
        if used >= self._cap:
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"date": self._today().isoformat(), "count": used + 1}))
        return True


@dataclass
class DJState:
    mood: str = ""
    running: bool = False
    played: list[str] = field(default_factory=list)       # video ids, newest last
    upcoming: Track | None = None                           # what we queued next
    intro: str | None = None                                # what to say when it starts
    tracks_since_patter: int = 0
    last_video: str | None = None
    note: str = ""


MOODS = [  # (from hour, mood) for auto mode
    (0, "ambient late night"), (5, "calm acoustic morning"), (9, "focused instrumental"),
    (12, "upbeat feel-good"), (15, "energetic afternoon"), (18, "chill evening"), (21, "late night lo-fi"),
]


def auto_mood(hour: int) -> str:
    """A mood for the time of day, used when you just say "DJ mode"."""
    return [mood for start, mood in MOODS if hour >= start][-1]


def _json_object(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(match.group(0)) if match else {}
    except ValueError:
        return {}


class DJ:
    def __init__(self, player: YouTubeMusicPlayer, library: MusicLibrary, model: Model | None,
                 guard: SpendGuard, say: Callable[[str], "asyncio.Future | None"] | None = None,
                 patter_every: int = 2, lookahead: float = 20.0, poll_seconds: float = 2.0,
                 run_loop: bool = True, style: str = "talk_over",
                 prefetch: Callable[[str], None] | None = None, hour: Callable[[], int] | None = None):
        self.player = player
        self.library = library
        self.model = model
        self.guard = guard
        self.say = say                    # AudioOut.say at DJ priority, or None (silent DJ)
        self.patter_every = max(1, patter_every)
        self.lookahead = lookahead
        self.poll_seconds = poll_seconds
        self.run_loop = run_loop          # False in tests, which call tick() themselves
        self.style = style                # "talk_over" (v2, radio-style) or "pause" (v1)
        self.prefetch = prefetch          # AudioOut.prefetch: synthesise the intro ahead of time
        self._hour = hour or (lambda: datetime.now().hour)
        self.state = DJState()
        self._task: asyncio.Task | None = None

    # ---------- control ----------

    async def start(self, mood: str) -> str:
        """Start (or re-aim) DJ mode. Returns what's happening, for the tool result."""
        mood = (mood or "").strip()
        if not mood or mood.lower() in ("auto", "anything", "whatever", "surprise me"):
            mood = auto_mood(self._hour())
        self.state = DJState(mood=mood, running=True)
        current = await asyncio.to_thread(self.player.song)
        if current is None or current.paused:
            seeds = await asyncio.to_thread(self.library.search, f"{self.state.mood} songs", 5)
            if not seeds:
                self.state.running = False
                return f"Couldn't find music for '{mood}'."
            first = seeds[0]
            await asyncio.to_thread(self.player.play_now, first.video_id)
            self.state.played.append(first.video_id)
            started = f"Starting with {first.describe()}."
        else:
            self.state.played.append(current.video_id)
            started = f"Keeping {current.title} by {current.artist} and taking it from there."
        if self.run_loop and (self._task is None or self._task.done()):
            self._task = asyncio.get_running_loop().create_task(self._loop())
        return f"DJ mode on for '{self.state.mood}'. {started}"

    async def stop(self) -> str:
        self.state.running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        return "DJ mode off. The music keeps playing."

    @property
    def running(self) -> bool:
        return self.state.running

    # ---------- the loop ----------

    async def _loop(self) -> None:
        while self.state.running:
            try:
                await self.tick()
            except (PlayerUnavailable, PlayerNotAuthorized) as e:
                self.state.note = str(e)
            except Exception:  # noqa: BLE001 -- one bad tick must not kill DJ mode
                log.exception("DJ tick failed")
            await asyncio.sleep(self.poll_seconds)

    async def tick(self) -> None:
        song = await asyncio.to_thread(self.player.song)
        if song is None:
            return
        if song.video_id != self.state.last_video:
            await self._on_new_track(song)
        elif self.state.upcoming is None and song.duration and song.remaining <= self.lookahead:
            await self._prepare_next(song)

    async def _on_new_track(self, song: Song) -> None:
        self.state.last_video = song.video_id
        if song.video_id not in self.state.played:
            self.state.played.append(song.video_id)
        planned = self.state.upcoming
        intro = self.state.intro
        self.state.upcoming, self.state.intro = None, None
        if planned is None or planned.video_id != song.video_id or not intro or self.say is None:
            return
        if self.style == "talk_over":
            # v2: talk over the opening seconds; AudioOut ducks the music under the voice.
            spoken = self.say(intro)
            if spoken is not None:
                await spoken
            return
        # v1: pause, introduce, resume -- never talk over the vocals.
        await asyncio.to_thread(self.player.pause)
        try:
            spoken = self.say(intro)
            if spoken is not None:
                await spoken
        finally:
            await asyncio.to_thread(self.player.play)

    async def _prepare_next(self, song: Song) -> None:
        candidates = await self._candidates(song)
        if not candidates:
            self.state.note = "No new songs to pick from."
            return
        choice = await self._choose(song, candidates)
        await asyncio.to_thread(self.player.enqueue, choice.video_id, True)
        self.state.upcoming = choice
        self.state.tracks_since_patter += 1
        if self.say is not None and self.state.tracks_since_patter >= self.patter_every:
            intro = await self._write_intro(song, choice)
            if intro:
                self.state.intro = intro
                self.state.tracks_since_patter = 0
                if self.prefetch is not None:
                    self.prefetch(intro)  # ready the moment the track starts

    async def _candidates(self, song: Song) -> list[Track]:
        radio = await asyncio.to_thread(self.library.radio, song.video_id, 25)
        liked = await asyncio.to_thread(self.library.liked, 50)
        seen = set(self.state.played)
        out: list[Track] = []
        for track in radio + liked:
            if track.video_id not in seen and track.video_id not in {t.video_id for t in out}:
                out.append(track)
        return out[:30]

    async def _choose(self, song: Song, candidates: list[Track]) -> Track:
        if self.model is None or len(candidates) == 1 or not self.guard.allow():
            return candidates[0]
        listing = "\n".join(f"{i}. {t.describe()}" for i, t in enumerate(candidates))
        prompt = (
            f"You are a radio DJ. Mood: {self.state.mood}.\nNow playing: {song.title} by {song.artist}.\n"
            f"Pick the best next song for the mood and a smooth transition from this list:\n{listing}\n"
            'Reply with JSON only: {"pick": <number>}'
        )
        try:
            response = await asyncio.to_thread(self.model.chat, [{"role": "user", "content": prompt}])
            pick = int(_json_object(response.text).get("pick", 0))
        except (RuntimeError, ValueError, TypeError):
            pick = 0
        return candidates[pick] if 0 <= pick < len(candidates) else candidates[0]

    async def _write_intro(self, song: Song, nxt: Track) -> str | None:
        if self.model is None or not self.guard.allow():
            return None
        prompt = (
            f"You are Escanor, {dj_voice()}. Mood: {self.state.mood}. "
            f"Just played: {song.title} by {song.artist}. Up next: {nxt.title} by {nxt.artist}"
            + (f" from {nxt.album}" if nxt.album else "")
            + ". Write the spoken intro: at most 25 words, plain speech, name the song and artist. "
              "Add one fact only if you're sure of it. No quotes, no emojis."
        )
        try:
            response = await asyncio.to_thread(self.model.chat, [{"role": "user", "content": prompt}])
        except RuntimeError:
            return None
        text = " ".join(response.text.split()).strip('"')
        words = text.split()
        return " ".join(words[:40]) if words else None

    def status(self) -> str:
        if not self.state.running:
            return "DJ mode is off."
        nxt = f" Up next: {self.state.upcoming.describe()}." if self.state.upcoming else ""
        note = f" ({self.state.note})" if self.state.note else ""
        return f"DJ mode on, mood '{self.state.mood}', {len(self.state.played)} played.{nxt}{note}"
