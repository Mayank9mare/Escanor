"""
assistant/briefing.py

"Good morning" / "brief me": one tool that gathers the day at a glance
and hands it to the model to summarise -- today's events, open reminders
(overdue and due today first), running timers, the weather if
[assistant] city is set, the latest journal entry, and anything later
phases register (e.g. research topics due for review).

Each source is independent: one failing (Calendar permission missing,
no network) becomes a one-line note instead of breaking the briefing.
"""
from __future__ import annotations
from datetime import datetime
from typing import Callable
from ..tools.base import READ, Tool, ToolResult
from ..tools.permissions import fence_untrusted

Source = tuple[str, Callable[[], str]]


class Briefing:
    def __init__(self, now: Callable[[], datetime] = datetime.now):
        self.now = now
        self.sources: list[Source] = []

    def add(self, title: str, fetch: Callable[[], str]) -> None:
        self.sources.append((title, fetch))

    def build(self) -> str:
        parts = [f"Now: {self.now():%A %d %B %Y, %H:%M}"]
        for title, fetch in self.sources:
            try:
                text = fetch().strip()
            except Exception as e:  # noqa: BLE001
                text = f"(unavailable: {e})"
            if text:
                parts.append(f"## {title}\n{text}")
        return "\n\n".join(parts)


def due_today_first(reminders: list[dict], today: str) -> str:
    """Overdue and due-today reminders first, then the rest (max 10)."""
    def rank(r: dict) -> tuple:
        due = r.get("due") or ""
        return (0 if due and due[:10] <= today else 1, due or "9999")
    lines = []
    for r in sorted(reminders, key=rank)[:10]:
        due = r.get("due") or ""
        flag = " (overdue)" if due and due[:10] < today else (" (today)" if due[:10] == today else "")
        lines.append(f"- {r['title']}{flag}")
    return "\n".join(lines) or "Nothing open."


def weather_source(search: Callable[[str], str], city: str) -> Callable[[], str]:
    def fetch() -> str:
        return fence_untrusted("web_search", search(f"weather today {city}")[:1200])
    return fetch


class BriefingTool(Tool):
    name = "morning_briefing"
    description = (
        "Gather today's briefing: date, calendar, reminders, timers, weather, latest journal entry and "
        "reviews due. Use it for 'good morning', 'brief me' or 'what's my day like'. Summarise the result "
        "warmly and briefly, most important first."
    )
    input_schema = {"type": "object", "properties": {}}
    side_effect = READ

    def __init__(self, briefing: Briefing):
        self.briefing = briefing

    def run(self) -> ToolResult:
        return ToolResult(ok=True, output=self.briefing.build())
