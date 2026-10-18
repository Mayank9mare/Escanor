"""
voice/speech.py

SpeechEngine composes an STT engine and a TTS engine into the two
operations Escanor voice actually needs: transcribe() and synthesize().
Which concrete engine each one is comes from stt/registry.py and
tts/registry.py -- the Strategy pattern, the same "small registry
picks a concrete implementation of a shared interface" shape
models/registry.py already uses for LLM providers (see stt/base.py
and tts/base.py for the interfaces themselves).

Both engines load once, when SpeechEngine is constructed (at server
startup -- the first run of a real engine also downloads its model
weights, which takes real time), and are reused for every request
after that -- loading either per-request would make every turn far
slower than it needs to be.
"""
from __future__ import annotations
from .stt.registry import get_stt_engine
from .tts.registry import get_tts_engine

DEFAULT_STT_ENGINE = "faster-whisper"
DEFAULT_TTS_ENGINE = "piper"


class SpeechEngine:
    def __init__(
        self,
        stt_engine: str = DEFAULT_STT_ENGINE,
        tts_engine: str = DEFAULT_TTS_ENGINE,
        whisper_model: str | None = None,
        tts_voice: str | None = None,
        tts_speed: float = 1.0,
    ):
        self._stt = get_stt_engine(stt_engine, model_name=whisper_model)
        self._tts = get_tts_engine(tts_engine, voice=tts_voice, speed=tts_speed)

    def transcribe(self, wav_bytes: bytes) -> str:
        """Turn recorded audio (a WAV file's raw bytes) into text."""
        return self._stt.transcribe(wav_bytes)

    def synthesize(self, text: str) -> bytes:
        """Turn text into a playable WAV file's raw bytes."""
        return self._tts.synthesize(text)
