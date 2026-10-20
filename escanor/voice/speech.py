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
from . import effects
import threading
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
        effect: str = "none",
        alt_voice: str | None = None,
        alt_effect: str | None = None,
    ):
        self._stt = get_stt_engine(stt_engine, model_name=whisper_model)
        self._tts_engine = tts_engine
        self._speed = tts_speed
        # The voice switch (HUD / menubar): "main" and, if set, "alt". The alt
        # engine loads the first time it's chosen.
        self.voices = {"main": (tts_voice, effect)}
        if alt_voice:
            self.voices["alt"] = (alt_voice, alt_effect or effect)
        self._engines = {tts_voice: get_tts_engine(tts_engine, voice=tts_voice, speed=tts_speed)}
        self._lock = threading.Lock()
        self.current = "main"

    @property
    def effect(self) -> str:
        return self.voices[self.current][1]

    @effect.setter
    def effect(self, value: str) -> None:
        voice, _ = self.voices[self.current]
        self.voices[self.current] = (voice, value)

    @property
    def _tts(self):
        voice = self.voices[self.current][0]
        if voice not in self._engines:
            self._engines[voice] = get_tts_engine(self._tts_engine, voice=voice, speed=self._speed)
        return self._engines[voice]

    @_tts.setter
    def _tts(self, engine) -> None:   # tests swap in a fake engine
        self._engines[self.voices[self.current][0]] = engine

    def use(self, name: str) -> str:
        """Switch voice: 'main', 'alt' or 'toggle'. Returns the voice now in use."""
        if name == "toggle":
            name = "alt" if self.current == "main" else "main"
        if name not in self.voices:
            raise ValueError("No second voice set ([voice] alt_voice).")
        with self._lock:
            self.current = name
            self._tts  # load it now, not mid-sentence
        return name

    def label(self, name: str | None = None) -> str:
        """'en_GB-alan-medium' + 'heavy' -> 'Alan · heavy'."""
        voice, effect = self.voices[name or self.current]
        parts = (voice or "voice").split("-")
        who = parts[1] if len(parts) > 1 else parts[0]
        return who.replace("_", " ").title() + ("" if effect == "none" else f" · {effect}")

    def transcribe(self, wav_bytes: bytes) -> str:
        """Turn recorded audio (a WAV file's raw bytes) into text."""
        return self._stt.transcribe(wav_bytes)

    def synthesize(self, text: str) -> bytes:
        """Turn text into a playable WAV file's raw bytes, made heavier by
        the voice effect. A lowered pitch also slows speech, so the engine
        speaks faster by the same factor to keep the pace."""
        with self._lock:
            engine, effect = self._tts, self.effect
        voice = effects.voice_for(effect)
        if hasattr(engine, "set_length_scale"):
            engine.set_length_scale(self._speed * voice.pitch)
        return effects.apply(engine.synthesize(text), voice)
