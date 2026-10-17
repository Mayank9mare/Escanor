"""
voice/tts/sarvam_engine.py

Talks to Sarvam AI's text-to-speech API (Bulbul) -- like
elevenlabs_engine.py, a cloud API, not a local model, using only the
standard library. Unlike ElevenLabs, Sarvam's response already is a
complete WAV file (base64-encoded in JSON) when output_audio_codec is
left at its "wav" default -- confirmed live -- so there's no need for
elevenlabs_engine.py's manual raw-PCM-to-WAV wrapping here.
"""
from __future__ import annotations
import base64
import json
import urllib.error
import urllib.request

from ... import config
from .base import TTSEngine

DEFAULT_MODEL = "bulbul:v3"
# One of bulbul:v3's documented voices -- see docs.sarvam.ai for the
# full list (30+ voices) if you want a different one.
DEFAULT_SPEAKER = "shubh"
DEFAULT_LANGUAGE_CODE = "en-IN"


class SarvamTTSEngine(TTSEngine):
    name = "sarvam"

    def __init__(
        self, speaker: str = DEFAULT_SPEAKER, model: str = DEFAULT_MODEL, language_code: str = DEFAULT_LANGUAGE_CODE,
    ):
        api_key = config.get_api_key("sarvam")
        if not api_key:
            raise RuntimeError("Set SARVAM_API_KEY to use the sarvam TTS engine.")
        self._api_key = api_key
        self._speaker = speaker
        self._model = model
        self._language_code = language_code

    def synthesize(self, text: str) -> bytes:
        body = json.dumps({
            "text": text,
            "language_code": self._language_code,
            "model": self._model,
            "speaker": self._speaker,
            "output_audio_codec": "wav",
        }).encode()
        request = urllib.request.Request(
            "https://api.sarvam.ai/text-to-speech", data=body,
            headers={"api-subscription-key": self._api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Sarvam TTS request failed ({e.code}): {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Could not reach Sarvam: {e}") from e

        audios = data.get("audios") or []
        if not audios:
            raise RuntimeError(f"Sarvam TTS returned no audio: {data}")
        return base64.b64decode(audios[0])
