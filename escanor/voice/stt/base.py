"""
voice/stt/base.py

The shape every speech-to-text engine must follow -- the same idea as
models/base.py's Model interface for LLM providers. Nothing else in
Escanor voice needs to know which engine is transcribing; it just calls
`.transcribe(wav_bytes)` and gets text back, no matter which engine is
plugged in underneath (see registry.py).
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class STTEngine(ABC):
    name: str = "base-stt"

    @abstractmethod
    def transcribe(self, wav_bytes: bytes) -> str:
        """Turn recorded audio (a WAV file's raw bytes) into text."""
        raise NotImplementedError
