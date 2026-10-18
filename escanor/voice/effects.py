"""
voice/effects.py

Makes the synthesized voice heavier: deeper, chestier, a little larger
than the room -- Escanor's weight rather than a newsreader's. Pure numpy,
a few milliseconds per sentence.

  pitch   < 1 lowers the voice. It resamples, so the formants drop too:
          that's what makes it sound *big* (a larger chest), not just low.
          The TTS speaks faster by the same factor to keep the pace.
  bass    low-end boost (0 = none)
  drive   soft saturation: grit and density in the chest
  room    reverb amount (0 = dry); a short, dark hall

With [voice] effect = "sun" the weight follows the sun like the persona:
heaviest at high noon, lightest (the timid innkeeper) at night.
"""
from __future__ import annotations
import io
import wave
from dataclasses import dataclass
from datetime import datetime
import numpy as np


@dataclass(frozen=True)
class Voice:
    pitch: float = 1.0
    bass: float = 0.0
    drive: float = 0.0
    room: float = 0.0


NONE = Voice()
HEAVY = Voice(pitch=0.84, bass=0.8, drive=0.25, room=0.12)
SUN = {
    "dawn": Voice(pitch=0.88, bass=0.6, drive=0.15, room=0.08),
    "day": HEAVY,
    "noon": Voice(pitch=0.78, bass=1.1, drive=0.35, room=0.22),
    "afternoon": Voice(pitch=0.86, bass=0.7, drive=0.2, room=0.1),
    "night": Voice(pitch=0.95, bass=0.3, drive=0.0, room=0.05),
}


def voice_for(effect: str, now: datetime | None = None) -> Voice:
    if effect == "sun":
        from ..core.persona import phase
        return SUN[phase(now or datetime.now())]
    return HEAVY if effect == "heavy" else NONE


def _read(wav: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(wav)) as wf:
        rate, channels = wf.getframerate(), wf.getnchannels()
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def _write(samples: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return buffer.getvalue()


def _lowpass(x: np.ndarray, rate: int, cutoff: float) -> np.ndarray:
    """Zero-phase low-pass in the frequency domain (fine for a sentence)."""
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / rate)
    spectrum *= 1 / (1 + (freqs / cutoff) ** 4)
    return np.fft.irfft(spectrum, len(x))


def _room(x: np.ndarray, rate: int, amount: float, seconds: float = 0.5) -> np.ndarray:
    n = int(rate * seconds)
    rng = np.random.default_rng(7)  # fixed: the same room every time
    impulse = rng.standard_normal(n) * np.exp(-np.arange(n) / (rate * seconds / 5))
    impulse = _lowpass(impulse, rate, 2500)              # a dark hall, not a bathroom
    impulse /= np.sqrt(np.sum(impulse ** 2)) or 1
    size = len(x) + n
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(impulse, size), size)
    out = np.concatenate([x, np.zeros(n)]) + amount * wet
    return out[: len(x) + n // 2]


def apply(wav: bytes, voice: Voice) -> bytes:
    if voice == NONE:
        return wav
    x, rate = _read(wav)
    if len(x) == 0:
        return wav
    if voice.pitch != 1.0:   # resample: lower pitch *and* formants
        positions = np.arange(0, len(x) - 1, voice.pitch)
        x = np.interp(positions, np.arange(len(x)), x)
    if voice.bass:
        x = x + voice.bass * _lowpass(x, rate, 220)
    if voice.drive:
        x = np.tanh(x * (1 + 3 * voice.drive)) / np.tanh(1 + 3 * voice.drive)
    if voice.room:
        x = _room(x, rate, voice.room)
    peak = float(np.max(np.abs(x))) or 1.0
    return _write(x * (0.89 / peak), rate)
