"""
server/desk.py

Turns started on the Mac rather than from a page -- the "desk" -- by the
menubar's hotkeys:

  ⌃⌥Space (hold)  push-to-talk: the transcript becomes a spoken turn
  ⌃⌥E             explain the selected text (or file) in the HUD

Selected text is someone else's words (a web page, a PDF, an email), so
it goes in fenced as untrusted: the model treats it as data, and the turn
counts as tainted, so nothing in it can quietly write to memory or run
an allowed shortcut (permissions.turn_read_untrusted).
"""
from __future__ import annotations
import re
from typing import Awaitable, Callable
from ..models.base import ToolCall
from ..tools.permissions import fence_untrusted
from .hub import Hub

SELECTION = "selection"
MAX_SELECTION = 20_000

_FENCE = re.compile(r'<untrusted source="[^"]*">\n?(.*?)\n?</untrusted>', re.S)


def explain_prompt(text: str = "", path: str = "") -> str:
    """The turn sent for ⌃⌥E: a short instruction, then the selection, fenced."""
    if path:
        what = f"the file I selected (read it with research_ingest):\n{fence_untrusted(SELECTION, path)}"
    else:
        clipped = text.strip()[:MAX_SELECTION]
        what = f"this, which I selected:\n{fence_untrusted(SELECTION, clipped)}"
    return (
        "Explain " + what + "\n\n"
        "Start with the core idea in a sentence or two of plain words, then build up the details, "
        "with an example if it helps. If one of my learning skills fits, use it. "
        "End by offering to save it to my brain."
    )


def display_text(user_text: str, limit: int = 160) -> str:
    """How a user message shows in the HUD: fenced selections become a
    short quote instead of the raw prompt with its tags."""
    match = _FENCE.search(user_text)
    if not match:
        return user_text
    quoted = " ".join(match.group(1).split())
    quoted = quoted if len(quoted) <= limit else quoted[:limit].rstrip() + "…"
    return f"Explain: “{quoted}”"


class DeskConfirmer:
    """Asks on a page showing the conversation if there is one (the dialog
    also speaks the question), else out loud with a spoken yes/no, else
    declines."""

    def __init__(self, hub: Hub, session_id: int, voice: Callable[[ToolCall, str], Awaitable[bool]] | None):
        self._hub = hub
        self._session_id = session_id
        self._voice = voice

    async def confirm(self, call: ToolCall, summary: str) -> bool:
        for viewer in self._hub.viewers_of(self._session_id):
            if viewer.confirmer is not None:
                return await viewer.confirmer.confirm(call, summary)
        if self._voice is not None:
            return await self._voice(call, summary)
        return False
