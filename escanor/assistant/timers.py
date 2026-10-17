"""
assistant/timers.py

Timers and alarms: "set a timer for 10 minutes", "wake me at 6:30".

A timer is an asyncio task in the running Escanor process (serve,
listen or chat). When it fires it is announced every way available: a
macOS notification, spoken through AudioOut at TIMER priority (attached
by the voice runtime), and printed by the CLI. Timers don't survive
restarting Escanor -- for anything that must, use a reminder.
"""
from __future__ import annotations
import asyncio
import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from ..tools.base import READ, WRITE, AsyncTool, ToolResult
from .osa import parse_when

Announce = Callable[[str], object]


@dataclass
class Timer:
    id: int
    label: str
    due: datetime
    task: asyncio.Task


class TimerService:
    def __init__(self, now: Callable[[], datetime] = datetime.now):
        self._now = now
        self._ids = itertools.count(1)
        self.timers: dict[int, Timer] = {}
        self.announcers: list[Announce] = []   # notify, speak, print...

    def add(self, label: str, due: datetime) -> Timer:
        timer_id = next(self._ids)
        delay = max(0.0, (due - self._now()).total_seconds())
        task = asyncio.get_running_loop().create_task(self._fire(timer_id, label, delay))
        timer = Timer(timer_id, label, due, task)
        self.timers[timer_id] = timer
        return timer

    async def _fire(self, timer_id: int, label: str, delay: float) -> None:
        await asyncio.sleep(delay)
        self.timers.pop(timer_id, None)
        message = f"Timer: {label}" if label else "Your timer is done."
        for announce in self.announcers:
            try:
                result = announce(message)
                if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                    await result
            except Exception:  # noqa: BLE001 -- one failing channel mustn't silence the others
                pass

    def cancel(self, timer_id: int | None = None, label: str | None = None) -> list[Timer]:
        cancelled = [t for t in self.timers.values()
                     if (timer_id is not None and t.id == timer_id) or (label and label.lower() in t.label.lower())]
        for timer in cancelled:
            timer.task.cancel()
            self.timers.pop(timer.id, None)
        return cancelled

    def describe(self, timer: Timer) -> str:
        left = timer.due - self._now()
        minutes, seconds = divmod(max(0, int(left.total_seconds())), 60)
        return f"#{timer.id} {timer.label or 'timer'} at {timer.due:%H:%M} ({minutes}m {seconds}s left)"


class SetTimerTool(AsyncTool):
    name = "set_timer"
    description = (
        "Set a timer or alarm. Give either `minutes` (from now) or `at` (local time, e.g. 2026-10-17T06:30), "
        "plus an optional label. Lasts while Escanor is running; use a reminder for anything important."
    )
    input_schema = {
        "type": "object",
        "properties": {"minutes": {"type": "number"}, "at": {"type": "string"}, "label": {"type": "string"}},
    }
    side_effect = WRITE

    def __init__(self, service: TimerService):
        self.service = service

    async def run_async(self, minutes: float | None = None, at: str | None = None, label: str = "") -> ToolResult:
        try:
            if at:
                due = parse_when(at)
            elif minutes is not None and float(minutes) > 0:
                due = self.service._now() + timedelta(minutes=float(minutes))
            else:
                return ToolResult(ok=False, output="Give minutes (more than 0) or a time.")
        except ValueError as e:
            return ToolResult(ok=False, output=str(e))
        if due <= self.service._now():
            return ToolResult(ok=False, output=f"{due:%H:%M} has already passed.")
        timer = self.service.add(label, due)
        return ToolResult(ok=True, output=f"Set {self.service.describe(timer)}.")


class TimersListTool(AsyncTool):
    name = "timers_list"
    description = "List running timers."
    input_schema = {"type": "object", "properties": {}}
    side_effect = READ
    group = "assistant"

    def __init__(self, service: TimerService):
        self.service = service

    async def run_async(self) -> ToolResult:
        timers = sorted(self.service.timers.values(), key=lambda t: t.due)
        return ToolResult(ok=True, output="\n".join(self.service.describe(t) for t in timers) or "No timers.")


class TimerCancelTool(AsyncTool):
    name = "timer_cancel"
    description = "Cancel a timer by its number or by words in its label."
    input_schema = {"type": "object", "properties": {"id": {"type": "integer"}, "label": {"type": "string"}}}
    side_effect = WRITE
    group = "assistant"

    def __init__(self, service: TimerService):
        self.service = service

    async def run_async(self, id: int | None = None, label: str | None = None) -> ToolResult:  # noqa: A002
        cancelled = self.service.cancel(id, label)
        if not cancelled:
            return ToolResult(ok=False, output="No matching timer.")
        return ToolResult(ok=True, output="Cancelled " + ", ".join(f"#{t.id} {t.label}".strip() for t in cancelled) + ".")
