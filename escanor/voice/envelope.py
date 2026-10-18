"""
voice/envelope.py

The loudness curve of a WAV, for lip-syncing the HUD's sun to speech
that plays on the Mac's speakers (the browser never gets that audio).
"""
from __future__ import annotations
import io
import wave
import numpy as np

STEP = 0.04  # seconds per value: 25 per second, smooth enough for animation


def wav_envelope(wav: bytes, step: float = STEP) -> list[float]:
    """RMS loudness per `step` seconds, scaled to 0..1 against the loudest window."""
    try:
        with wave.open(io.BytesIO(wav), "rb") as wf:
            if wf.getsampwidth() != 2:
                return []
            rate, channels = wf.getframerate(), wf.getnchannels()
            samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32)
    except (wave.Error, EOFError, ValueError):
        return []
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    window = max(1, int(rate * step))
    count = len(samples) // window
    if count == 0:
        return []
    rms = np.sqrt((samples[: count * window].reshape(count, window) ** 2).mean(axis=1))
    peak = float(rms.max()) or 1.0
    return [round(float(v) / peak, 3) for v in rms]
