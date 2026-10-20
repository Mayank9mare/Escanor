"""
doctor.py

`escanor doctor`: is everything working? One line per check:

    ✓ ok    ! works, but something's missing    ✗ broken

Models get the same four live checks each (an idea from JBIQ's port
conformance suites: one test every provider must pass), through the
exact message shapes a real turn sends:

  reply     a plain streamed answer
  tool      picks a tool when asked
  replay    reads a tool result back and answers (Gemini's thought
            signatures, ids and formats round-trip)
  switch    continues a turn another provider started (B27)

Only aliases you have a key for are tried, each check is one small
request, and --no-models skips them. Everything else is local: keys
(names only), voices, microphone, server, YouTube Music, phone access,
Accessibility, brain.
"""
from __future__ import annotations
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from . import config

OK, WARN, FAIL = "✓", "!", "✗"


@dataclass
class Check:
    status: str
    name: str
    detail: str = ""


TIME_TOOL = {"name": "get_time", "description": "Get the current local time.",
             "input_schema": {"type": "object", "properties": {}}}


def _short(error: Exception | str, limit: int = 140) -> str:
    text = " ".join(str(error).split())
    return text if len(text) <= limit else text[:limit] + "…"


def _final(model, messages, tools=None):
    response = None
    for event in model.chat_stream_tools(messages, tools):
        if event.kind == "done":
            response = event.response
    if response is None:
        raise RuntimeError("the stream ended without a final response")
    return response


def model_checks(name: str, model, spec: str) -> list[Check]:
    """The conformance suite for one model."""
    from .core.prompts import PromptBuilder
    from .core.session import SessionStore
    from .models.base import ModelResponse, ToolCall
    from .tools.base import ToolResult
    import tempfile

    out: list[Check] = []
    system = {"role": "system", "content": "You are a terse test assistant."}
    label = f"{name} ({spec})"

    started = time.monotonic()
    try:
        reply = _final(model, [system, {"role": "user", "content": "Reply with just the word: ready"}])
        ok = bool(reply.text.strip())
        out.append(Check(OK if ok else FAIL, f"{label}: reply",
                         f"{time.monotonic() - started:.1f}s" if ok else "empty reply"))
    except RuntimeError as e:
        return [Check(FAIL, f"{label}: reply", _short(e))]   # nothing else will work either

    with tempfile.TemporaryDirectory() as scratch:
        session = SessionStore(Path(scratch) / "doctor.db").create()
        session.append_user("What time is it? Use the get_time tool.")
        try:
            asked = _final(model, PromptBuilder().build(session), [TIME_TOOL])
        except RuntimeError as e:
            out.append(Check(FAIL, f"{label}: tool", _short(e)))
            return out
        calls = [c for c in asked.tool_calls if c.name == "get_time"]
        out.append(Check(OK if calls else FAIL, f"{label}: tool",
                         "" if calls else f"no tool call (said: {_short(asked.text, 60)!r})"))
        if not calls:
            return out

        session.append_assistant(asked, spec)
        session.append_tool_result(calls[0], ToolResult(ok=True, output="09:30"))
        try:
            answer = _final(model, PromptBuilder().build(session), [TIME_TOOL])
            ok = "9" in answer.text or "nine" in answer.text.lower()
            out.append(Check(OK if ok else WARN, f"{label}: replay",
                             "" if ok else f"answered without the result: {_short(answer.text, 60)!r}"))
        except RuntimeError as e:
            out.append(Check(FAIL, f"{label}: replay", _short(e)))

        other = SessionStore(Path(scratch) / "switch.db").create()
        other.append_user("What time is it?")
        other.append_assistant(ModelResponse(tool_calls=[ToolCall("x1", "get_time", {})]), "other:model")
        other.append_tool_result(ToolCall("x1", "get_time", {}), ToolResult(ok=True, output="09:30"))
        other.collapse_tool_history(spec)    # what failover does mid-turn
        try:
            switched = _final(model, PromptBuilder().build(other), [TIME_TOOL])
            ok = bool(switched.text.strip() or switched.tool_calls)
            out.append(Check(OK if ok else FAIL, f"{label}: switch", "" if ok else "empty reply"))
        except RuntimeError as e:
            out.append(Check(FAIL, f"{label}: switch", _short(e)))
    return out


def _models(settings: dict, build: Callable) -> list[Check]:
    out: list[Check] = []
    seen: set[str] = set()
    for alias, spec in settings["models"]["aliases"].items():
        if alias == "mock" or spec in seen:
            continue
        seen.add(spec)
        provider = spec.split(":", 1)[0]
        if provider == "ollama" and not _reachable(config.get_ollama_base_url() + "/api/tags"):
            out.append(Check(WARN, f"{alias} ({spec})", "Ollama isn't running: skipped"))
            continue
        try:
            model, built = build(alias)
        except (ValueError, RuntimeError) as e:
            out.append(Check(WARN, f"{alias} ({spec})", f"skipped: {_short(e, 90)}"))
            continue
        out += model_checks(alias, model, built)
    return out


def _reachable(url: str, timeout: float = 1.5, headers: dict | None = None) -> bool:
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True          # something answered
    except (OSError, ValueError):
        return False


def _keys() -> list[Check]:
    present = [p for p in config._ENV_VAR_BY_PROVIDER if config.get_api_key(p)]
    missing = [p for p in ("anthropic", "groq", "gemini", "openrouter") if p not in present]
    return [Check(OK if present else FAIL, "API keys", f"set: {', '.join(present) or 'none'}"
                  + (f" · not set: {', '.join(missing)}" if missing else ""))]


def _voices(settings: dict) -> list[Check]:
    voice = settings["voice"]
    out = []
    try:
        import faster_whisper  # noqa: F401
        out.append(Check(OK, "speech-to-text", f"faster-whisper ({voice.get('whisper_model', 'base')})"))
    except ImportError:
        out.append(Check(WARN, "speech-to-text", "not installed: uv pip install -e '.[voice]'"))
    if voice.get("tts") != "piper":
        return out + [Check(OK, "text-to-speech", voice.get("tts", ""))]
    wanted = {"main": voice.get("tts_voice"), "alt": voice.get("alt_voice")}
    wanted.update({name: spec.get("voice") for name, spec in (voice.get("more") or {}).items()})
    dirs = [config.data_dir() / "voices", Path.home() / ".kmare" / "voices"]
    for name, model in wanted.items():
        if not model:
            continue
        found = any((d / f"{model}.onnx").exists() for d in dirs)
        out.append(Check(OK if found else WARN, f"voice {name}", model + ("" if found else ": downloads on first use")))
    return out


def _mic() -> list[Check]:
    try:
        import sounddevice
        device = sounddevice.query_devices(kind="input")
        return [Check(OK, "microphone", device["name"])]
    except ImportError:
        return [Check(WARN, "microphone", "not installed: uv pip install -e '.[listen]'")]
    except Exception as e:  # noqa: BLE001 -- PortAudio errors vary
        return [Check(FAIL, "microphone", _short(e))]


def _server(settings: dict) -> list[Check]:
    url = f"http://{settings['server']['host']}:{settings['server']['port']}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            status = json.loads(response.read())
        return [Check(OK, "server", f"running on {status.get('model')}")]
    except (OSError, ValueError):
        return [Check(WARN, "server", "not running (escanor serve, or escanor menubar)")]


def _music(settings: dict) -> list[Check]:
    if not settings["music"].get("enabled"):
        return [Check(OK, "YouTube Music", "off")]
    reachable = _reachable(settings["music"]["api_url"].rstrip("/") + "/api/v1/song")
    return [Check(OK if reachable else WARN, "YouTube Music",
                  "app reachable" if reachable else "app not running (needed for music and DJ)")]


def _phone() -> list[Check]:
    from .server import remote
    if remote.load_token() is None:
        return [Check(OK, "phone access", "off (escanor pair to turn on)")]
    import shutil
    app = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    has = shutil.which("tailscale") or Path(app).exists()
    return [Check(OK if has else WARN, "phone access", "paired" + ("" if has else ", but Tailscale isn't installed"))]


def _accessibility() -> list[Check]:
    try:
        from .selection import accessibility_trusted
        trusted = accessibility_trusted()
    except Exception:  # noqa: BLE001 -- not a Mac, or no AppKit
        return []
    return [Check(OK if trusted else WARN, "Accessibility",
                  "granted (one-key explain)" if trusted else "not granted: ⌃⌥E explains the clipboard instead")]


def _brain(settings: dict) -> list[Check]:
    root = config.data_dir() / "brain"
    notes = len(list(root.rglob("*.md"))) if root.exists() else 0
    out = [Check(OK, "brain", f"{notes} notes in {root}")]
    if settings["brain"].get("embedder") == "ollama" and not _reachable(config.get_ollama_base_url() + "/api/tags"):
        out.append(Check(WARN, "brain search", "keyword only: Ollama isn't running (meaning-based search off)"))
    return out


def run(settings: dict, build: Callable, models: bool = True, echo: Callable[[str], None] = print) -> int:
    sections = [
        ("Setup", lambda: _keys() + _voices(settings) + _mic() + _brain(settings)),
        ("Running", lambda: _server(settings) + _music(settings) + _phone() + _accessibility()),
    ]
    if models:
        sections.append(("Models (live: one small request per check)", lambda: _models(settings, build)))
    failed = 0
    for title, collect in sections:
        echo(title)
        for check in collect():
            echo(f"  {check.status} {check.name}" + (f" — {check.detail}" if check.detail else ""))
            failed += check.status == FAIL
    echo(f"\n{failed} problem{'s' if failed != 1 else ''} found." if failed else "\nAll good.")
    return 1 if failed else 0
