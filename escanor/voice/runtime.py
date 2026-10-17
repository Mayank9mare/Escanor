"""
voice/runtime.py

Starting the voice side of Escanor: building the speech engines from
[voice] settings, and the two voice entry points --

  escanor serve    web UI (text + push-to-talk) at http://127.0.0.1:8770
  escanor listen   hands-free: wake word -> speak -> answer aloud
"""
from __future__ import annotations
import asyncio
import sys
from ..app import Escanor
from ..core.session import SessionStore
from .audio_out import DJ as DJ_PRIORITY
from .audio_out import AfplayPlayer, AudioOut, NullPlayer
from .speech import SpeechEngine


def attach_dj_voice(app: Escanor, audio: AudioOut) -> None:
    """Give the DJ the speaker, at the lowest priority."""
    dj = app.services.get("dj")
    if dj is not None:
        dj.say = lambda text: audio.say(text, DJ_PRIORITY)


def build_speech(settings: dict) -> SpeechEngine:
    voice = settings["voice"]
    return SpeechEngine(
        stt_engine=voice["stt"], tts_engine=voice["tts"],
        whisper_model=voice.get("whisper_model") or None, tts_voice=voice.get("tts_voice") or None,
    )


def serve(app: Escanor, store: SessionStore, speech: SpeechEngine | None, mute: bool = False) -> None:
    import uvicorn
    from ..server.app import create_server

    async def main() -> None:
        audio = None
        if speech is not None:
            audio = AudioOut(speech.synthesize, NullPlayer() if mute else AfplayPlayer(),
                             ducker=app.services.get("ducker"))
            attach_dj_voice(app, audio)
        web = create_server(app, store, speech, audio)
        host, port = app.settings["server"]["host"], int(app.settings["server"]["port"])
        print(f"Escanor is at http://{host}:{port}  (Ctrl-C to stop)")
        server = uvicorn.Server(uvicorn.Config(web, host=host, port=port, log_level="warning"))
        try:
            await server.serve()
        finally:
            if audio is not None:
                await audio.close()

    asyncio.run(main())


def listen(app: Escanor, store: SessionStore, speech: SpeechEngine, use_enter: bool = False) -> None:
    from .conversation import VoiceConfirmer, speak_turn
    from .listen import EnergyVAD, ListenLoop, MicFrames, OpenWakeWord

    async def main() -> None:
        audio = AudioOut(speech.synthesize, AfplayPlayer(), ducker=app.services.get("ducker"))
        attach_dj_voice(app, audio)
        frames = MicFrames()
        wake = None
        if not use_enter:
            try:
                wake = OpenWakeWord(app.settings["voice"]["wake_word"])
            except RuntimeError as e:
                print(f"{e}\nFalling back to: press Enter, then speak.", file=sys.stderr)

        async def wait_enter() -> None:
            await asyncio.to_thread(sys.stdin.readline)
            frames.drain()  # don't treat audio from before Enter as speech

        latest = store.latest_id()
        session = store.load(latest) if latest is not None else store.create()
        loop: ListenLoop | None = None

        async def hear_answer() -> str:
            return await loop.hear()

        confirmer = VoiceConfirmer(audio, hear_answer)

        async def respond(text: str) -> None:
            def show(event) -> None:
                from ..core.events import TextDelta
                if isinstance(event, TextDelta):
                    print(event.text, end="", flush=True)
            print("escanor › ", end="", flush=True)
            await speak_turn(app.agent, session, text, audio, confirmer, show)
            print()

        loop = ListenLoop(
            frames, EnergyVAD(), wake, speech.transcribe, respond, audio,
            barge_in=app.settings["voice"]["barge_in"] == "headphones",
            wait_enter=wait_enter, on_status=lambda s: print(f"· {s}", flush=True),
        )
        print(f"Escanor is listening ({app.agent.model_spec}). Ctrl-C to stop.")
        try:
            await loop.run()
        finally:
            frames.close()
            await audio.close()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
