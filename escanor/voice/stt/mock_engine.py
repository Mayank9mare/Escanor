"""
voice/stt/mock_engine.py

A fake STT engine that needs no model download and costs nothing --
the same idea as models/mock_provider.py's MockModel for the LLM side.
Doesn't actually recognize speech; reports the recording's real
duration (a free, deterministic read from the WAV header) so the rest
of the pipeline -- the LLM call, the TTS reply, the UI -- can be
exercised without faster-whisper installed or a real microphone.
"""
from __future__ import annotations
import io
import wave

from .base import STTEngine


class MockSTTEngine(STTEngine):
    name = "mock-stt"

    def transcribe(self, wav_bytes: bytes) -> str:
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                duration = wf.getnframes() / wf.getframerate()
        except (wave.Error, ZeroDivisionError):
            duration = 0.0
        return f"(mock transcript) You said something for about {duration:.1f} seconds."
