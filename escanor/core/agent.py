"""
core/agent.py

The agent loop: send the conversation to the model, stream its reply
out as events, run any tools it asks for, feed the results back, and
repeat until it answers without a tool call (or a limit is hit).

It is async because a personal assistant has to do several things at
once: ask "are you sure?" mid-turn and wait for the answer, stop the
moment you interrupt it, and (later) let the DJ keep running while you
talk. The providers and tools are ordinary blocking code; astream()
and asyncio.to_thread() run them on worker threads so the event loop
stays free.

Cancelling the task that iterates run_turn() stops the turn. The
session stays valid: an unanswered tool call gets a "cancelled" result
the next time a turn starts (Session.repair()).
"""
from __future__ import annotations
import asyncio
import threading
from typing import AsyncIterator, Callable, Iterator, Protocol, TypeVar
from ..models.base import Model, ModelResponse, ToolCall
from ..tools.base import ToolResult
from ..tools.registry import ToolRegistry
from .events import Error, Event, ModelSwitched, TextDelta, ToolFinished, ToolStarted, TurnDone
from .prompts import PromptBuilder
from .session import Session

T = TypeVar("T")


class Confirmer(Protocol):
    """How an interface asks the user to approve a tool call: a y/n prompt
    in the CLI, a dialog in the web UI, a spoken "yes" in voice."""

    async def confirm(self, call: ToolCall, summary: str) -> bool: ...


class Gate(Protocol):
    """Decides whether a tool call may run (see tools/permissions.py)."""

    async def dispatch(self, call: ToolCall, tools: ToolRegistry, confirmer: Confirmer, session: Session) -> ToolResult: ...


class AllowAllGate:
    """Runs every tool without asking. Only for tests and tool-less use."""

    async def dispatch(self, call: ToolCall, tools: ToolRegistry, confirmer: Confirmer, session: Session) -> ToolResult:
        return await asyncio.to_thread(tools.dispatch, call)


class DenyConfirmer:
    """A Confirmer that always says no -- the safe default."""

    async def confirm(self, call: ToolCall, summary: str) -> bool:
        return False


async def astream(make_iterator: Callable[[], Iterator[T]]) -> AsyncIterator[T]:
    """Consume a blocking iterator on a worker thread, yielding its items
    to async code as they arrive. If the consumer stops early (break,
    cancellation), the worker stops at the next item and closes the
    iterator, which closes the provider's HTTP stream."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()

    def put(kind: str, value: object) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, (kind, value))
        except RuntimeError:
            pass  # the event loop is gone; nobody is listening any more

    def worker() -> None:
        iterator = make_iterator()
        try:
            for item in iterator:
                if stop.is_set():
                    break
                put("item", item)
            put("end", None)
        except BaseException as e:  # noqa: BLE001 -- re-raised on the async side
            put("error", e)
        finally:
            close = getattr(iterator, "close", None)
            if close:
                close()

    loop.run_in_executor(None, worker)
    try:
        while True:
            kind, value = await queue.get()
            if kind == "item":
                yield value  # type: ignore[misc]
            elif kind == "end":
                return
            else:
                raise value  # type: ignore[misc]
    finally:
        stop.set()


class Agent:
    def __init__(self, model: Model, model_spec: str, tools: ToolRegistry | None = None,
                 prompts: PromptBuilder | None = None, gate: Gate | None = None, max_steps: int = 8):
        self.model = model
        self.model_spec = model_spec  # "provider:model-id", recorded on each assistant turn
        self.tools = tools or ToolRegistry()
        self.prompts = prompts or PromptBuilder()
        self.gate: Gate = gate or AllowAllGate()
        self.max_steps = max_steps
        self._pending_switch: tuple[Model, str] | None = None

    def set_model(self, model: Model, model_spec: str) -> None:
        """Switch models between turns. The session's history is fixed up
        for the new model at the start of the next turn."""
        self.model = model
        self.model_spec = model_spec

    def request_model_switch(self, model: Model, model_spec: str) -> None:
        """Switch once the current turn ends (used by the switch_model tool),
        so a turn never sends one model's in-flight tool calls to another."""
        self._pending_switch = (model, model_spec)

    def _apply_pending_switch(self) -> str | None:
        if self._pending_switch is None:
            return None
        model, spec = self._pending_switch
        self._pending_switch = None
        self.set_model(model, spec)
        return spec

    def tool_schemas(self) -> list[dict]:
        return self.tools.schemas()

    async def run_turn(self, session: Session, user_text: str, confirmer: Confirmer | None = None,
                       spoken: bool = False) -> AsyncIterator[Event]:
        """One turn. spoken=True asks for a reply suited to being read aloud."""
        try:
            async for event in self._steps(session, user_text, confirmer or DenyConfirmer(), spoken):
                yield event
        finally:
            switched_to = self._apply_pending_switch()
        if switched_to:
            yield ModelSwitched(switched_to)

    async def _steps(self, session: Session, user_text: str, confirmer: Confirmer, spoken: bool) -> AsyncIterator[Event]:
        session.repair()
        session.collapse_tool_history(self.model_spec)
        session.append_user(user_text)
        usage = {"input_tokens": 0, "output_tokens": 0}

        for _ in range(self.max_steps):
            messages = self.prompts.build(session, spoken)
            schemas = self.tool_schemas() or None
            response: ModelResponse | None = None
            try:
                async for event in astream(lambda: self.model.chat_stream_tools(messages, schemas)):
                    if event.kind == "text" and event.text:
                        yield TextDelta(event.text)
                    elif event.kind == "done":
                        response = event.response
            except RuntimeError as e:
                yield Error(str(e))
                return
            if response is None:
                yield Error("The model ended its reply without finishing it.")
                return

            usage["input_tokens"] += response.input_tokens or 0
            usage["output_tokens"] += response.output_tokens or 0

            if response.stop_reason == "max_tokens" and response.tool_calls:
                # A tool call cut off mid-arguments can't be trusted to run.
                # Keep the text, drop the half-written calls.
                session.append_assistant(ModelResponse(text=response.text, stop_reason="max_tokens"), self.model_spec)
                yield Error("The reply was cut off before its tool call was complete.")
                return

            session.append_assistant(response, self.model_spec)
            if not response.tool_calls or response.stop_reason == "refusal":
                yield TurnDone(stop_reason=response.stop_reason, usage=usage)
                return

            for call in response.tool_calls:
                yield ToolStarted(call)
                result = await self.gate.dispatch(call, self.tools, confirmer, session)
                session.append_tool_result(call, result)
                yield ToolFinished(call, result)

        yield Error(f"Stopped after {self.max_steps} tool steps without a final answer.")

