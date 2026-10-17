"""
voice/tts/base.py

The shape every text-to-speech engine must follow -- the same idea as
models/base.py's Model interface for LLM providers, and stt/base.py's
STTEngine for the speech-to-text side. Nothing else in Escanor voice
needs to know which engine is synthesizing; it just calls
`.synthesize(text)` and gets WAV bytes back (see registry.py).
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class TTSEngine(ABC):
    name: str = "base-tts"

    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Turn text into a playable WAV file's raw bytes."""
        raise NotImplementedError
