"""
assistant/apple.py

Reminders, Calendar and small Mac actions through AppleScript.

Permission levels (tools/permissions.py):
- reading reminders/events, notifications, opening an app: run freely
- creating or completing reminders, creating events, opening a URL:
  "external" -- always asks you first. (A URL can carry data out in its
  query string, so even "just open this link" asks.)

Calendar limitation: AppleScript's `every event whose start date ...`
doesn't expand repeating events, so a weekly meeting created long ago
may not show up on a given day.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Callable
from ..tools.base import EXTERNAL, READ, WRITE, Tool, ToolResult
from . import osa
from .osa import date_expr, parse_when, quote

GROUP = "assistant"
GROUP_DESCRIPTION = "Apple Reminders and Calendar, opening apps/links, notifications, clipboard, timers"

_FS = "(ASCII character 31)"
_RS = "(ASCII character 30)"
_ISO = "((%s) as «class isot» as string)"


class Apple:
    """The AppleScript-backed operations. `runner` is injectable for tests."""

    def __init__(self, runner: Callable[[str], str] = osa.run, now: Callable[[], datetime] = datetime.now):
        self.run = runner
        self.now = now

    # ---------- reminders ----------

    def reminders(self, list_name: str | None = None) -> list[dict]:
        source = f"reminders of list {quote(list_name)}" if list_name else "reminders"
        script = f'''tell application "Reminders"
set out to ""
repeat with r in ({source} whose completed is false)
  set dd to ""
  try
    set dd to {_ISO % "due date of r"}
  end try
  set out to out & (name of r) & {_FS} & dd & {_FS} & (name of container of r) & {_RS}
end repeat
return out
end tell'''
        return [{"title": f[0], "due": f[1] if len(f) > 1 else "", "list": f[2] if len(f) > 2 else ""}
                for f in osa.records(self.run(script))]

    def create_reminder(self, title: str, due: datetime | None = None, list_name: str | None = None,
                        notes: str = "") -> None:
        target = f"list {quote(list_name)}" if list_name else "default list"
        lines = ['tell application "Reminders"',
                 f"set newReminder to make new reminder at end of {target} with properties {{name:{quote(title)}, body:{quote(notes)}}}"]
        if due is not None:
            lines += [date_expr("dueDate", due), "set due date of newReminder to dueDate",
                      "set remind me date of newReminder to dueDate"]
        lines.append("end tell")
        self.run("\n".join(lines))

    def complete_reminder(self, title: str) -> int:
        script = f'''tell application "Reminders"
set matches to (reminders whose completed is false and name is {quote(title)})
repeat with r in matches
  set completed of r to true
end repeat
return count of matches
end tell'''
        return int(self.run(script) or 0)

    # ---------- calendar ----------

    def events(self, start: datetime, days: int = 1) -> list[dict]:
        end = start + timedelta(days=max(1, days))
        script = f'''{date_expr("startDate", start)}
{date_expr("endDate", end)}
tell application "Calendar"
set out to ""
repeat with c in calendars
  repeat with e in (every event of c whose start date ≥ startDate and start date < endDate)
    set loc to ""
    try
      set loc to location of e
      if loc is missing value then set loc to ""
    end try
    set out to out & (summary of e) & {_FS} & {_ISO % "start date of e"} & {_FS} & {_ISO % "end date of e"} & {_FS} & loc & {_FS} & (name of c) & {_RS}
  end repeat
end repeat
return out
end tell'''
        events = [{"title": f[0], "start": f[1], "end": f[2], "location": f[3], "calendar": f[4]}
                  for f in osa.records(self.run(script)) if len(f) >= 5]
        return sorted(events, key=lambda e: e["start"])

    def create_event(self, title: str, start: datetime, end: datetime, calendar: str | None = None,
                     location: str = "", notes: str = "") -> None:
        target = f"calendar {quote(calendar)}" if calendar else "first calendar whose writable is true"
        script = f'''{date_expr("startDate", start)}
{date_expr("endDate", end)}
tell application "Calendar"
set target to {target}
make new event at end of events of target with properties {{summary:{quote(title)}, start date:startDate, end date:endDate, location:{quote(location)}, description:{quote(notes)}}}
end tell'''
        self.run(script)

    # ---------- mac ----------

    def notify(self, title: str, text: str) -> None:
        self.run(f"display notification {quote(text)} with title {quote(title)}")

    def open_app(self, name: str) -> None:
        self.run(f"tell application {quote(name)} to activate")

    def open_url(self, url: str) -> None:
        self.run(f"open location {quote(url)}")

    def clipboard(self) -> str:
        return self.run("the clipboard as text")


def format_events(events: list[dict]) -> str:
    if not events:
        return "No events."
    return "\n".join(
        f"- {_fmt_time(e['start'])}–{e['end'][11:16]} {e['title']}"
        + (f" @ {e['location']}" if e["location"] else "") + f" ({e['calendar']})" for e in events
    )


def _fmt_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%a %d %b %H:%M")
    except ValueError:
        return iso or "no due date"


class _AppleTool(Tool):
    group = GROUP

    def __init__(self, apple: Apple):
        self.apple = apple

    def _safely(self, action: Callable[[], str]) -> ToolResult:
        try:
            return ToolResult(ok=True, output=action())
        except (RuntimeError, ValueError) as e:
            return ToolResult(ok=False, output=str(e))


class RemindersListTool(_AppleTool):
    name = "reminders_list"
    description = "List open reminders (optionally from one list), with due dates."
    input_schema = {"type": "object", "properties": {"list": {"type": "string"}}}
    side_effect = READ

    def run(self, list: str | None = None) -> ToolResult:  # noqa: A002 -- matches the schema
        def action() -> str:
            items = self.apple.reminders(list)
            if not items:
                return "No open reminders."
            return "\n".join(f"- {r['title']} ({_fmt_time(r['due'])}, {r['list']})" for r in items)
        return self._safely(action)


class ReminderCreateTool(_AppleTool):
    name = "reminder_create"
    description = "Create a reminder in Apple Reminders. `due` is local time, e.g. 2026-10-17T19:00."
    input_schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}, "due": {"type": "string"}, "list": {"type": "string"},
                       "notes": {"type": "string"}},
        "required": ["title"],
    }
    side_effect = EXTERNAL

    def describe_call(self, arguments: dict) -> str:
        due = arguments.get("due")
        return f"create reminder '{arguments.get('title', '')}'" + (f" for {due}" if due else "")

    def run(self, title: str, due: str | None = None, list: str | None = None, notes: str = "") -> ToolResult:  # noqa: A002
        def action() -> str:
            when = parse_when(due) if due else None
            self.apple.create_reminder(title, when, list, notes)
            return f"Reminder '{title}' created" + (f" for {when:%a %d %b %H:%M}." if when else ".")
        return self._safely(action)


class ReminderCompleteTool(_AppleTool):
    name = "reminder_complete"
    description = "Mark open reminders with exactly this title as done."
    input_schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
    side_effect = EXTERNAL

    def describe_call(self, arguments: dict) -> str:
        return f"mark reminder '{arguments.get('title', '')}' as done"

    def run(self, title: str) -> ToolResult:
        def action() -> str:
            count = self.apple.complete_reminder(title)
            return f"Marked {count} reminder(s) done." if count else f"No open reminder titled '{title}'."
        return self._safely(action)


class CalendarListTool(_AppleTool):
    name = "calendar_list"
    description = "List calendar events from a date (default today) for N days (default 1)."
    input_schema = {"type": "object", "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"},
                                                     "days": {"type": "integer"}}}
    side_effect = READ

    def run(self, date: str | None = None, days: int = 1) -> ToolResult:
        def action() -> str:
            start = parse_when(date) if date else self.apple.now()
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            return format_events(self.apple.events(start, int(days or 1)))
        return self._safely(action)


class CalendarCreateTool(_AppleTool):
    name = "calendar_create"
    description = "Create a calendar event. Times are local, e.g. 2026-10-18T15:00; default length 60 minutes."
    input_schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}, "start": {"type": "string"}, "minutes": {"type": "integer"},
                       "calendar": {"type": "string"}, "location": {"type": "string"}, "notes": {"type": "string"}},
        "required": ["title", "start"],
    }
    side_effect = EXTERNAL

    def describe_call(self, arguments: dict) -> str:
        return f"add event '{arguments.get('title', '')}' at {arguments.get('start', '?')}"

    def run(self, title: str, start: str, minutes: int = 60, calendar: str | None = None,
            location: str = "", notes: str = "") -> ToolResult:
        def action() -> str:
            begin = parse_when(start)
            self.apple.create_event(title, begin, begin + timedelta(minutes=int(minutes or 60)), calendar, location, notes)
            return f"Added '{title}' on {begin:%a %d %b at %H:%M}."
        return self._safely(action)


class NotifyTool(_AppleTool):
    name = "notify"
    description = "Show a macOS notification."
    input_schema = {"type": "object", "properties": {"title": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["text"]}
    side_effect = WRITE

    def run(self, text: str, title: str = "Escanor") -> ToolResult:
        return self._safely(lambda: (self.apple.notify(title, text), "Shown.")[1])


class OpenAppTool(_AppleTool):
    name = "open_app"
    description = "Open (or bring to the front) a Mac app by name, e.g. 'Safari', 'Notes'."
    input_schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
    side_effect = WRITE

    def run(self, name: str) -> ToolResult:
        return self._safely(lambda: (self.apple.open_app(name), f"Opened {name}.")[1])


class OpenUrlTool(_AppleTool):
    name = "open_url"
    description = "Open a link in the default browser."
    input_schema = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    side_effect = EXTERNAL

    def describe_call(self, arguments: dict) -> str:
        return f"open {arguments.get('url', '')}"

    def run(self, url: str) -> ToolResult:
        if not url.startswith(("http://", "https://")):
            return ToolResult(ok=False, output="Only http(s) links can be opened.")
        return self._safely(lambda: (self.apple.open_url(url), "Opened.")[1])


class ClipboardTool(_AppleTool):
    name = "clipboard_read"
    description = "Read the text on the clipboard (when the user refers to 'what I copied')."
    input_schema = {"type": "object", "properties": {}}
    side_effect = READ
    untrusted_output = True  # it may have been copied from anywhere

    def run(self) -> ToolResult:
        return self._safely(lambda: self.apple.clipboard() or "(the clipboard is empty)")


def apple_tools(apple: Apple) -> list[Tool]:
    return [
        RemindersListTool(apple), ReminderCreateTool(apple), ReminderCompleteTool(apple),
        CalendarListTool(apple), CalendarCreateTool(apple), NotifyTool(apple), OpenAppTool(apple),
        OpenUrlTool(apple), ClipboardTool(apple),
    ]
