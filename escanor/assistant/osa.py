"""
assistant/osa.py

Running AppleScript (osascript) safely from model-supplied values.

- quote() turns any text into an AppleScript string literal, so a title
  like `Buy milk" & do shell script "rm -rf ~` stays a title.
- date_expr() builds an AppleScript date from numbers rather than
  parsing a date string, which depends on the Mac's region settings.
  The day is set to 1 before the month changes, so 31 January -> February
  can't roll over into March.
- run() raises RuntimeError with a plain explanation when macOS blocks
  automation ("Not authorized to send Apple events"): the fix is
  System Settings → Privacy & Security → Automation.
"""
from __future__ import annotations
import subprocess
from datetime import datetime
from typing import Callable

FIELD = "\x1f"   # ASCII unit separator: between fields
RECORD = "\x1e"  # ASCII record separator: between records

Runner = Callable[[str], str]


def quote(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def date_expr(var: str, when: datetime) -> str:
    """AppleScript lines that set `var` to `when` (local time)."""
    seconds = when.hour * 3600 + when.minute * 60 + when.second
    return "\n".join([
        f"set {var} to current date",
        f"set day of {var} to 1",
        f"set year of {var} to {when.year}",
        f"set month of {var} to {when.month}",
        f"set day of {var} to {when.day}",
        f"set time of {var} to {seconds}",
    ])


def run(script: str, timeout: float = 20) -> str:
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise RuntimeError("AppleScript isn't available (this needs macOS).") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("The app didn't answer in time (it may be showing a permission prompt).") from e
    if result.returncode != 0:
        error = result.stderr.strip()
        if "-1743" in error or "Not authorized" in error:
            raise RuntimeError(
                "macOS blocked Escanor from controlling that app. Allow it in System Settings → "
                "Privacy & Security → Automation (for your terminal / Python)."
            )
        raise RuntimeError(f"AppleScript failed: {error or 'unknown error'}")
    return result.stdout.rstrip("\n")


def records(output: str) -> list[list[str]]:
    return [r.split(FIELD) for r in output.split(RECORD) if r.strip()]


def parse_when(text: str) -> datetime:
    """Model-supplied local date/time: '2026-10-17T19:00', '2026-10-17 19:00' or '2026-10-17'."""
    try:
        return datetime.fromisoformat(text.strip().replace("Z", ""))
    except ValueError as e:
        raise ValueError(f"Use a date like 2026-10-17T19:00 (got '{text}').") from e
