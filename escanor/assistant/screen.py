"""
assistant/screen.py

"What's on my screen?" -- look_at_screen takes a screenshot of the main
display with macOS's screencapture, shrinks it (sips, ~1568 px JPEG: what
vision models read well, a fraction of the tokens) and attaches it to the
tool result so the model can see it (models/images.py).

It asks first ("external"): your screen may show private things, and the
image goes to the model provider. Text on screen can contain anything,
so the result is untrusted too. The first capture needs Screen Recording
permission for your terminal (System Settings -> Privacy & Security).
Screenshots are kept in ~/.escanor/screens (newest `keep`).
"""
from __future__ import annotations
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable
from ..tools.base import EXTERNAL, Tool, ToolResult

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=20)


class Screen:
    def __init__(self, folder: Path, keep: int = 30, run: Runner = _run, now: Callable[[], datetime] = datetime.now):
        self.folder = folder
        self.keep = keep
        self._run = run
        self._now = now

    def capture(self, max_side: int = 1568) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"screen-{self._now():%Y%m%d-%H%M%S}.jpg"
        result = self._run(["screencapture", "-x", "-m", "-t", "jpg", str(path)])
        if result.returncode != 0 or not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(
                "Couldn't capture the screen. Allow Screen Recording for your terminal in System Settings → "
                "Privacy & Security, then try again."
                + (f" ({result.stderr.strip()})" if result.stderr.strip() else "")
            )
        self._run(["sips", "-Z", str(max_side), str(path)])  # best effort: full size still works
        self._prune()
        return path

    def _prune(self) -> None:
        shots = sorted(self.folder.glob("screen-*.jpg"))
        for old in shots[:-self.keep] if self.keep > 0 else []:
            old.unlink(missing_ok=True)


class LookAtScreenTool(Tool):
    name = "look_at_screen"
    description = (
        "Take a screenshot of the user's main display and look at it. Use when they ask about what's on "
        "their screen ('what's this error', 'summarise this page', 'what does this chart say')."
    )
    input_schema = {"type": "object", "properties": {}}
    side_effect = EXTERNAL
    untrusted_output = True

    def __init__(self, screen: Screen, model_name: Callable[[], str] = lambda: "the model"):
        self.screen = screen
        self._model_name = model_name

    def describe_call(self, arguments: dict) -> str:
        return f"capture your screen and send it to {self._model_name()}"

    def run(self) -> ToolResult:
        try:
            path = self.screen.capture()
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output=f"Screenshot of the main display taken at {self.screen._now():%H:%M}, attached.",
                          data={"images": [str(path)]})
