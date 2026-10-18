"""
voice/tts/piper_engine.py

Talks to piper-tts. This file is the only place in Escanor voice that
knows Piper's specific API (voice models are .onnx files that have to
be downloaded once, then loaded) -- everywhere else uses the generic
TTSEngine shape from base.py.
"""
from __future__ import annotations
import io
import wave
from pathlib import Path

from .base import TTSEngine

# A deep male voice, for Escanor. Others: en_GB-alan-medium (British male),
# en_US-lessac-medium (female). Any voice from huggingface.co/rhasspy/piper-voices.
DEFAULT_PIPER_VOICE = "en_US-ryan-high"

_VOICES_DIR = Path.home() / ".escanor" / "voices"
# kmare-code downloads the same voices here; reuse them instead of
# downloading ~60 MB again.
_KMARE_VOICES_DIR = Path.home() / ".kmare" / "voices"


class PiperEngine(TTSEngine):
    name = "piper"

    def __init__(self, voice: str = DEFAULT_PIPER_VOICE, length_scale: float = 1.0):
        try:
            from piper.voice import PiperVoice
            from piper.download_voices import download_voice
        except ImportError as e:
            raise RuntimeError("Run `pip install piper-tts` to use the Piper TTS engine.") from e

        onnx_path = _KMARE_VOICES_DIR / f"{voice}.onnx"
        if not onnx_path.exists():
            _VOICES_DIR.mkdir(parents=True, exist_ok=True)
            onnx_path = _VOICES_DIR / f"{voice}.onnx"
            if not onnx_path.exists():
                download_voice(voice, _VOICES_DIR)
        self._voice = PiperVoice.load(str(onnx_path))
        from piper.config import SynthesisConfig
        # length_scale > 1 speaks more slowly: 1.1 gives Escanor some gravitas.
        self._config = SynthesisConfig(length_scale=length_scale)

    def synthesize(self, text: str) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            self._voice.synthesize_wav(text, wav_file, syn_config=self._config)
        return buffer.getvalue()
