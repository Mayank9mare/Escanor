"""
models/images.py

Images in the conversation (today: screenshots from look_at_screen).

A tool result can carry image files (ToolResult.data["images"]); the
session stores their paths on the tool message, and each provider loads
and sends them in its own format when building a request:

- Claude: image blocks inside the tool_result
- OpenAI / Gemini / Ollama: their tool messages are text-only, so the
  images follow in a short user message right after the tool results

Files, not base64, live in the database, so conversations stay small.
"""
from __future__ import annotations
import base64
from pathlib import Path

_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
FOLLOW_UP_TEXT = "Image(s) returned by the tool call above:"
MISSING_TEXT = "(an image from this tool result is no longer available)"


def load_image(path: str) -> tuple[str, str] | None:
    """(media type, base64) or None if the file is gone or not an image."""
    file = Path(path)
    media = _MEDIA.get(file.suffix.lower())
    if media is None or not file.is_file():
        return None
    return media, base64.b64encode(file.read_bytes()).decode()


def tool_images(messages: list[dict]) -> list[tuple[str, str]]:
    """All loadable images attached to a run of tool messages."""
    images = []
    for message in messages:
        for path in message.get("images") or []:
            loaded = load_image(path)
            if loaded:
                images.append(loaded)
    return images
