"""
voice/stt/faster_whisper_engine.py

Talks to faster-whisper. This file is the only place in Escanor voice
that knows faster-whisper's specific requirements (16kHz raw float32
arrays, no sample-rate parameter) -- everywhere else uses the generic
STTEngine shape from base.py.
"""
from __future__ import annotations
import io
import wave

import numpy as np

from .base import STTEngine

DEFAULT_WHISPER_MODEL = "base"


class FasterWhisperEngine(STTEngine):
    name = "faster-whisper"

    def __init__(self, model_name: str = DEFAULT_WHISPER_MODEL):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError("Run `pip install faster-whisper` to use the faster-whisper STT engine.") from e
        self._model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe(self, wav_bytes: bytes) -> str:
        audio = _wav_bytes_to_float32(wav_bytes)
        segments, _ = self._model.transcribe(audio, language="en")
        return " ".join(s.text.strip() for s in segments).strip()


def _wav_bytes_to_float32(wav_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(f"Expected 16-bit audio, got {wf.getsampwidth() * 8}-bit")
        raw = wf.readframes(wf.getnframes())
        channels = wf.getnchannels()
        rate = wf.getframerate()
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        # The browser always records mono (see static/app.js) -- this
        # is just a defensive fallback for a WAV from somewhere else.
        samples = samples.reshape(-1, channels).mean(axis=1)
    # faster-whisper's transcribe() has no sample-rate parameter for a
    # raw array -- it assumes 16kHz unconditionally. A browser's
    # microphone is normally 44.1kHz or 48kHz, not 16kHz, so this
    # resample is required, not optional: skipping it doesn't error,
    # it just silently feeds Whisper audio that's the wrong speed/pitch
    # for its feature extraction, degrading accuracy. See VOICE.md.
    return _resample_linear(samples, rate, 16000)


def _resample_linear(samples: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    """A plain linear-interpolation resample -- good enough for speech
    (no proper anti-aliasing filter, unlike a real polyphase resampler),
    using only numpy so this needs no extra audio-processing dependency."""
    if orig_rate == target_rate or len(samples) == 0:
        return samples
    duration = len(samples) / orig_rate
    target_length = max(1, int(round(duration * target_rate)))
    orig_indices = np.arange(len(samples))
    target_indices = np.linspace(0, len(samples) - 1, target_length)
    return np.interp(target_indices, orig_indices, samples).astype(np.float32)
