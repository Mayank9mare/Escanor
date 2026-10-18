"""
assistant/proactive.py

Proactive mode: Escanor speaks up on its own, the Jarvis part.

Every minute (while serve / listen / chat is running) it checks:
- Meetings: an event starting within `meeting_lead_minutes` -> "Heads up:
  Standup starts at 10:00", plus the most relevant note from your brain
  (a local search, no model call).
- Evening wrap-up at `evening_wrap` (e.g. 21:00): reminders still open
  and learning reviews due.
- Breaks: after `break_after_minutes` of continuous activity on the Mac
  (from macOS's idle timer, no keylogging) -> a nudge to stand up.

Guard rails: nothing during quiet hours, at most `max_per_hour`
announcements, each thing announced once, and "be quiet for an hour"
(the proactive_pause tool) silences it. Announcements go through the same
channels as timers: notification, speech (TIMER priority) and the chat.
"""
from __future__ import annotations
import asyncio
import logging
import re
import subprocess
from datetime import datetime, time, timedelta
from typing import Callable
from ..tools.base import WRITE, Tool, ToolResult

log = logging.getLogger(__name__)


def mac_idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse input (macOS HIDIdleTime)."""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
    return int(match.group(1)) / 1e9 if match else None


def parse_clock(text: str) -> time | None:
    try:
        return time.fromisoformat(text.strip()) if text and text.strip() else None
    except ValueError:
        return None


def in_quiet_hours(now: datetime, spec: str) -> bool:
    """spec like '22:30-07:30' (may wrap past midnight); '' = never quiet."""
    if not spec or "-" not in spec:
        return False
    start, end = (parse_clock(part) for part in spec.split("-", 1))
    if start is None or end is None:
        return False
    current = now.time()
    return start <= current < end if start < end else (current >= start or current < end)


class Proactive:
    def __init__(self, settings: dict, announcers: list, events: Callable[[datetime, int], list[dict]] | None = None,
                 reminders: Callable[[], list[dict]] | None = None, related_note: Callable[[str], str | None] | None = None,
                 reviews_due: Callable[[], int] | None = None, idle_seconds: Callable[[], float | None] = mac_idle_seconds,
                 now: Callable[[], datetime] = datetime.now):
        self.settings = settings
        self.announcers = announcers
        self._events = events
        self._reminders = reminders
        self._related_note = related_note
        self._reviews_due = reviews_due
        self._idle = idle_seconds
        self._now = now
        self.announced: set[str] = set()
        self._recent: list[datetime] = []
        self._events_cache: tuple[datetime, list[dict]] | None = None
        self._active_since: datetime | None = None
        self.paused_until: datetime | None = None
        self._task: asyncio.Task | None = None

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def pause(self, minutes: float) -> datetime:
        self.paused_until = self._now() + timedelta(minutes=minutes)
        return self.paused_until

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 -- one bad check must not stop proactive mode
                log.exception("proactive tick failed")
            await asyncio.sleep(float(self.settings.get("poll_seconds", 60)))

    # ---------- checks ----------

    async def tick(self) -> list[str]:
        """Run every check once; returns what was announced (for tests)."""
        now = self._now()
        if self.paused_until and now < self.paused_until:
            return []
        if in_quiet_hours(now, self.settings.get("quiet_hours", "")):
            return []
        candidates: list[tuple[str, str]] = []
        candidates += await self._meetings(now)
        candidates += await self._evening(now)
        candidates += self._break(now)
        said = []
        for key, message in candidates:
            if key in self.announced or not self._under_cap(now):
                continue
            self.announced.add(key)
            self._recent.append(now)
            await self._announce(message)
            said.append(message)
        return said

    def _under_cap(self, now: datetime) -> bool:
        self._recent = [t for t in self._recent if now - t < timedelta(hours=1)]
        return len(self._recent) < int(self.settings.get("max_per_hour", 4))

    async def _announce(self, message: str) -> None:
        for announce in list(self.announcers):
            try:
                result = announce(message)
                if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                    await result
            except Exception:  # noqa: BLE001
                pass

    async def _meetings(self, now: datetime) -> list[tuple[str, str]]:
        lead = float(self.settings.get("meeting_lead_minutes", 10))
        if self._events is None or lead <= 0:
            return []
        cached = self._events_cache
        if cached is None or now - cached[0] >= timedelta(minutes=5):  # don't poll Calendar every minute
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            try:
                events = await asyncio.to_thread(self._events, midnight, 1)
            except Exception:  # noqa: BLE001 -- e.g. Calendar permission not granted
                events = []
            self._events_cache = (now, events)
        out = []
        for event in self._events_cache[1]:
            try:
                start = datetime.fromisoformat(event["start"])
            except (KeyError, ValueError):
                continue
            minutes = (start - now).total_seconds() / 60
            if 0 < minutes <= lead:
                message = f"Heads up: {event['title']} starts at {start:%H:%M}"
                if event.get("location"):
                    message += f" ({event['location']})"
                message += "."
                if self._related_note:
                    note = await asyncio.to_thread(self._related_note, event["title"])
                    if note:
                        message += f" Your notes on it are in {note}."
                out.append((f"meeting:{event['title']}:{event['start']}", message))
        return out

    async def _evening(self, now: datetime) -> list[tuple[str, str]]:
        at = parse_clock(self.settings.get("evening_wrap", ""))
        if at is None:
            return []
        start = datetime.combine(now.date(), at, tzinfo=now.tzinfo)
        if not start <= now < start + timedelta(hours=2):  # announce within two hours of the set time
            return []
        key = f"evening:{now.date().isoformat()}"
        if key in self.announced:
            return []
        parts = ["Evening wrap-up."]
        if self._reminders:
            try:
                open_items = await asyncio.to_thread(self._reminders)
            except Exception:  # noqa: BLE001
                open_items = None
            if open_items is not None:
                today = now.date().isoformat()
                overdue = [r for r in open_items if r.get("due") and r["due"][:10] <= today]
                if overdue:
                    parts.append(f"{len(overdue)} reminder(s) due today or overdue, starting with {overdue[0]['title']}.")
                elif open_items:
                    parts.append(f"{len(open_items)} open reminder(s), nothing overdue.")
                else:
                    parts.append("No open reminders.")
        if self._reviews_due:
            due = self._reviews_due()
            if due:
                parts.append(f"{due} learning topic(s) are due for review.")
        return [(key, " ".join(parts))]

    def _break(self, now: datetime) -> list[tuple[str, str]]:
        after = float(self.settings.get("break_after_minutes", 120))
        if after <= 0:
            return []
        idle = self._idle()
        if idle is None:
            return []
        if idle >= 300:  # away for 5+ minutes: that was a break
            self._active_since = None
            return []
        if self._active_since is None:
            self._active_since = now - timedelta(seconds=idle)
            return []
        active = (now - self._active_since).total_seconds() / 60
        if active >= after:
            key = f"break:{self._active_since.isoformat()}"
            self._active_since = now  # next nudge after another full stretch
            return [(key, f"You've been at it for {int(active // 60)}h{int(active % 60):02d}m. Time to stand up and stretch.")]
        return []


class ProactivePauseTool(Tool):
    name = "proactive_pause"
    description = "Stop proactive announcements (meeting heads-ups, nudges) for some minutes, e.g. 'be quiet for an hour'."
    input_schema = {"type": "object", "properties": {"minutes": {"type": "number"}}, "required": ["minutes"]}
    side_effect = WRITE
    group = "assistant"

    def __init__(self, proactive: Proactive):
        self.proactive = proactive

    def run(self, minutes: float) -> ToolResult:
        if float(minutes) <= 0:
            self.proactive.paused_until = None
            return ToolResult(ok=True, output="Proactive mode resumed.")
        until = self.proactive.pause(float(minutes))
        return ToolResult(ok=True, output=f"Quiet until {until:%H:%M}.")
