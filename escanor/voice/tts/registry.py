"""
voice/tts/registry.py

The factory that turns an engine name into an actual TTSEngine object,
the same shape as models/registry.py's get_model() for LLM providers
and stt/registry.py's get_stt_engine(). Imports are lazy (inside the
if-branches) so you don't need piper-tts installed unless you actually
use it.
"""
from __future__ import annotations
from .base import TTSEngine

_ENGINES = ("mock", "piper", "elevenlabs", "sarvam")


def get_tts_engine(name: str, voice: str | None = None, speed: float = 1.0) -> TTSEngine:
    if name == "mock":
        from .mock_engine import MockTTSEngine
        return MockTTSEngine()

    if name == "piper":
        from .piper_engine import DEFAULT_PIPER_VOICE, PiperEngine
        return PiperEngine(voice or DEFAULT_PIPER_VOICE, length_scale=speed)

    if name == "elevenlabs":
        # UNVERIFIED -- built without ElevenLabs credentials to test
        # against. See elevenlabs_engine.py's module docstring.
        from .elevenlabs_engine import DEFAULT_VOICE_ID, ElevenLabsEngine
        return ElevenLabsEngine(voice or DEFAULT_VOICE_ID)

    if name == "sarvam":
        # A cloud API (needs SARVAM_API_KEY), not a local model like
        # piper -- natural voices tuned for Indian languages. Verified
        # live: response is already a complete WAV, unlike ElevenLabs.
        from .sarvam_engine import DEFAULT_SPEAKER, SarvamTTSEngine
        return SarvamTTSEngine(speaker=voice or DEFAULT_SPEAKER)

    raise ValueError(f"Unknown TTS engine: {name!r}. Choices: {', '.join(_ENGINES)}")
