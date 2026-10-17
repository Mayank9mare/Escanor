"""
voice/tts/elevenlabs_engine.py

Talks to ElevenLabs' text-to-speech API.

*** UNVERIFIED ***
Built without ElevenLabs credentials to actually test against (see
VOICE.md's "elevenlabs" section) -- every other engine in this project
was verified with a real round-trip before being committed; this one
wasn't and couldn't be. Implemented carefully against ElevenLabs'
documented REST API shape (endpoint, headers, the pcm_* output-format
option), but treat the first real run -- once ELEVENLABS_API_KEY is
set -- as the actual test, not this code review. If it doesn't work,
the most likely culprits are DEFAULT_VOICE_ID (a specific voice ID may
have changed or been retired) or the exact set of accepted
`output_format` values, since those are the two most detail-specific,
least stable parts of the request.

Uses only the standard library (no `elevenlabs` SDK dependency) --
matches how models/ollama_provider.py already talks to Ollama's HTTP
API directly, and this is a plain JSON POST, not something that
benefits much from an SDK.
"""
from __future__ import annotations
import io
import json
import urllib.error
import urllib.request
import wave

from ... import config
from .base import TTSEngine

# UNVERIFIED: "Rachel", one of ElevenLabs' long-standing default
# premade voices -- used here only as a fallback if the caller doesn't
# pick a voice from their own account. This specific ID was not
# confirmed live (no credentials to test against, see the module
# docstring above) and voice IDs can be retired. Override with
# --elevenlabs-voice-id if this one doesn't work.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_MODEL_ID = "eleven_multilingual_v2"

_SAMPLE_RATE = 16000


class ElevenLabsEngine(TTSEngine):
    name = "elevenlabs"

    def __init__(self, voice_id: str = DEFAULT_VOICE_ID, model_id: str = DEFAULT_MODEL_ID):
        api_key = config.get_api_key("elevenlabs")
        if not api_key:
            raise RuntimeError("Set ELEVENLABS_API_KEY to use the elevenlabs TTS engine.")
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id

    def synthesize(self, text: str) -> bytes:
        # UNVERIFIED: pcm_16000 is documented as raw 16-bit mono PCM at
        # 16kHz, no container -- not confirmed against a live response
        # (no credentials to test against). We wrap it in a real WAV
        # header ourselves (see _pcm_to_wav below) so this engine's
        # output matches every other TTSEngine's contract (playable
        # WAV bytes) regardless of what format the provider returns --
        # but if `output_format` itself is rejected or has changed,
        # this is the line that would need to change.
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}?output_format=pcm_{_SAMPLE_RATE}"
        body = json.dumps({"text": text, "model_id": self._model_id}).encode()
        request = urllib.request.Request(
            url, data=body,
            headers={"xi-api-key": self._api_key, "Content-Type": "application/json", "Accept": "audio/pcm"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                pcm_bytes = response.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ElevenLabs TTS request failed ({e.code}): {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Could not reach ElevenLabs: {e}") from e

        return _pcm_to_wav(pcm_bytes, _SAMPLE_RATE)


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()
