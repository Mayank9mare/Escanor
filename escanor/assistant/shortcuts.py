"""
assistant/shortcuts.py

Apple Shortcuts: "turn on focus mode", "lights off", "start my work
setup" -- anything you've built in the Shortcuts app, run by name via the
built-in `shortcuts` command.

Running one is "external" (it can do anything: send messages, change
settings, control your home), so Escanor asks first -- except for the
names you list in [assistant] shortcuts_allowed, which run straight away.
Names are matched case-insensitively against your actual shortcuts, so
the model can't run something that doesn't exist.
"""
from __future__ import annotations
import subprocess
from typing import Callable
from ..tools.base import EXTERNAL, READ, Tool, ToolResult

Runner = Callable[[list[str], str | None], subprocess.CompletedProcess]


def _run(command: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=120)


class Shortcuts:
    def __init__(self, run: Runner = _run):
        self._run = run

    def names(self) -> list[str]:
        try:
            result = self._run(["shortcuts", "list"], None)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"Couldn't list Shortcuts: {e}") from e
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Couldn't list Shortcuts.")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def resolve(self, name: str) -> str | None:
        wanted = name.strip().lower()
        return next((n for n in self.names() if n.lower() == wanted), None)

    def run(self, name: str, text: str | None = None) -> str:
        command = ["shortcuts", "run", name]
        try:
            result = self._run(command, text)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"'{name}' was still running after 2 minutes.") from e
        except OSError as e:
            raise RuntimeError(str(e)) from e
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"'{name}' failed.")
        return result.stdout.strip()


class ShortcutsListTool(Tool):
    name = "shortcuts_list"
    description = "List the user's Apple Shortcuts by name."
    input_schema = {"type": "object", "properties": {}}
    side_effect = READ
    group = "assistant"

    def __init__(self, shortcuts: Shortcuts):
        self.shortcuts = shortcuts

    def run(self) -> ToolResult:
        try:
            names = self.shortcuts.names()
        except RuntimeError as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output="\n".join(names) or "No shortcuts yet (make some in the Shortcuts app).")


class ShortcutRunTool(Tool):
    name = "shortcut_run"
    description = (
        "Run one of the user's Apple Shortcuts by its exact name (see shortcuts_list), optionally passing text "
        "as input. Use it for things like focus modes, smart-home scenes, or personal automations."
    )
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "input": {"type": "string", "description": "optional text input"}},
        "required": ["name"],
    }
    group = "assistant"

    def __init__(self, shortcuts: Shortcuts, allowed: list[str] | None = None):
        self.shortcuts = shortcuts
        self.allowed = {a.strip().lower() for a in allowed or []}
        self.side_effect = EXTERNAL  # allowed names skip the question: see confirmation_needed

    def describe_call(self, arguments: dict) -> str:
        return f"run your shortcut '{arguments.get('name', '')}'"

    def confirmation_needed(self, arguments: dict) -> bool:
        return str(arguments.get("name", "")).strip().lower() not in self.allowed

    def run(self, name: str, input: str | None = None) -> ToolResult:  # noqa: A002 -- matches the schema
        try:
            actual = self.shortcuts.resolve(name)
            if actual is None:
                return ToolResult(ok=False, output=f"No shortcut named '{name}'. Use shortcuts_list to see them.")
            output = self.shortcuts.run(actual, input)
        except RuntimeError as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output=f"Ran '{actual}'." + (f"\n{output[:2000]}" if output else ""))

