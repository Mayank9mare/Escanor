"""
cli.py

`escanor` -- a text chat in the terminal, built on the same agent event
stream the web UI and voice loop will use.

Commands:
  /model [name]   show the current model, or switch (alias or provider:model-id)
  /models         list model aliases
  /new            start a new conversation
  /skills         list the skills Escanor can load
  /tools          list tool groups and whether they're on
  /help           show commands
  /quit           exit (Ctrl-D works too)

Ctrl-C while Escanor is replying stops that reply; Ctrl-C at the prompt exits.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import signal
import sys
from . import config
from .app import build_agent, build_model
from .core.agent import Agent
from .core.events import Error, ModelSwitched, TextDelta, ToolFinished, ToolStarted, TurnDone
from .core.session import Session, SessionStore, default_store
from .models.base import ToolCall
from .skills.loader import Skill

_TTY = sys.stdout.isatty()


def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if _TTY else text


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if _TTY else text


def _short_args(arguments: dict, limit: int = 80) -> str:
    text = json.dumps(arguments, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "...}"


class StdinLines:
    """Lines typed at the terminal, read without a blocking thread.

    The prompt and confirmation questions both await readline(). Reading
    on the event loop (add_reader + os.read) means a cancelled wait -- a
    confirmation interrupted by Ctrl-C -- simply stops waiting. A thread
    blocked in input() can't be cancelled and would swallow the next line
    you type (B8 in docs/escanor-core/bugs.md). Falls back to a thread
    when stdin can't be watched (e.g. redirected from a regular file),
    where Ctrl-C mid-confirmation isn't a concern."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._buffer = b""
        self._watching = False

    def start(self) -> None:
        try:
            asyncio.get_running_loop().add_reader(sys.stdin.fileno(), self._on_readable)
            self._watching = True
        except (ValueError, OSError, NotImplementedError, PermissionError, AttributeError):
            self._watching = False

    def stop(self) -> None:
        if self._watching:
            asyncio.get_running_loop().remove_reader(sys.stdin.fileno())
            self._watching = False

    def _on_readable(self) -> None:
        data = os.read(sys.stdin.fileno(), 4096)
        if not data:
            if self._buffer:
                self._queue.put_nowait(self._buffer.decode(errors="replace"))
                self._buffer = b""
            self._queue.put_nowait(None)
            self.stop()
            return
        self._buffer += data
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            self._queue.put_nowait(line.decode(errors="replace"))

    async def readline(self, prompt: str) -> str:
        """Next line, without its newline. Raises EOFError at end of input."""
        print(prompt, end="", flush=True)
        if self._watching or not self._queue.empty():
            line = await self._queue.get()
        else:
            try:
                line = await asyncio.to_thread(sys.stdin.readline)
            except (OSError, ValueError):
                line = ""
            line = line.rstrip("\n") if line else None
        if line is None:
            raise EOFError
        return line


class TerminalConfirmer:
    def __init__(self, lines: StdinLines):
        self._lines = lines

    async def confirm(self, call: ToolCall, summary: str) -> bool:
        try:
            answer = await self._lines.readline(f"\n{_bold('Allow?')} {summary} [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes")


class Repl:
    def __init__(self, agent: Agent, store: SessionStore, session: Session, settings: dict,
                 skills: dict[str, Skill] | None = None):
        self.agent = agent
        self.skills = skills or {}
        self.store = store
        self.session = session
        self.settings = settings
        self.lines = StdinLines()
        self.confirmer = TerminalConfirmer(self.lines)

    def handle_command(self, line: str) -> bool:
        """Run a /command. Returns False when the REPL should exit."""
        command, _, argument = line[1:].partition(" ")
        argument = argument.strip()
        if command in ("quit", "exit", "q"):
            return False
        if command == "help":
            print(__doc__.split("Commands:")[1].split("Ctrl-C")[0].rstrip())
        elif command == "models":
            for alias, spec in self.settings["models"]["aliases"].items():
                marker = " (current)" if spec == self.agent.model_spec else ""
                print(f"  {alias:<8} {spec}{marker}")
        elif command == "model":
            if not argument:
                print(f"  {self.agent.model_spec}")
            else:
                try:
                    model, spec = build_model(argument, self.settings)
                except (ValueError, RuntimeError) as e:
                    print(f"  {e}")
                else:
                    self.agent.set_model(model, spec)
                    print(_dim(f"  switched to {spec}"))
        elif command == "new":
            self.session = self.store.create()
            self.agent.tools.reset()
            print(_dim(f"  new conversation #{self.session.id}"))
        elif command == "skills":
            if not self.skills:
                print("  No skills found. Check [skills] dirs/allow in ~/.escanor/config.toml.")
            for skill in self.skills.values():
                print(f"  {skill.name:<16} {skill.description[:90]}")
        elif command == "tools":
            enabled = self.agent.tools.enabled_groups()
            for group, names in self.agent.tools.groups().items():
                state = "on " if group in enabled else "off"
                print(f"  [{state}] {group:<10} {', '.join(names)}")
        else:
            print(f"  Unknown command /{command}. Try /help.")
        return True

    async def run_turn(self, text: str) -> None:
        printed_text = False
        async for event in self.agent.run_turn(self.session, text, self.confirmer):
            if isinstance(event, TextDelta):
                print(event.text, end="", flush=True)
                printed_text = True
            elif isinstance(event, ToolStarted):
                print(_dim(f"\n  · {event.call.name}({_short_args(event.call.arguments)})"), flush=True)
            elif isinstance(event, ToolFinished):
                if not event.result.ok:
                    print(_dim(f"  ✗ {event.result.output[:200]}"), flush=True)
            elif isinstance(event, Error):
                print(f"\n  [error] {event.message}")
            elif isinstance(event, TurnDone):
                if event.stop_reason == "refusal":
                    print(_dim("\n  (the model declined this request)"))
            elif isinstance(event, ModelSwitched):
                print(_dim(f"\n  switched to {event.model_spec}"))
        if printed_text:
            print()

    async def run_turn_interruptible(self, text: str) -> None:
        """Run one turn; Ctrl-C cancels just this turn."""
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(self.run_turn(text))
        try:
            loop.add_signal_handler(signal.SIGINT, task.cancel)
        except (NotImplementedError, RuntimeError):
            pass  # no signal support here (e.g. not the main thread); Ctrl-C exits instead
        try:
            await task
        except asyncio.CancelledError:
            print(_dim("\n  (stopped)"))
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):
                pass

    async def loop(self) -> None:
        print(_dim(f"Escanor · {self.agent.model_spec} · conversation #{self.session.id} · /help for commands"))
        self.lines.start()
        try:
            await self._loop()
        finally:
            self.lines.stop()

    async def _loop(self) -> None:
        while True:
            try:
                line = await self.lines.readline(_bold("you › "))
            except EOFError:
                print()
                return
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not self.handle_command(line):
                    return
                continue
            print(_bold("escanor › "), end="", flush=True)
            await self.run_turn_interruptible(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="escanor", description="Escanor, your local assistant.")
    parser.add_argument("--model", help="alias or provider:model-id (default from config: haiku)")
    parser.add_argument("--continue", dest="resume", action="store_true", help="resume the latest conversation")
    args = parser.parse_args(argv)

    config.load_dotenv()
    config.load_dotenv(config.data_dir() / ".env")
    try:
        settings = config.load_settings()
        model, spec = build_model(args.model or settings["models"]["default"], settings)
    except (ValueError, RuntimeError) as e:
        print(f"escanor: {e}", file=sys.stderr)
        print("Tip: try `escanor --model mock` to run without any API key.", file=sys.stderr)
        return 2

    store = default_store()
    latest = store.latest_id() if args.resume else None
    session = store.load(latest) if latest is not None else store.create()
    agent, skills = build_agent(settings, model, spec)
    try:
        asyncio.run(Repl(agent, store, session, settings, skills).loop())
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
