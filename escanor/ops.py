"""
ops.py

Housekeeping commands, all run explicitly by you:

  escanor service install|uninstall|status   start `escanor serve` at login (launchd)
  escanor brain backup                        commit the brain folder to a local git repo
  escanor eval [--model X]                    check each capability picks the right tool

eval never runs real tools: the gate answers every call with a stub, so
it can't touch your notes, calendar or music. It does make real model
calls, so it costs a few cents.
"""
from __future__ import annotations
import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from . import config
from .core.agent import Agent
import asyncio
from .core.events import Error, ToolStarted
from .core.session import SessionStore
from .models.base import ToolCall
from .tools.base import ToolResult

LABEL = "com.escanor.serve"


# ---------- launchd ----------

def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def service_plist(executable: str, logs: Path, command: str = "serve", label: str = LABEL) -> bytes:
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": [executable, command],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(logs / f"{command}.log"),
        "StandardErrorPath": str(logs / f"{command}.err.log"),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"},
    })


def service(action: str, run=subprocess.run, menubar: bool = False) -> int:
    """menubar=True installs the menu-bar app instead (it starts the server itself)."""
    label = "com.escanor.menubar" if menubar else LABEL
    command = "menubar" if menubar else "serve"
    path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    domain = f"gui/{os.getuid()}"
    if action == "status":
        result = run(["launchctl", "print", f"{domain}/{label}"], capture_output=True, text=True)
        print("running" if result.returncode == 0 else "not installed / not running", f"({path})")
        return 0
    if action == "uninstall":
        run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
        path.unlink(missing_ok=True)
        print("Removed. Escanor won't start at login.")
        return 0
    executable = shutil.which("escanor") or str(Path(sys.executable).with_name("escanor"))
    logs = config.data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(service_plist(executable, logs, command, label))
    run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
    result = run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Wrote {path}, but launchctl failed: {result.stderr.strip()}")
        return 1
    print(f"Installed: `escanor {command}` now starts at login. Logs in {logs}.")
    return 0


# ---------- brain backup ----------

def backup_brain(brain: Path, run=subprocess.run, now=datetime.now) -> str:
    if shutil.which("git") is None:
        return "git isn't installed."
    if not (brain / ".git").exists():
        run(["git", "init", "-q"], cwd=brain, check=True)
    run(["git", "add", "-A"], cwd=brain, check=True)
    status = run(["git", "status", "--porcelain"], cwd=brain, capture_output=True, text=True, check=True)
    if not status.stdout.strip():
        return "Nothing changed since the last backup."
    run(["git", "-c", "user.name=Escanor", "-c", "user.email=escanor@localhost", "commit", "-q",
         "-m", f"Brain backup {now():%Y-%m-%d %H:%M}"], cwd=brain, check=True)
    changed = len(status.stdout.strip().splitlines())
    return f"Backed up {changed} changed file(s) to {brain / '.git'}."


# ---------- eval ----------

@dataclass
class EvalCase:
    prompt: str
    expect: tuple[str, ...]      # any of these tool names counts; () = no tool expected


CASES = [
    EvalCase("what time is it?", ("get_time",)),
    EvalCase("search the web for the latest Python release", ("web_search",)),
    EvalCase("remember that I prefer window seats on flights", ("remember_fact",)),
    EvalCase("what do my notes say about the Escanor project?", ("brain_search",)),
    EvalCase("set a timer for 10 minutes for the pasta", ("set_timer",)),
    EvalCase("good morning!", ("morning_briefing",)),
    EvalCase("play some lofi beats", ("enable_tools", "music_play", "dj_start")),
    EvalCase("teach me how attention works in transformers", ("use_skill",)),
    EvalCase("explain this paper: https://arxiv.org/abs/1706.03762", ("enable_tools", "research_ingest")),
    EvalCase("hi there, how are you?", ()),
]


class StubGate:
    """Records calls and answers with a stub; never runs a real tool."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def dispatch(self, call: ToolCall, tools, confirmer, session) -> ToolResult:
        self.calls.append(call.name)
        if call.name == "enable_tools":
            return await tools.dispatch_async(call)  # harmless, and lets the next step see the group
        return ToolResult(ok=True, output=f"(eval stub) {call.name} done.")


async def run_eval(agent: Agent, store: SessionStore, cases: list[EvalCase] = CASES,
                   delay: float = 0.0) -> list[tuple[EvalCase, list[str], bool | None]]:
    """Grade each case: True/False, or None when the turn errored (rate limit,
    network) -- an error says nothing about tool choice, so it isn't a FAIL (B16).
    `delay` seconds between cases keeps free tiers under their per-minute quota."""
    results = []
    for i, case in enumerate(cases):
        if i and delay:
            await asyncio.sleep(delay)
        gate = StubGate()
        agent.gate = gate
        agent.tools.reset()
        called: list[str] = []
        errored = False
        async for event in agent.run_turn(store.create(), case.prompt):
            if isinstance(event, ToolStarted):
                called.append(event.call.name)
            elif isinstance(event, Error) and not called:
                errored = True
                called.append(f"error: {event.message[:80]}")
        if errored:
            results.append((case, called, None))
            continue
        passed = (not called) if not case.expect else any(name in called for name in case.expect)
        results.append((case, called, passed))
    return results
