"""
voice/stt/sarvam_engine.py

Talks to Sarvam AI's speech-to-text API (Saaras) -- unlike
faster-whisper, this is a cloud API tuned for Indian languages (22
Indian languages + English), not a local model. Uses only the standard
library (no `requests` dependency), matching how models/ollama_provider.py
and voice/tts/elevenlabs_engine.py already talk to their APIs directly.

The API takes multipart/form-data, not JSON -- Python's stdlib has no
multipart encoder, so _encode_multipart() below hand-rolls one. Small
and specific to this one request shape (one file field, a couple of
plain text fields) rather than a general-purpose library, on purpose.
"""
from __future__ import annotations
import json
import urllib.error
import urllib.request
import uuid

from ... import config
from .base import STTEngine

DEFAULT_MODEL = "saaras:v3"
# "unknown" tells Sarvam to auto-detect the spoken language instead of
# assuming one -- verified live: correctly detected en-IN with 0.994
# confidence on an English test clip. The real point of this engine
# over faster-whisper is exactly this: it also handles Hindi, Tamil,
# Bengali, etc. without a --language flag.
DEFAULT_LANGUAGE_CODE = "unknown"


class SarvamSTTEngine(STTEngine):
    name = "sarvam"

    def __init__(self, model: str = DEFAULT_MODEL, language_code: str = DEFAULT_LANGUAGE_CODE):
        api_key = config.get_api_key("sarvam")
        if not api_key:
            raise RuntimeError("Set SARVAM_API_KEY to use the sarvam STT engine.")
        self._api_key = api_key
        self._model = model
        self._language_code = language_code

    def transcribe(self, wav_bytes: bytes) -> str:
        body, content_type = _encode_multipart(
            fields={"model": self._model, "language_code": self._language_code},
            file_field="file", filename="audio.wav", file_bytes=wav_bytes, content_type="audio/wav",
        )
        request = urllib.request.Request(
            "https://api.sarvam.ai/speech-to-text", data=body,
            headers={"api-subscription-key": self._api_key, "Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Sarvam STT request failed ({e.code}): {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Could not reach Sarvam: {e}") from e

        return (data.get("transcript") or "").strip()


def _encode_multipart(
    fields: dict[str, str], file_field: str, filename: str, file_bytes: bytes, content_type: str,
) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(str(value).encode())
    parts.append(f"--{boundary}".encode())
    parts.append(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'.encode())
    parts.append(f"Content-Type: {content_type}".encode())
    parts.append(b"")
    parts.append(file_bytes)
    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    return b"\r\n".join(parts), f"multipart/form-data; boundary={boundary}"
