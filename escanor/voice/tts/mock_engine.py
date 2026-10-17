"""
voice/tts/mock_engine.py

A fake TTS engine that needs no model download and costs nothing --
the same idea as models/mock_provider.py's MockModel for the LLM side.
Doesn't actually synthesize speech; returns a valid silent WAV file,
roughly as long as the text would take to say, so the browser's
<audio> element has something real to play without erroring, and the
rest of the pipeline can be exercised without Piper installed.
"""
from __future__ import annotations
import io
import wave

from .base import TTSEngine

_SAMPLE_RATE = 16000


class MockTTSEngine(TTSEngine):
    name = "mock-tts"

    def synthesize(self, text: str) -> bytes:
        # Rough, not a real speech-rate estimate -- just enough that
        # a longer reply "sounds" longer when testing the UI.
        duration_seconds = max(0.3, len(text) / 15)
        n_samples = int(duration_seconds * _SAMPLE_RATE)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(_SAMPLE_RATE)
            wav_file.writeframes(b"\x00\x00" * n_samples)
        return buffer.getvalue()
