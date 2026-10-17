"""
core/prompts.py

Builds the message list for each model call: one system message, then
the conversation. The system prompt is assembled from sections so later
phases (skills index, brain facts, voice style) can add their own
without editing this file.

Caching note: Claude reuses its processing of an unchanged prompt
prefix (see models/anthropic_provider.py), and the system prompt is
the front of that prefix. So nothing here may change between turns of a
conversation: no clock time (the model calls get_time instead), only
the date, which changes at most once a day.
"""
from __future__ import annotations
from datetime import date
from typing import Callable
from .session import Session

PERSONA = """\
You are Escanor, a personal assistant that runs on the user's own Mac.

How to answer:
- Be direct and concise. Lead with the answer; offer detail only if asked.
- Say plainly when you don't know something or a tool failed. Never invent facts,
  sources, or tool results.
- Use your tools when they help: look things up rather than guess, and prefer
  one well-chosen call over many.
- Anything that acts outside this conversation (sending, creating, deleting,
  controlling apps) needs the user's confirmation; the system asks for it, so
  just call the tool and respect the answer."""

Section = Callable[[], str]


class PromptBuilder:
    def __init__(self, persona: str = PERSONA, sections: list[Section] | None = None,
                 today: Callable[[], date] = date.today):
        self.persona = persona
        self.sections: list[Section] = list(sections or [])
        self._today = today

    def add_section(self, section: Section) -> None:
        self.sections.append(section)

    def system_prompt(self) -> str:
        parts = [self.persona, f"Today's date: {self._today().isoformat()}."]
        parts += [text for text in (section() for section in self.sections) if text]
        return "\n\n".join(parts)

    def build(self, session: Session) -> list[dict]:
        return [{"role": "system", "content": self.system_prompt()}, *session.messages]
