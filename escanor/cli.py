"""
cli.py

`escanor` -- a text chat in the terminal, built on the same agent event
stream the web UI and voice loop use. Subcommands: `escanor serve` (web
UI + push-to-talk), `escanor listen` (hands-free), `escanor brain reindex`.

Commands:
  /model [name]   show the current model, or switch (alias or provider:model-id)
  /models         list model aliases
  /new            start a new conversation
  /skills         list the skills Escanor can load
  /tools          list tool groups and whether they're on
  /brain          re-index the second brain and show its size
  /mcp            connected MCP servers (Gmail, Notion, GitHub, ...)
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
from pathlib import Path
from . import config
from .app import Escanor, build_app, build_model
from .core.events import Error, ModelSwitched, TextDelta, ToolFinished, ToolStarted, TurnDone
from .core.session import Session, SessionStore, default_store
from .models.base import ToolCall

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
    def __init__(self, app: Escanor, store: SessionStore, session: Session):
        self.app = app
        self.agent = app.agent
        self.skills = app.skills
        self.settings = app.settings
        self.store = store
        self.session = session
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
        elif command == "brain":
            counts = self.app.index.sync()
            stats = self.app.index.stats()
            print(f"  {self.app.vault.root} · {stats['files']} notes, {stats['chunks']} chunks, "
                  f"{stats['embedded']} embedded ({self.app.index.embedder.name}) · "
                  f"{len(self.app.vault.facts())} facts · {counts['changed']} re-indexed")
        elif command == "mcp":
            manager = self.app.services.get("mcp")
            for line in (manager.status_lines() if manager else ["No MCP servers configured ([mcp.servers.*])."]):
                print(f"  {line}")
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
        async for event in self.agent.run_turn(self.session, text, self.confirmer, source="chat"):
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

    async def loop(self, first_message: str | None = None) -> None:
        await self.app.start()
        try:
            await self._session_loop(first_message)
        finally:
            await self.app.stop()

    async def _session_loop(self, first_message: str | None) -> None:
        print(_dim(f"Escanor · {self.agent.model_spec} · conversation #{self.session.id} · /help for commands"))
        if first_message:
            print(_bold("escanor › "), end="", flush=True)
            await self.run_turn_interruptible(first_message)
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


def eval_command(app: Escanor, delay: float = 0.0) -> int:
    import tempfile
    from .core.session import SessionStore
    from .ops import run_eval
    print(f"Evaluating tool choice on {app.agent.model_spec} (stubbed tools; real model calls)…")
    with tempfile.TemporaryDirectory() as scratch:
        results = asyncio.run(run_eval(app.agent, SessionStore(Path(scratch) / "eval.db"), delay=delay))
    for case, called, passed in results:
        expected = " or ".join(case.expect) or "no tool"
        label = "ERROR" if passed is None else ("PASS" if passed else "FAIL")
        print(f"{label:<5}  {case.prompt[:50]:<50}  expected {expected}; called {', '.join(called) or 'nothing'}")
    graded = [p for _, _, p in results if p is not None]
    errors = len(results) - len(graded)
    print(f"{sum(graded)}/{len(graded)} passed" + (f", {errors} errored (not graded; try --delay)" if errors else ""))
    return 0 if graded and all(graded) and not errors else 1


def usage_command(settings: dict, days: int) -> int:
    from datetime import datetime, timedelta
    from . import telemetry
    from .core.costs import cost, start_of_local_day_utc
    since = start_of_local_day_utc(datetime.now() - timedelta(days=max(1, days) - 1))
    total, rows = cost(telemetry.get_store().usage_since(since), settings["budget"].get("prices", {}))
    label = "today" if days <= 1 else f"last {days} days"
    if not rows:
        print(f"No model calls {label}.")
        return 0
    print(f"{'provider/model':<40} {'calls':>6} {'in tok':>10} {'out tok':>9} {'avg s':>6} {'US$':>8}")
    for r in rows:
        usd = f"{r['usd']:.4f}" if r["usd"] is not None else "?"
        print(f"{r['provider'] + '/' + r['model']:<40} {r['calls']:>6} {r['input_tokens']:>10,} "
              f"{r['output_tokens']:>9,} {r['avg_duration_ms'] / 1000:>6.1f} {usd:>8}")
    cap = float(settings["budget"].get("daily_usd", 0))
    print(f"Total {label}: ${total:.4f}" + (f" (daily cap ${cap:.2f})" if cap > 0 else "")
          + ("  · '?' = no price set ([budget.prices])" if any(r["usd"] is None for r in rows) else ""))
    return 0


def music_command(app: Escanor, action: str) -> int:
    from .music.player import PlayerNotAuthorized, PlayerUnavailable
    player, library = app.services.get("player"), app.services.get("library")
    if player is None:
        print("Music is disabled ([music] enabled = false).")
        return 1
    if action == "status":
        try:
            song = player.song()
            print(f"Player: connected. {song.describe() if song else 'Nothing playing.'}")
        except (PlayerUnavailable, PlayerNotAuthorized) as e:
            print(f"Player: {e}")
        print(f"Library: {'signed in' if library.has_account else 'search only (no history/likes)'} ({library.auth_path})")
        return 0

    print("1/2  Connecting to the YouTube Music app. Click 'Allow' in the dialog it shows…")
    try:
        player.authorize()
        print("     Connected.")
    except (PlayerUnavailable, PlayerNotAuthorized, RuntimeError) as e:
        print(f"     {e}")
    print("2/2  Optional: sign in for your history and liked songs (used by the DJ).")
    print("     In your browser, open music.youtube.com signed in, open DevTools → Network, click any")
    print("     request to 'browse', copy its request headers, and paste them below (Ctrl-D to finish).")
    print("     Skip with Ctrl-C. These cookies expire after days to weeks; re-run this when they do.")
    import shutil
    import subprocess
    tool = shutil.which("ytmusicapi") or str(Path(sys.executable).with_name("ytmusicapi"))
    try:
        subprocess.run([tool, "browser", "--file", str(library.auth_path)], check=False)
    except (KeyboardInterrupt, OSError) as e:
        print(f"     Skipped ({e.__class__.__name__}).")
    if library.auth_path.exists():
        library.auth_path.chmod(0o600)
        print(f"     Saved to {library.auth_path}.")
    return 0


def _tailscale() -> str | None:
    import shutil
    app = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    return shutil.which("tailscale") or (app if Path(app).exists() else None)


def _tailscale_name(tool: str) -> str | None:
    """This Mac's Tailscale HTTPS name, e.g. 'mac.tail1234.ts.net'."""
    import subprocess
    try:
        out = subprocess.run([tool, "status", "--json"], capture_output=True, text=True, timeout=10)
        return (json.loads(out.stdout).get("Self", {}).get("DNSName") or "").rstrip(".") or None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _print_qr(link: str) -> None:
    try:
        import qrcode
    except ImportError:
        print(_dim("  (for a QR code here: uv pip install -e '.[remote]')"))
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(link)
    qr.print_ascii(invert=True)


def pair_command(settings: dict, rotate: bool = False, off: bool = False) -> int:
    import subprocess
    from .server import remote
    if off:
        print("Phone access is off; paired devices are unpaired." if remote.remove_token()
              else "Phone access was already off.")
        return 0
    token = remote.load_token()
    if token is None or rotate:
        token = remote.new_token()
        print("Every device paired before is now unpaired." if rotate else "Phone access is on.")
    port = int(settings["server"]["port"])
    tool = _tailscale()
    if tool is None:
        print("Escanor reaches your phone through Tailscale, a free private network between your devices.")
        print("  1. Install Tailscale on this Mac (https://tailscale.com/download) and on your phone,")
        print("     signed in to the same account.")
        print("  2. Run `escanor pair` again.")
        return 1
    name = _tailscale_name(tool)
    if name is None:
        print("Tailscale is installed but not connected. Open it, sign in, then run `escanor pair` again.")
        return 1
    serving = subprocess.run([tool, "serve", "status"], capture_output=True, text=True).stdout
    if f"127.0.0.1:{port}" not in serving and f"localhost:{port}" not in serving:
        print(f"Tailscale needs to pass https://{name} on to Escanor (port {port}), for your devices only.")
        answer = input(f"Run `tailscale serve --bg {port}` now? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print(f"Run it yourself when ready: tailscale serve --bg {port}")
            return 1
        result = subprocess.run([tool, "serve", "--bg", str(port)])
        if result.returncode != 0:
            print("That didn't work (HTTPS certificates may need turning on in the Tailscale admin console).")
            return 1
    link = f"https://{name}/?pair={token}"
    print(f"\nOn your phone, open (or scan):\n  {link}\n")
    _print_qr(link)
    print("Then Share → Add to Home Screen for an app icon. Keep `escanor serve` (or the menubar) running.")
    print(_dim("This link is a key: don't share it. `escanor pair --rotate` unpairs every device."))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="escanor", description="Escanor, your local assistant.")
    parser.add_argument("--model", help="alias or provider:model-id (default from config: haiku)")
    parser.add_argument("--continue", dest="resume", action="store_true", help="resume the latest conversation")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("chat", help="text chat in the terminal (the default)")
    serve_cmd = sub.add_parser("serve", help="web UI with push-to-talk at http://127.0.0.1:8770")
    serve_cmd.add_argument("--port", type=int, help="port (default from config: 8770)")
    serve_cmd.add_argument("--no-voice", action="store_true", help="text only; don't load speech models")
    serve_cmd.add_argument("--mute", action="store_true", help="load speech-to-text but never play speech")
    listen_cmd = sub.add_parser("listen", help="hands-free: wake word, then talk")
    listen_cmd.add_argument("--enter", action="store_true", help="press Enter instead of saying the wake word")
    brain_cmd = sub.add_parser("brain", help="second brain maintenance")
    brain_cmd.add_argument("action", choices=["reindex", "stats", "backup"])
    service_cmd = sub.add_parser("service", help="start `escanor serve` at login (launchd)")
    service_cmd.add_argument("action", choices=["install", "uninstall", "status"])
    service_cmd.add_argument("--menubar", action="store_true", help="run the menu-bar sun at login (it starts the server)")
    eval_cmd = sub.add_parser("eval", help="check each capability picks the right tool (real model calls)")
    eval_cmd.add_argument("--delay", type=float, default=0.0, help="seconds between cases (for rate-limited free tiers)")
    sub.add_parser("brief", help="your morning briefing, then the chat")
    menubar_cmd = sub.add_parser("menubar", help="a sun in the menu bar showing Escanor's state")
    menubar_cmd.add_argument("--no-server", action="store_true", help="don't start `escanor serve` if it isn't running")
    usage_cmd = sub.add_parser("usage", help="model calls, tokens and cost")
    usage_cmd.add_argument("--days", type=int, default=1, help="how many days back (default: today)")
    pair_cmd = sub.add_parser("pair", help="use Escanor from your phone (over Tailscale)")
    pair_group = pair_cmd.add_mutually_exclusive_group()
    pair_group.add_argument("--rotate", action="store_true", help="new link; unpairs every device")
    pair_group.add_argument("--off", action="store_true", help="turn phone access off")
    trace_cmd = sub.add_parser("trace", help="recent turns, or one turn as a timeline (where the seconds went)")
    trace_cmd.add_argument("id", nargs="?", help="a turn id (any unique prefix)")
    trace_cmd.add_argument("-n", type=int, default=20, help="how many turns to list (default 20)")
    trace_cmd.add_argument("--slow", action="store_true", help="only turns over 10 s")
    doctor_cmd = sub.add_parser("doctor", help="check keys, voices, mic, server, and every model you have a key for")
    doctor_cmd.add_argument("--no-models", action="store_true", help="skip the live model checks")
    mcp_cmd = sub.add_parser("mcp", help="MCP connectors: accept a server's changed tools")
    mcp_cmd.add_argument("action", choices=["trust"])
    mcp_cmd.add_argument("server", help="the [mcp.servers.<name>] whose current tools you accept")
    music_cmd = sub.add_parser("music", help="connect YouTube Music")
    music_cmd.add_argument("action", choices=["connect", "status"])
    args = parser.parse_args(argv)

    config.load_dotenv()
    config.load_dotenv(config.data_dir() / ".env")
    if args.command == "trace":  # reads telemetry only
        from .trace_view import main as show_trace
        return show_trace(args.id, args.n, args.slow)
    if args.command == "doctor":  # checks models itself, so it needn't build the default one
        from .doctor import run as run_doctor
        settings = config.load_settings()
        return run_doctor(settings, lambda name: build_model(name, settings), models=not args.no_models)
    if args.command == "mcp":  # no model needed: it only edits the pins
        from .connectors.mcp_client import Pins
        pins = Pins(config.data_dir() / "mcp_pins.json")
        if pins.trust(args.server):
            pins.save()
            print(f"OK: {args.server}'s tools will be accepted as they are now, the next time Escanor starts.")
        else:
            print(f"No pinned tools for '{args.server}' yet; nothing to do.")
        return 0
    if args.command == "pair":  # needs no model either
        return pair_command(config.load_settings(), rotate=args.rotate, off=args.off)
    if args.command == "menubar":  # needs no model or key: it only talks to the local server
        from .menubar import run as run_menubar
        try:
            run_menubar(config.load_settings(), start_server=not args.no_server)
        except RuntimeError as e:
            print(f"escanor: {e}", file=sys.stderr)
            return 2
        return 0
    try:
        settings = config.load_settings()
        model, spec = build_model(args.model or settings["models"]["default"], settings)
    except (ValueError, RuntimeError) as e:
        print(f"escanor: {e}", file=sys.stderr)
        print("Tip: try `escanor --model mock` to run without any API key.", file=sys.stderr)
        return 2

    store = default_store()
    app = build_app(settings, model, spec)

    if args.command == "service":
        from .ops import service
        return service(args.action, menubar=args.menubar)

    if args.command == "eval":
        return eval_command(app, args.delay)

    if args.command == "brain" and args.action == "backup":
        from .ops import backup_brain
        print(backup_brain(app.vault.root))
        return 0

    if args.command == "brain":
        counts = app.index.sync() if args.action == "reindex" else {"changed": 0, "removed": 0}
        stats = app.index.stats()
        print(f"{app.vault.root}: {stats['files']} notes, {stats['chunks']} chunks, {stats['embedded']} embedded "
              f"({app.index.embedder.name}); re-indexed {counts['changed']}, removed {counts['removed']}")
        return 0

    if args.command == "music":
        return music_command(app, args.action)

    if args.command == "usage":
        return usage_command(settings, args.days)

    if args.command in ("serve", "listen"):
        from .voice import runtime
        speech = None
        if not (args.command == "serve" and args.no_voice):
            print("Loading speech models…", flush=True)
            try:
                speech = runtime.build_speech(settings)
            except (RuntimeError, ValueError) as e:
                print(f"escanor: {e}", file=sys.stderr)
                return 2
        try:
            if args.command == "serve":
                if args.port:
                    settings["server"]["port"] = args.port
                runtime.serve(app, store, speech, mute=args.mute)
            else:
                runtime.listen(app, store, speech, use_enter=args.enter)
        except RuntimeError as e:  # e.g. a missing optional install: say what to do, no traceback
            print(f"escanor: {e}", file=sys.stderr)
            return 2
        return 0

    latest = store.latest_id() if args.resume else None
    session = store.load(latest) if latest is not None else store.create()
    timers = app.services.get("timers")
    if timers is not None:
        timers.announcers.append(lambda message: print(f"\n⏰ {message}", flush=True))
    first = "Good morning! Give me my briefing." if args.command == "brief" else None
    try:
        asyncio.run(Repl(app, store, session).loop(first_message=first))
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
