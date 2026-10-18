from datetime import datetime, timedelta
import pytest
from escanor.assistant.proactive import Proactive, ProactivePauseTool, in_quiet_hours

SETTINGS = {"meeting_lead_minutes": 10, "evening_wrap": "21:00", "break_after_minutes": 120,
            "quiet_hours": "22:30-07:30", "max_per_hour": 4}


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def _proactive(clock, heard, events=(), reminders=(), idle=10.0, settings=None, note="projects/standup.md", reviews=0):
    idle_box = {"value": idle}
    p = Proactive({**SETTINGS, **(settings or {})}, [heard.append],
                  events=lambda start, days: list(events), reminders=lambda: list(reminders),
                  related_note=lambda title: note, reviews_due=lambda: reviews,
                  idle_seconds=lambda: idle_box["value"], now=clock)
    return p, idle_box


@pytest.mark.parametrize("at,quiet", [("23:00", True), ("03:00", True), ("07:29", True), ("07:30", False), ("12:00", False)])
def test_quiet_hours_wrap_midnight(at, quiet):
    h, m = map(int, at.split(":"))
    assert in_quiet_hours(datetime(2026, 10, 18, h, m), "22:30-07:30") is quiet
    assert in_quiet_hours(datetime(2026, 10, 18, h, m), "") is False


async def test_meeting_heads_up_once_with_related_note():
    clock = Clock(datetime(2026, 10, 18, 9, 52))
    heard = []
    events = [{"title": "Standup", "start": "2026-10-18T10:00:00", "end": "2026-10-18T10:15:00", "location": "Zoom"},
              {"title": "Lunch", "start": "2026-10-18T13:00:00", "end": "2026-10-18T14:00:00", "location": ""}]
    p, _ = _proactive(clock, heard, events=events)
    await p.tick()
    assert heard == ["Heads up: Standup starts at 10:00 (Zoom). Your notes on it are in projects/standup.md."]
    clock.at += timedelta(minutes=2)
    await p.tick()
    assert len(heard) == 1  # announced once


async def test_evening_wrap_mentions_overdue_and_reviews():
    clock = Clock(datetime(2026, 10, 18, 21, 5))
    heard = []
    reminders = [{"title": "Pay rent", "due": "2026-10-17T09:00:00"}, {"title": "Later", "due": ""}]
    p, _ = _proactive(clock, heard, reminders=reminders, reviews=2, settings={"break_after_minutes": 0})
    await p.tick()
    assert heard == ["Evening wrap-up. 1 reminder(s) due today or overdue, starting with Pay rent. "
                     "2 learning topic(s) are due for review."]
    await p.tick()
    assert len(heard) == 1


async def test_break_nudge_after_long_activity_and_reset_after_away():
    clock = Clock(datetime(2026, 10, 18, 10, 0))
    heard = []
    p, idle = _proactive(clock, heard, settings={"evening_wrap": ""})
    await p.tick()                       # starts tracking
    clock.at += timedelta(minutes=121)
    await p.tick()
    assert heard and "stand up" in heard[0]
    idle["value"] = 600                  # stepped away
    clock.at += timedelta(minutes=10)
    await p.tick()
    idle["value"] = 5
    clock.at += timedelta(minutes=30)
    await p.tick()
    await p.tick()
    assert len(heard) == 1               # the clock restarted after the break


async def test_silent_in_quiet_hours_paused_and_over_cap():
    clock = Clock(datetime(2026, 10, 18, 23, 0))
    heard = []
    events = [{"title": f"E{i}", "start": f"2026-10-18T23:0{i + 1}:00", "end": "", "location": ""} for i in range(6)]
    p, _ = _proactive(clock, heard, events=events, note=None, settings={"break_after_minutes": 0, "evening_wrap": ""})
    assert await p.tick() == []          # quiet hours
    p.settings["quiet_hours"] = ""
    ProactivePauseTool(p).run(minutes=30)
    assert await p.tick() == []          # paused
    assert ProactivePauseTool(p).run(minutes=0).output == "Proactive mode resumed."
    said = await p.tick()
    assert len(said) == 4                # max_per_hour


async def test_calendar_failure_doesnt_stop_other_checks():
    clock = Clock(datetime(2026, 10, 18, 21, 0))
    heard = []

    def broken(start, days):
        raise RuntimeError("Calendar permission denied")

    p = Proactive({**SETTINGS, "break_after_minutes": 0}, [heard.append], events=broken, reminders=lambda: [],
                  idle_seconds=lambda: None, now=clock)
    await p.tick()
    assert heard == ["Evening wrap-up. No open reminders."]


async def test_start_and_stop_background_task():
    p = Proactive({**SETTINGS, "poll_seconds": 3600}, [], idle_seconds=lambda: None)
    await p.start()
    assert p._task is not None
    await p.stop()
    assert p._task is None


async def test_late_evening_wrap_still_fires():
    clock = Clock(datetime(2026, 10, 18, 23, 10))
    heard = []
    p, _ = _proactive(clock, heard, settings={"evening_wrap": "23:00", "quiet_hours": "", "break_after_minutes": 0})
    await p.tick()
    assert heard and heard[0].startswith("Evening wrap-up.")
