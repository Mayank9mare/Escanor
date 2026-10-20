"""
voice/conversation.py

Glue between the agent's event stream and the speaker:

- speak_turn(): runs one agent turn in spoken style and says each
  sentence as soon as it's complete (SentenceChunker -> AudioOut), while
  passing every event on to the interface (web UI, terminal) for display.
- VoiceConfirmer: asks "Should I ...?" out loud and listens for yes/no,
  for hands-free use.
"""
from __future__ import annotations
import asyncio
import re
import time
from typing import Awaitable, Callable
from ..core.agent import Agent, Confirmer
from ..core.events import Error, Event, TextDelta, ToolStarted, TurnDone
from ..core.session import Session
from ..models.base import ToolCall
from .. import telemetry
from ..core.persona import line
from .audio_out import CONFIRM, REPLY, AudioOut
from .chunker import SentenceChunker

EventSink = Callable[[Event], Awaitable[None] | None]

# Tools that go out to the web or read documents: seconds of silence, so
# Escanor says it's working (once a turn) instead of going quiet.
SLOW_TOOLS = {"web_search", "web_fetch", "news_headlines", "research_ingest", "research_read", "look_at_screen"}

_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|confirm|please do|affirmative)\b", re.I)
_NO = re.compile(r"\b(no|nope|don't|do not|cancel|stop|never mind|nevermind|negative)\b", re.I)


def is_yes(answer: str) -> bool:
    """A spoken answer counts as yes only if it says yes and doesn't also say no."""
    return bool(_YES.search(answer)) and not _NO.search(answer)


async def speak_turn(agent: Agent, session: Session, text: str, audio: AudioOut | None,
                     confirmer: Confirmer | None = None, sink: EventSink | None = None,
                     patience: float | None = None, source: str = "voice", trace_id: str | None = None) -> list[Event]:
    """Run a turn, saying the reply aloud through `audio` (spoken style) or,
    with audio=None, as a normal text turn. Returns all events. Cancelling
    the calling task stops the turn; the caller should also call
    audio.interrupt().

    patience: if no reply has started after this many seconds, say "I'll
    tell you when it's ready" (the listen loop then goes back to waiting
    for you), and introduce the answer when it comes. The turn itself keeps
    running. Idea from JBIQ's deferred replies."""
    chunker = SentenceChunker()
    events: list[Event] = []
    said_working = False
    trace = trace_id or telemetry.new_trace_id()
    if audio is not None and hasattr(audio, "mark_turn"):
        audio.mark_turn(trace, time.monotonic())
    say = (lambda text: audio.say(text, REPLY, trace=trace)) if audio is not None else None
    late = {"deferred": False, "started": False}

    async def watch() -> None:
        await asyncio.sleep(patience)
        if not late["started"]:
            late["deferred"] = True
            say(line("deferred"))

    watchdog = asyncio.ensure_future(watch()) if (audio is not None and patience) else None
    try:
        async for event in agent.run_turn(session, text, confirmer, spoken=audio is not None,
                                              source=source, trace_id=trace):
            events.append(event)
            if sink is not None:
                result = sink(event)
                if result is not None:
                    await result
            if audio is None:
                continue
            if isinstance(event, TextDelta):
                if not late["started"]:
                    late["started"] = True
                    if watchdog is not None:
                        watchdog.cancel()
                    if late["deferred"]:
                        say(line("deferred_ready"))
                for sentence in chunker.feed(event.text):
                    say(sentence)
            elif isinstance(event, ToolStarted) and event.call.name in SLOW_TOOLS and not said_working:
                said_working = True
                for sentence in chunker.flush():   # anything said before the tool first
                    say(sentence)
                say(line("working"))
            elif isinstance(event, (TurnDone, Error)):
                for sentence in chunker.flush():
                    say(sentence)
                if isinstance(event, Error):
                    say(line("error"))
    finally:
        if watchdog is not None:
            watchdog.cancel()
    return events


class VoiceConfirmer:
    """Says the question at confirmation priority, then waits for a spoken answer."""

    def __init__(self, audio: AudioOut, listen: Callable[[], Awaitable[str]]):
        self._audio = audio
        self._listen = listen

    async def confirm(self, call: ToolCall, summary: str) -> bool:
        await self._audio.say(line("confirm", summary=summary) + " Say yes or no.", CONFIRM)
        answer = await self._listen()
        return is_yes(answer)
