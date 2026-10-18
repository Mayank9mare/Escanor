"""
server/hub.py

What the server shares between connections: the conversations in use,
the pages showing them, and the turn running on each.

Before the hotkeys, every turn came from one page and went back to that
page. Now a turn can start on the Mac itself (hold ⌃⌥Space and talk, or
⌃⌥E to explain a selection), so:

- one Session object per conversation, shared by every page showing it
  (two objects for one conversation would each append and overwrite the
  other's messages);
- every turn's events go to all pages showing that conversation;
- at most one turn per conversation: a new one -- from a page or a
  hotkey -- cancels the running one, just as typing over Escanor did.
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Coroutine
from ..core.session import Session, SessionStore

log = logging.getLogger(__name__)


@dataclass(eq=False)
class Viewer:
    """One connected page."""
    session_id: int
    send: Callable[[dict], Awaitable[None]]
    confirmer: Any = None
    local_speech: bool = False   # speech plays on the Mac (it gets the lip-sync envelope)


class Hub:
    def __init__(self, store: SessionStore):
        self.store = store
        self._sessions: dict[int, Session] = {}
        self._viewers: list[Viewer] = []
        self._turns: dict[int, tuple[asyncio.Task, Viewer | None]] = {}

    # ---- conversations ----

    def session(self, wanted: str = "latest") -> Session:
        """'new', 'latest' or a conversation id (unknown ids start a new one)."""
        latest = self.store.latest_id()
        if wanted == "new" or latest is None:
            return self._keep(self.store.create())
        if wanted == "latest":
            return self._load(latest)
        try:
            return self._load(int(wanted))
        except (KeyError, ValueError):
            return self._keep(self.store.create())

    def _load(self, session_id: int) -> Session:
        if session_id not in self._sessions:
            self._sessions[session_id] = self.store.load(session_id)
        return self._sessions[session_id]

    def _keep(self, session: Session) -> Session:
        self._sessions[session.id] = session
        return session

    # ---- pages ----

    def join(self, viewer: Viewer) -> None:
        self._viewers.append(viewer)

    def leave(self, viewer: Viewer) -> None:
        if viewer in self._viewers:
            self._viewers.remove(viewer)

    def viewers_of(self, session_id: int) -> list[Viewer]:
        return [v for v in self._viewers if v.session_id == session_id]

    async def _send(self, viewers: list[Viewer], message: dict) -> None:
        for viewer in viewers:
            try:
                await viewer.send(message)
            except Exception:  # noqa: BLE001 -- a page that just closed mustn't break the others
                pass

    async def broadcast(self, session_id: int, message: dict) -> None:
        await self._send(self.viewers_of(session_id), message)

    async def broadcast_local(self, message: dict) -> None:
        """To every page whose speech plays on the Mac (speaking, lip-sync, listening)."""
        await self._send([v for v in self._viewers if v.local_speech], message)

    async def wait_for_viewer(self, session_id: int, timeout: float) -> bool:
        """Wait (up to `timeout` s) for a page to open this conversation."""
        deadline = asyncio.get_running_loop().time() + timeout
        while not self.viewers_of(session_id):
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.05)
        return True

    # ---- turns ----

    @property
    def busy(self) -> bool:
        return any(not task.done() for task, _ in self._turns.values())

    async def cancel(self, session_id: int) -> None:
        entry = self._turns.pop(session_id, None)
        if entry is None or entry[0].done():
            return
        entry[0].cancel()
        try:
            await entry[0]
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    async def cancel_owned_by(self, viewer: Viewer) -> int:
        """Stop the turns this page started; returns how many were running."""
        running = [sid for sid, (task, owner) in list(self._turns.items()) if owner is viewer and not task.done()]
        for session_id in running:
            await self.cancel(session_id)
        return len(running)

    async def start(self, session_id: int, turn: Coroutine, owner: Viewer | None = None) -> asyncio.Task:
        """Run `turn` as this conversation's turn, cancelling any running one.
        `owner` is the page that asked: its turn stops if it disconnects
        (a hotkey turn has no owner and finishes regardless)."""
        await self.cancel(session_id)
        task = asyncio.get_running_loop().create_task(turn)
        self._turns[session_id] = (task, owner)

        def forget(done: asyncio.Task) -> None:
            if self._turns.get(session_id, (None,))[0] is done:
                del self._turns[session_id]
        task.add_done_callback(forget)
        return task
