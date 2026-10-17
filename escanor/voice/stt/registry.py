"""
voice/stt/registry.py

The factory that turns an engine name into an actual STTEngine object,
the same shape as models/registry.py's get_model() for LLM providers.
Imports are lazy (inside the if-branches) so you don't need
faster-whisper installed unless you actually use it.
"""
from __future__ import annotations
from .base import STTEngine

_ENGINES = ("mock", "faster-whisper", "sarvam")


def get_stt_engine(name: str, model_name: str | None = None) -> STTEngine:
    if name == "mock":
        from .mock_engine import MockSTTEngine
        return MockSTTEngine()

    if name == "faster-whisper":
        from .faster_whisper_engine import DEFAULT_WHISPER_MODEL, FasterWhisperEngine
        return FasterWhisperEngine(model_name or DEFAULT_WHISPER_MODEL)

    if name == "sarvam":
        # A cloud API (needs SARVAM_API_KEY), not a local model like
        # the two above -- tuned for 22 Indian languages + English.
        from .sarvam_engine import DEFAULT_MODEL, SarvamSTTEngine
        return SarvamSTTEngine(model=model_name or DEFAULT_MODEL)

    raise ValueError(f"Unknown STT engine: {name!r}. Choices: {', '.join(_ENGINES)}")
