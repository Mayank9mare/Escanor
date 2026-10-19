import asyncio
import io
import wave
import numpy as np
import pytest
from escanor.core.agent import Agent
from escanor.models.base import ModelResponse, ToolCall
from escanor.models.mock_provider import MockModel
from escanor.voice.audio_out import CONFIRM, DJ, REPLY, AudioOut, NullPlayer
from escanor.voice.chunker import SentenceChunker, speakable
from escanor.voice.conversation import VoiceConfirmer, is_yes, speak_turn
from escanor.voice.listen import FRAME, EnergyVAD, ListenLoop, UtteranceSettings, record_utterance, to_wav
from escanor.voice.web_player import wav_duration


# ---------- chunker ----------

def _chunk_all(fragments):
    chunker = SentenceChunker()
    out = []
    for f in fragments:
        out += chunker.feed(f)
    return out + chunker.flush()


def test_should_emit_sentence_as_soon_as_complete():
    chunker = SentenceChunker()
    assert chunker.feed("The weather today is sunny") == []
    assert chunker.feed(" and warm. Tomorrow") == ["The weather today is sunny and warm."]
    assert chunker.flush() == ["Tomorrow"]


@pytest.mark.parametrize("text,expected", [
    ("Dr. Rao said it costs 3.50 dollars. Then he left.", ["Dr. Rao said it costs 3.50 dollars.", "Then he left."]),
    ("J. R. R. Tolkien wrote it, e.g. The Hobbit. Nice!", ["J. R. R. Tolkien wrote it, e.g. The Hobbit.", "Nice!"]),
    ("Is it ready? Yes it is, mostly.", ["Is it ready?", "Yes it is, mostly."]),
])
def test_should_not_split_on_abbreviations_numbers_or_initials(text, expected):
    assert _chunk_all([text]) == expected


def test_should_wait_for_code_block_to_close_and_not_read_code():
    sentences = _chunk_all(["Here is code: ```py\nx = 1. y = 2.\n", "``` That's all folks."])
    assert sentences == ["Here is code: (code shown on screen) That's all folks."]


def test_should_force_break_when_sentence_is_very_long():
    text = ("word, " * 80).strip()
    sentences = SentenceChunker().feed(text + " more")
    assert sentences and all(len(s) <= 300 for s in sentences)


def test_speakable_strips_markdown_links_and_urls():
    assert speakable("## Plan\n- **Buy** [milk](https://x.y) at https://shop.test now") == "Plan Buy milk at a link now"


# ---------- audio out ----------

def _tts(text: str) -> bytes:
    return text.encode()


async def test_should_play_in_order_and_duck_while_speaking():
    events = []

    class Ducker:
        def duck(self): events.append("duck")
        def restore(self): events.append("restore")

    player = NullPlayer()
    audio = AudioOut(_tts, player, Ducker())
    first, second = audio.say("one"), audio.say("two")
    assert await first is True and await second is True
    await audio.wait_idle(2)
    await asyncio.sleep(0.05)
    assert player.played == [b"one", b"two"]
    assert events == ["duck", "restore"]
    await audio.close()


async def test_should_cut_off_dj_patter_when_reply_arrives():
    player = NullPlayer(seconds_per_item=0.3)
    audio = AudioOut(_tts, player)
    patter = audio.say("And now, a classic", DJ)
    await asyncio.sleep(0.1)
    reply = audio.say("Your meeting is at 5", REPLY)
    assert await patter is False
    assert await reply is True
    assert player.played == [b"Your meeting is at 5"] and player.stopped == 1
    await audio.close()


async def test_should_drop_queue_and_stop_when_interrupted():
    player = NullPlayer(seconds_per_item=0.3)
    audio = AudioOut(_tts, player)
    a, b = audio.say("first"), audio.say("second")
    await asyncio.sleep(0.05)
    audio.interrupt()
    assert await a is False and await b is False
    await audio.wait_idle(2)
    assert player.played == []
    await audio.close()


async def test_should_restore_music_even_when_synthesis_fails():
    events = []

    class Ducker:
        def duck(self): events.append("duck")
        def restore(self): events.append("restore")

    def broken(text):
        raise RuntimeError("tts down")

    audio = AudioOut(broken, NullPlayer(), Ducker())
    assert await audio.say("hello") is False
    audio.hold()
    audio.release()
    await audio.close()
    assert events in ([], ["duck", "restore"]) and (not events or events[-1] == "restore")


async def test_should_keep_music_ducked_while_held():
    events = []

    class Ducker:
        def duck(self): events.append("duck")
        def restore(self): events.append("restore")

    audio = AudioOut(_tts, NullPlayer(), Ducker())
    audio.hold()
    await audio.say("hi")
    await asyncio.sleep(0.05)
    assert events == ["duck"]
    audio.release()
    assert events == ["duck", "restore"]
    await audio.close()


def test_wav_duration_reads_header():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(b"\0\0" * 8000)
    assert wav_duration(buffer.getvalue()) == 0.5
    assert wav_duration(b"junk") == 0.0


# ---------- speak_turn ----------

async def test_should_speak_reply_sentence_by_sentence_in_spoken_style(store):
    model = MockModel(script=[ModelResponse(text="It's sunny outside. Take sunglasses today.")])
    player = NullPlayer()
    audio = AudioOut(_tts, player)
    await speak_turn(Agent(model, "mock:m"), store.create(), "weather?", audio)
    await audio.wait_idle(2)
    assert player.played == [b"It's sunny outside.", b"Take sunglasses today."]
    assert "read aloud" in model.calls[0]["messages"][0]["content"]
    await audio.close()


async def test_should_not_use_spoken_style_when_audio_is_none(store):
    model = MockModel(script=[ModelResponse(text="ok")])
    await speak_turn(Agent(model, "mock:m"), store.create(), "hi", None)
    assert "read aloud" not in model.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("answer,expected", [
    ("yes please", True), ("yeah go ahead", True), ("okay", True),
    ("no", False), ("yes, no wait, don't", False), ("hmm", False), ("", False),
])
def test_is_yes(answer, expected):
    assert is_yes(answer) is expected


async def test_voice_confirmer_asks_aloud_then_listens():
    player = NullPlayer()
    audio = AudioOut(_tts, player)

    async def listen():
        return "yes do it"

    assert await VoiceConfirmer(audio, listen).confirm(ToolCall("c", "x", {}), "creating a reminder") is True
    assert b"creating a reminder" in player.played[0]
    await audio.close()


# ---------- listening ----------

def _frames(pattern: str):
    """'s' = speech frame, '.' = silence frame."""
    loud = (np.sin(np.linspace(0, 200, FRAME)) * 8000).astype(np.int16)
    quiet = np.zeros(FRAME, dtype=np.int16)
    return [loud if c == "s" else quiet for c in pattern]


async def _aiter(items):
    for item in items:
        yield item


async def test_should_record_from_first_speech_until_silence():
    frames = _frames("...." + "s" * 10 + "." * 12 + "ssss")
    got = await record_utterance(_aiter(frames), EnergyVAD(), UtteranceSettings(end_silence=0.8))
    assert got is not None and len(got) == 3 + 10 + 10  # pre-roll + speech + 0.8s silence


async def test_should_return_none_when_nobody_speaks():
    got = await record_utterance(_aiter(_frames("." * 80)), EnergyVAD(), UtteranceSettings(start_timeout=1.0))
    assert got is None


async def test_should_ignore_a_cough():
    got = await record_utterance(_aiter(_frames("..s" + "." * 20)), EnergyVAD())
    assert got is None


def test_to_wav_round_trips():
    with wave.open(io.BytesIO(to_wav(_frames("ss"))), "rb") as wf:
        assert (wf.getframerate(), wf.getnframes()) == (16000, 2 * FRAME)


class _Wake:
    def __init__(self):
        self.calls = 0

    def detected(self, frame):
        self.calls += 1
        return self.calls == 2


async def test_listen_loop_wakes_hears_and_responds():
    frames = _frames("..." + "s" * 8 + "." * 12)
    heard = []
    player = NullPlayer()
    audio = AudioOut(_tts, player)

    async def respond(text):
        heard.append(text)
        audio.say("Done.")

    loop = ListenLoop(_aiter(frames), EnergyVAD(), _Wake(), lambda wav: "turn on focus mode", respond, audio)
    loop.chime_seconds = 0
    assert await loop.once() == "turn on focus mode"
    assert heard == ["turn on focus mode"] and player.played == [b"Done."]
    await audio.close()


async def test_listen_loop_barge_in_cancels_turn_when_enabled():
    frames = _frames("..." + "s" * 8 + "." * 12 + "s" * 10)
    audio = AudioOut(_tts, NullPlayer(seconds_per_item=5))
    cancelled = asyncio.Event()

    async def respond(text):
        try:
            audio.say("a very long answer")
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def wait_enter():
        return None

    loop = ListenLoop(_aiter(frames), EnergyVAD(), None, lambda wav: "tell me a story", respond, audio,
                      barge_in=True, wait_enter=wait_enter)
    loop.chime_seconds = 0
    await asyncio.wait_for(loop.once(), 5)
    assert cancelled.is_set() and not audio.speaking
    await audio.close()


class _DrainableFrames:
    """Frames where drain() drops everything queued so far (like MicFrames)."""

    def __init__(self, queued, later):
        self.queued, self.later = list(queued), list(later)
        self.drained = 0

    def drain(self):
        self.drained += len(self.queued)
        self.queued = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.queued:
            return self.queued.pop(0)
        if self.later:
            return self.later.pop(0)
        raise StopAsyncIteration


async def test_listen_loop_drops_chime_audio_before_recording():
    """B15: the chime must not count as the start of an utterance."""
    chime_frames = _frames("ss")                       # the mic hearing the chime
    speech = _frames("...." + "s" * 8 + "." * 12)      # then you start talking
    frames = _DrainableFrames(chime_frames, speech)
    heard = []

    async def wait_enter():
        return None

    async def respond(text):
        heard.append(text)

    audio = AudioOut(_tts, NullPlayer())
    loop = ListenLoop(frames, EnergyVAD(), None, lambda wav: f"{len(wav)} bytes", respond, audio, wait_enter=wait_enter)
    loop.chime_seconds = 0
    assert await loop.once() != ""
    assert frames.drained >= 2 and heard
    await audio.close()


async def test_ducker_failure_warns_once_without_traceback(caplog):
    """B19: a failing ducker ran on every utterance and printed a traceback each time."""
    class Broken:
        def duck(self): raise RuntimeError("volume API down")
        def restore(self): raise RuntimeError("volume API down")

    player = NullPlayer()
    audio = AudioOut(_tts, player, Broken())
    with caplog.at_level("WARNING"):
        for word in ("one", "two", "three"):
            await audio.say(word)
        await audio.wait_idle(2)
    await audio.close()
    assert player.played == [b"one", b"two", b"three"]
    warnings = [r for r in caplog.records if "music volume" in r.getMessage()]
    assert len(warnings) == 1 and warnings[0].exc_info is None


# ---------- lip-sync envelope ----------

def test_envelope_follows_loudness():
    from escanor.voice.envelope import wav_envelope
    loud = (np.sin(np.linspace(0, 400, 1600)) * 12000).astype(np.int16)
    quiet = np.zeros(1600, dtype=np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
        wf.writeframes(np.concatenate([quiet, loud, quiet]).tobytes())
    values = wav_envelope(buffer.getvalue(), step=0.1)  # 1600 samples per value
    assert values == [0.0, 1.0, 0.0]
    assert wav_envelope(b"not a wav") == []


async def test_audio_out_reports_each_wav_before_playing():
    played = []
    audio = AudioOut(_tts, NullPlayer())
    audio.on_play = played.append
    await audio.say("hello there")
    await audio.close()
    assert played == [b"hello there"]



# ---------- "hey Escanor" ----------

from escanor.voice.listen import TranscriptWake, match_wake_phrase  # noqa: E402


@pytest.mark.parametrize("heard,rest", [
    ("Hey Escanor.", ""),
    ("Hey Escanor, what time is it?", "what time is it"),
    ("Escanor. Play some music.", "play some music"),
    ("Hey, I'm Skinner.", ""),                         # real Whisper output before the vocabulary hint
    ("Hey, Eskiner, what time is it?", "what time is it"),
    ("Eskender, play some music.", "play some music"),
    ("hey es canor good morning", "good morning"),       # split by the transcriber
    ("Hello there! How are you?", None),
    ("I scan the document.", None),
    ("Tell Skinner I set high.", None),                 # the name not at the start
    ("", None),
])
def test_wake_phrase_matching(heard, rest):
    assert match_wake_phrase(heard) == rest


async def test_transcript_wake_answers_question_said_in_the_same_breath():
    frames = _frames("..." + "s" * 8 + "." * 10 + "..." + "s" * 8 + "." * 10)
    transcripts = iter(["Hello there.", "Hey Escanor, what time is it?"])
    answered = []

    async def respond(text):
        answered.append(text)

    audio = AudioOut(_tts, NullPlayer())
    loop = ListenLoop(_aiter(frames), EnergyVAD(), TranscriptWake("hey escanor"),
                      lambda wav: next(transcripts), respond, audio)
    loop.chime_seconds = 0
    assert await loop.once() == "what time is it"
    assert answered == ["what time is it"]
    await audio.close()


async def test_says_it_is_working_once_when_a_slow_tool_runs(tmp_path):
    from escanor.core.agent import Agent
    from escanor.core.session import SessionStore
    from escanor.models.base import ModelResponse, ToolCall
    from escanor.models.mock_provider import MockModel
    from escanor.voice.conversation import speak_turn

    class Ear:
        def __init__(self):
            self.said = []

        def say(self, text, priority=None):
            self.said.append(text)

    script = [ModelResponse(tool_calls=[ToolCall("1", "web_search", {"query": "a"})]),
              ModelResponse(tool_calls=[ToolCall("2", "web_search", {"query": "b"})]),
              ModelResponse(text="Found it.")]
    ear = Ear()
    await speak_turn(Agent(MockModel(script=script), "mock:m"), SessionStore(tmp_path / "s.db").create(), "look", ear)
    from escanor.core.persona import LINES
    assert sum(s in LINES["working"].values() for s in ear.said) == 1 and ear.said[-1] == "Found it."
