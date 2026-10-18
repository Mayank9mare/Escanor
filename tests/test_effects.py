from datetime import datetime
import numpy as np
from escanor.voice import effects
from escanor.voice.listen import to_wav
from escanor.voice.speech import SpeechEngine


def tone(freq=200.0, seconds=0.5, rate=16_000):
    t = np.arange(int(rate * seconds)) / rate
    return to_wav([(np.sin(2 * np.pi * freq * t) * 8000).astype(np.int16)])


def dominant(wav):
    x, rate = effects._read(wav)
    spectrum = np.abs(np.fft.rfft(x[: rate // 2]))
    return np.fft.rfftfreq(len(x[: rate // 2]), 1 / rate)[spectrum.argmax()]


def test_heavy_lowers_pitch_without_clipping():
    out = effects.apply(tone(200), effects.Voice(pitch=0.8))
    assert abs(dominant(out) - 160) < 6
    x, _ = effects._read(effects.apply(tone(200), effects.SUN["noon"]))
    assert np.max(np.abs(x)) < 0.9


def test_none_is_untouched_and_empty_audio_survives():
    wav = tone()
    assert effects.apply(wav, effects.NONE) is wav
    assert effects.apply(to_wav([]), effects.HEAVY) == to_wav([])


def test_sun_is_heaviest_at_noon_lightest_at_night():
    noon = effects.voice_for("sun", datetime(2026, 10, 19, 12, 0))
    night = effects.voice_for("sun", datetime(2026, 10, 19, 22, 0))
    assert noon.pitch < effects.HEAVY.pitch < night.pitch
    assert effects.voice_for("none") == effects.NONE


def test_speech_engine_speeds_tts_up_to_keep_the_pace():
    engine = SpeechEngine(stt_engine="mock", tts_engine="mock", effect="heavy")
    seen = []
    engine._tts.set_length_scale = seen.append
    engine.synthesize("hello")
    assert seen == [1.0 * effects.HEAVY.pitch]
