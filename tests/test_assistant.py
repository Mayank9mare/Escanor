import asyncio
import shutil
import subprocess
from datetime import datetime, timedelta
import pytest
from escanor import config
from escanor.app import build_app, build_model
from escanor.assistant import osa
from escanor.assistant.apple import Apple, CalendarCreateTool, ClipboardTool, OpenUrlTool, ReminderCreateTool, RemindersListTool
from escanor.assistant.briefing import Briefing, due_today_first
from escanor.assistant.osa import FIELD, RECORD, date_expr, parse_when, quote
from escanor.assistant.timers import SetTimerTool, TimerCancelTool, TimerService, TimersListTool
from escanor.core.events import ToolFinished
from escanor.models.base import ModelResponse, ToolCall
from escanor.models.mock_provider import MockModel
from escanor.tools.base import EXTERNAL
from escanor.tools.permissions import DECLINED

HAS_OSASCRIPT = shutil.which("osascript") is not None


class Recorder:
    def __init__(self, reply=""):
        self.scripts: list[str] = []
        self.reply = reply

    def __call__(self, script: str) -> str:
        self.scripts.append(script)
        return self.reply


# ---------- osa ----------

def test_quote_neutralises_injection():
    assert quote('a"b\\c') == '"a\\"b\\\\c"'


@pytest.mark.skipif(not HAS_OSASCRIPT, reason="needs macOS")
@pytest.mark.parametrize("when", [datetime(2026, 1, 31, 19, 5, 7), datetime(2028, 2, 29, 0, 0), datetime(2026, 12, 31, 23, 59)])
def test_date_expr_builds_exact_date_without_month_rollover(when):
    script = date_expr("d", when) + '\nreturn (d as «class isot» as string)'
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout.strip()
    assert out == when.isoformat(timespec="seconds")


def test_parse_when_accepts_iso_and_rejects_words():
    assert parse_when("2026-10-17 19:00") == datetime(2026, 10, 17, 19, 0)
    with pytest.raises(ValueError, match="2026-10-17T19:00"):
        parse_when("tomorrow evening")


def test_run_explains_automation_permission(monkeypatch):
    class Result:
        returncode, stdout, stderr = 1, "", "execution error: Not authorized to send Apple events to Reminders. (-1743)"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(RuntimeError, match="Privacy & Security → Automation"):
        osa.run("x")


# ---------- apple ----------

def test_reminders_parsed_from_separated_records():
    output = f"Call mom{FIELD}2026-10-17T19:00:00{FIELD}Family{RECORD}Buy milk{FIELD}{FIELD}Groceries{RECORD}"
    items = Apple(runner=Recorder(output)).reminders()
    assert items == [{"title": "Call mom", "due": "2026-10-17T19:00:00", "list": "Family"},
                     {"title": "Buy milk", "due": "", "list": "Groceries"}]
    assert "Call mom (Sat 17 Oct 19:00, Family)" in RemindersListTool(Apple(runner=Recorder(output))).run().output


def test_reminder_create_escapes_title_and_sets_due_date():
    recorder = Recorder()
    result = ReminderCreateTool(Apple(runner=recorder)).run(title='Call "mom"', due="2026-10-17T19:00", list="Family")
    assert result.ok and "Sat 17 Oct 19:00" in result.output
    script = recorder.scripts[0]
    assert 'name:"Call \\"mom\\""' in script and 'list "Family"' in script and "set due date of newReminder" in script


def test_tools_report_bad_dates_as_results():
    assert ReminderCreateTool(Apple(runner=Recorder())).run(title="x", due="soonish").ok is False
    assert CalendarCreateTool(Apple(runner=Recorder())).run(title="x", start="later").ok is False


def test_calendar_events_sorted_and_formatted():
    output = (f"Gym{FIELD}2026-10-17T18:00:00{FIELD}2026-10-17T19:00:00{FIELD}{FIELD}Personal{RECORD}"
              f"Standup{FIELD}2026-10-17T09:30:00{FIELD}2026-10-17T09:45:00{FIELD}Zoom{FIELD}Work{RECORD}")
    events = Apple(runner=Recorder(output)).events(datetime(2026, 10, 17))
    assert [e["title"] for e in events] == ["Standup", "Gym"]


def test_external_tools_are_external_and_describe_plainly():
    assert ReminderCreateTool.side_effect == EXTERNAL and CalendarCreateTool.side_effect == EXTERNAL
    assert OpenUrlTool.side_effect == EXTERNAL and ClipboardTool.untrusted_output
    assert ReminderCreateTool(Apple(runner=Recorder())).describe_call({"title": "Call mom", "due": "2026-10-17T19:00"}) == \
        "create reminder 'Call mom' for 2026-10-17T19:00"


def test_open_url_refuses_non_http():
    recorder = Recorder()
    assert OpenUrlTool(Apple(runner=recorder)).run(url="file:///etc/passwd").ok is False
    assert recorder.scripts == []


# ---------- end to end: "remind me to call mom at 7" needs a yes ----------

def _app(store):
    settings = config.load_settings()
    settings["brain"]["embedder"] = "none"
    settings["skills"]["dirs"] = []
    settings["music"]["enabled"] = False
    model, spec = build_model("mock", settings)
    return build_app(settings, model, spec)


class Answer:
    def __init__(self, yes):
        self.yes = yes
        self.asked = []

    async def confirm(self, call, summary):
        self.asked.append(summary)
        return self.yes


@pytest.mark.parametrize("approve", [True, False])
async def test_should_create_reminder_only_after_confirmation(store, approve):
    app = _app(store)
    recorder = Recorder()
    app.services["apple"].run = recorder
    app.agent.tools.enable("assistant")
    app.agent.set_model(MockModel(script=[
        ModelResponse(tool_calls=[ToolCall("c1", "reminder_create", {"title": "Call mom", "due": "2026-10-17T19:00"})]),
        ModelResponse(text="Done."),
    ]), "mock:m")
    answer = Answer(approve)
    events = [e async for e in app.agent.run_turn(store.create(), "remind me to call mom at 7", answer)]
    result = next(e for e in events if isinstance(e, ToolFinished)).result
    assert answer.asked == ["create reminder 'Call mom' for 2026-10-17T19:00"]
    assert (len(recorder.scripts) == 1) is approve
    assert result.ok is approve and (approve or result.output == DECLINED)


# ---------- timers ----------

async def test_timer_fires_through_every_announcer_and_survives_a_broken_one():
    service = TimerService()
    heard = []
    service.announcers += [lambda m: (_ for _ in ()).throw(RuntimeError("no speaker")), heard.append]
    result = await SetTimerTool(service).run_async(minutes=0.001, label="tea")
    assert result.ok and "tea" in result.output
    await asyncio.sleep(0.2)
    assert heard == ["Timer: tea"] and service.timers == {}


async def test_timer_list_and_cancel():
    service = TimerService()
    await SetTimerTool(service).run_async(minutes=10, label="pasta")
    assert "pasta" in (await TimersListTool(service).run_async()).output
    assert (await TimerCancelTool(service).run_async(label="PASTA")).ok
    assert (await TimersListTool(service).run_async()).output == "No timers."


async def test_timer_rejects_past_and_missing_times():
    service = TimerService(now=lambda: datetime(2026, 10, 17, 12, 0))
    assert not (await SetTimerTool(service).run_async(at="2026-10-17T11:00")).ok
    assert not (await SetTimerTool(service).run_async()).ok


# ---------- briefing ----------

def test_briefing_survives_failing_source():
    briefing = Briefing(now=lambda: datetime(2026, 10, 17, 7, 30))
    briefing.add("Calendar today", lambda: (_ for _ in ()).throw(RuntimeError("Calendar blocked")))
    briefing.add("Empty", lambda: "")
    briefing.add("Reminders", lambda: "- Call mom")
    text = briefing.build()
    assert text.startswith("Now: Saturday 17 October 2026, 07:30")
    assert "(unavailable: Calendar blocked)" in text and "## Empty" not in text and "- Call mom" in text


def test_due_today_first_flags_overdue():
    reminders = [{"title": "Later", "due": "2026-10-20T10:00"}, {"title": "Old", "due": "2026-10-10T10:00"},
                 {"title": "Now", "due": "2026-10-17T10:00"}, {"title": "Whenever", "due": ""}]
    assert due_today_first(reminders, "2026-10-17").splitlines() == [
        "- Old (overdue)", "- Now (today)", "- Later", "- Whenever"]


async def test_briefing_tool_is_in_core_and_uses_registered_sources(store):
    app = _app(store)
    app.services["apple"].run = Recorder("")
    output = app.agent.tools.get("morning_briefing").run().output
    assert "## Reminders\nNothing open." in output
