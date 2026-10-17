"""
cli.py

`escanor` -- a text chat in the terminal, built on the same agent event
stream the web UI and voice loop will use.

Commands:
  /model [name]   show the current model, or switch (alias or provider:model-id)
  /models         list model aliases
  /new            start a new conversation
  /help           show commands
  /quit           exit (Ctrl-D works too)

Ctrl-C while Escanor is replying stops that reply; Ctrl-C at the prompt exits.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import signal
import sys
from . import config
from .core.agent import Agent
from .core.events import Error, TextDelta, ToolFinished, ToolStarted, TurnDone
from .core.session import Session, SessionStore, default_store
from .models.base import Model, ToolCall
from .models.registry import get_model

_TTY = sys.stdout.isatty()


def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if _TTY else text


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if _TTY else text


def _short_args(arguments: dict, limit: int = 80) -> str:
    text = json.dumps(arguments, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "...}"


class TerminalConfirmer:
    async def confirm(self, call: ToolCall, summary: str) -> bool:
        answer = await asyncio.to_thread(input, f"\n{_bold('Allow?')} {summary} [y/N] ")
        return answer.strip().lower() in ("y", "yes")


def build_model(name: str, settings: dict) -> tuple[Model, str]:
    """Resolve an alias/spec and construct the model. Raises ValueError for
    an unknown name, RuntimeError for a missing key or package."""
    provider, model_id = config.resolve_model(name, settings)
    model = get_model(provider, model_id, role="chat", effort=settings["models"].get("effort"))
    return model, f"{provider}:{model_id}"


class Repl:
    def __init__(self, agent: Agent, store: SessionStore, session: Session, settings: dict):
        self.agent = agent
        self.store = store
        self.session = session
        self.settings = settings
        self.confirmer = TerminalConfirmer()

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
            print(_dim(f"  new conversation #{self.session.id}"))
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
        while True:
            try:
                line = await asyncio.to_thread(input, _bold("you › "))
            except (EOFError, KeyboardInterrupt):
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
    agent = Agent(model, spec, max_steps=settings["agent"]["max_steps"])
    try:
        asyncio.run(Repl(agent, store, session, settings).loop())
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
