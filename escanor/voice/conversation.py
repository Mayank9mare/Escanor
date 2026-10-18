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
import re
from typing import Awaitable, Callable
from ..core.agent import Agent, Confirmer
from ..core.events import Error, Event, TextDelta, TurnDone
from ..core.session import Session
from ..models.base import ToolCall
from ..core.persona import line
from .audio_out import CONFIRM, REPLY, AudioOut
from .chunker import SentenceChunker

EventSink = Callable[[Event], Awaitable[None] | None]

_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|confirm|please do|affirmative)\b", re.I)
_NO = re.compile(r"\b(no|nope|don't|do not|cancel|stop|never mind|nevermind|negative)\b", re.I)


def is_yes(answer: str) -> bool:
    """A spoken answer counts as yes only if it says yes and doesn't also say no."""
    return bool(_YES.search(answer)) and not _NO.search(answer)


async def speak_turn(agent: Agent, session: Session, text: str, audio: AudioOut | None,
                     confirmer: Confirmer | None = None, sink: EventSink | None = None) -> list[Event]:
    """Run a turn, saying the reply aloud through `audio` (spoken style) or,
    with audio=None, as a normal text turn. Returns all events. Cancelling
    the calling task stops the turn; the caller should also call
    audio.interrupt()."""
    chunker = SentenceChunker()
    events: list[Event] = []
    async for event in agent.run_turn(session, text, confirmer, spoken=audio is not None):
        events.append(event)
        if sink is not None:
            result = sink(event)
            if result is not None:
                await result
        if audio is None:
            continue
        if isinstance(event, TextDelta):
            for sentence in chunker.feed(event.text):
                audio.say(sentence, REPLY)
        elif isinstance(event, (TurnDone, Error)):
            for sentence in chunker.flush():
                audio.say(sentence, REPLY)
            if isinstance(event, Error):
                audio.say(line("error"), REPLY)
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
