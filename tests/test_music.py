import asyncio
import json
import stat
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from escanor.core.agent import Agent
from escanor.core.events import ToolFinished
from escanor.models.base import ModelResponse, ToolCall
from escanor.models.mock_provider import MockModel
from escanor.music.dj import DJ, SpendGuard
from escanor.music.library import MusicLibrary, Track
from escanor.music.player import PlayerDucker, PlayerNotAuthorized, PlayerUnavailable, Song, YouTubeMusicPlayer
from escanor.music.tools import PlayTool, music_tools
from escanor.tools.registry import ToolRegistry


# ---------- a fake YouTube Music app (th-ch API Server) ----------

class FakeApp:
    def __init__(self):
        self.token = "jwt-123"
        self.song = {"title": "Tum Hi Ho", "artist": "Arijit Singh", "videoId": "v1", "songDuration": 260,
                     "elapsedSeconds": 30, "isPaused": False, "album": "Aashiqui 2"}
        self.volume = 70
        self.calls: list[tuple[str, str, dict | None]] = []


@pytest.fixture
def fake_app():
    app = FakeApp()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code, body=None):
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            app.calls.append((method, self.path, body))
            if self.path == "/auth/escanor" and method == "POST":
                return self._reply(200, {"accessToken": app.token})
            if self.headers.get("Authorization") != f"Bearer {app.token}":
                return self._reply(401, {"error": "Unauthorized"})
            if self.path == "/api/v1/song":
                return self._reply(200, app.song) if app.song else self._reply(204)
            if self.path == "/api/v1/volume":
                if method == "GET":
                    return self._reply(200, {"state": app.volume, "isMuted": False})
                app.volume = body["volume"]
                return self._reply(204)
            return self._reply(204)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_DELETE(self):
            self._handle("DELETE")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield app
    server.shutdown()


@pytest.fixture
def player(fake_app, tmp_path):
    return YouTubeMusicPlayer(fake_app.url, token_path=tmp_path / "ytm_token")


# ---------- player ----------

def test_should_ask_to_connect_when_no_token(player):
    with pytest.raises(PlayerNotAuthorized, match="music connect"):
        player.song()


def test_should_store_token_privately_and_send_it_as_bearer(player, fake_app):
    player.authorize()
    assert player.token() == "jwt-123"
    assert stat.S_IMODE(player.token_path.stat().st_mode) == 0o600
    song = player.song()
    assert song == Song("Tum Hi Ho", "Arijit Singh", "v1", "Aashiqui 2", 260.0, 30.0, False)
    assert song.remaining == 230.0


def test_should_report_unauthorized_when_token_revoked(player, fake_app):
    player.authorize()
    fake_app.token = "rotated"
    with pytest.raises(PlayerNotAuthorized, match="refused"):
        player.song()


def test_should_report_unavailable_when_app_not_running(tmp_path):
    p = YouTubeMusicPlayer("http://127.0.0.1:9", token_path=tmp_path / "t", timeout=1)
    p.token_path.write_text("x")
    with pytest.raises(PlayerUnavailable, match="API Server"):
        p.song()


def test_should_return_none_when_nothing_loaded(player, fake_app):
    player.authorize()
    fake_app.song = None
    assert player.song() is None


def test_should_queue_next_and_skip_when_playing_now(player, fake_app):
    player.authorize()
    player.play_now("v9")
    posts = [(path, body) for method, path, body in fake_app.calls if method == "POST" and path.startswith("/api")]
    assert posts == [("/api/v1/queue", {"videoId": "v9", "insertPosition": "INSERT_AFTER_CURRENT_VIDEO"}),
                     ("/api/v1/next", None)]


def test_should_clamp_volume(player, fake_app):
    player.authorize()
    player.set_volume(150)
    assert fake_app.volume == 100


def test_ducker_lowers_then_restores_volume(player, fake_app):
    player.authorize()
    ducker = PlayerDucker(player, level=20)
    ducker.duck()
    assert fake_app.volume == 20
    ducker.restore()
    assert fake_app.volume == 70
    fake_app.volume = 10
    ducker.duck()
    ducker.restore()
    assert fake_app.volume == 10  # already quieter than the duck level: left alone


# ---------- library ----------

class FakeYT:
    def __init__(self, fail=False):
        self.fail = fail
        self.searches = 0

    def search(self, query, filter, limit):
        self.searches += 1
        return [{"videoId": "a", "title": "Song A", "artists": [{"name": "X"}], "album": {"name": "Al"}},
                {"title": "no id"}, {"videoId": "b", "title": "Song B", "artists": []}]

    def get_watch_playlist(self, videoId, limit):
        return {"tracks": [{"videoId": videoId, "title": "seed"}, {"videoId": "r1", "title": "R1"}]}

    def get_history(self):
        if self.fail:
            raise KeyError("cookie expired")
        return [{"videoId": "h1", "title": "H1"}]

    def get_liked_songs(self, limit):
        return {"tracks": [{"videoId": "l1", "title": "L1"}]}


def test_should_parse_search_and_skip_items_without_ids(tmp_path):
    fake = FakeYT()
    library = MusicLibrary(tmp_path / "auth.json", client_factory=lambda auth: fake)
    tracks = library.search("anything")
    assert [t.video_id for t in tracks] == ["a", "b"]
    assert tracks[0].describe() == "Song A — X (Al)"
    library.search("ANYTHING")
    assert fake.searches == 1  # cached, case-insensitively


def test_should_exclude_seed_from_radio(tmp_path):
    library = MusicLibrary(tmp_path / "auth.json", client_factory=lambda auth: FakeYT())
    assert [t.video_id for t in library.radio("seed")] == ["r1"]


def test_should_return_empty_with_note_when_not_signed_in(tmp_path):
    library = MusicLibrary(tmp_path / "auth.json", client_factory=lambda auth: FakeYT())
    assert library.history() == [] and "music connect" in library.last_note


def test_should_return_empty_with_note_when_cookies_expired(tmp_path):
    (tmp_path / "auth.json").write_text("{}")
    library = MusicLibrary(tmp_path / "auth.json", client_factory=lambda auth: FakeYT(fail=auth is not None))
    assert library.history() == [] and "expired" in library.last_note
    assert [t.video_id for t in library.liked()] == ["l1"]


# ---------- spend guard ----------

def test_spend_guard_caps_per_day_and_persists(tmp_path):
    today = [date(2026, 10, 17)]
    guard = SpendGuard(tmp_path / "usage.json", 2, today=lambda: today[0])
    assert guard.allow() and guard.allow() and not guard.allow()
    assert SpendGuard(tmp_path / "usage.json", 2, today=lambda: today[0]).used() == 2
    today[0] = date(2026, 10, 18)
    assert guard.allow()


# ---------- DJ ----------

class MemoryPlayer:
    """In-memory stand-in for the app, recording the order of actions."""

    def __init__(self, song=None):
        self.current = song
        self.log: list[str] = []

    def song(self):
        return self.current

    def play_now(self, video_id):
        self.log.append(f"play_now:{video_id}")
        self.current = Song("Seed", "Artist", video_id, duration=200, elapsed=0)

    def enqueue(self, video_id, next_up=False):
        self.log.append(f"enqueue:{video_id}:{next_up}")

    def pause(self):
        self.log.append("pause")

    def play(self):
        self.log.append("play")


class MemoryLibrary:
    def search(self, query, limit=5):
        return [Track("seed", "Seed", ("Artist",))]

    def radio(self, video_id, limit=25):
        return [Track("seed", "Seed"), Track("r1", "Next One", ("Band",)), Track("r2", "Other", ("Band",))]

    def liked(self, limit=100):
        return [Track("r1", "Next One", ("Band",)), Track("l1", "Liked", ("Me",))]


def _dj(tmp_path, player, model=None, cap=10, said=None, patter_every=1):
    async def say(text):
        said.append(text)
        player.log.append("say")
    return DJ(player, MemoryLibrary(), model, SpendGuard(tmp_path / "u.json", cap),
              say=say if said is not None else None, patter_every=patter_every, run_loop=False)


async def test_should_start_from_search_when_nothing_playing(tmp_path):
    player = MemoryPlayer()
    dj = _dj(tmp_path, player)
    message = await dj.start("chill evening")
    assert "chill evening" in message and player.log == ["play_now:seed"]
    await dj.stop()
    assert not dj.running


async def test_should_pick_queue_and_introduce_next_track(tmp_path):
    player = MemoryPlayer(Song("Seed", "Artist", "seed", duration=200, elapsed=190))
    model = MockModel(script=[ModelResponse(text='{"pick": 1}'), ModelResponse(text="Up next, Liked by Me. Enjoy.")])
    said: list[str] = []
    dj = _dj(tmp_path, player, model, said=said)
    await dj.start("focus")
    await dj.tick()  # first sight of the song: just records it
    await dj.tick()  # near the end: choose + queue + write intro
    # candidates: r1, r2 (radio) then l1 (liked); seed is excluded as already played
    assert player.log == ["enqueue:r2:True"]
    assert dj.state.upcoming.video_id == "r2" and dj.state.intro == "Up next, Liked by Me. Enjoy."
    assert "Mood: focus" in model.calls[0]["messages"][0]["content"]
    await dj.stop()


async def test_should_pause_speak_and_resume_when_introduced_track_starts(tmp_path):
    player = MemoryPlayer(Song("Seed", "Artist", "seed", duration=200, elapsed=190))
    model = MockModel(script=[ModelResponse(text='{"pick": 0}'), ModelResponse(text="Here's Next One by Band.")])
    said: list[str] = []
    dj = _dj(tmp_path, player, model, said=said)
    await dj.start("focus")
    await dj.tick()
    await dj.tick()
    assert dj.state.upcoming.video_id == "r1" and dj.state.intro == "Here's Next One by Band."
    player.current = Song("Next One", "Band", "r1", duration=180, elapsed=1)
    await dj.tick()
    assert said == ["Here's Next One by Band."]
    assert player.log[-3:] == ["pause", "say", "play"]
    await dj.stop()


async def test_should_avoid_repeats_and_stay_silent_when_cap_reached(tmp_path):
    player = MemoryPlayer(Song("Seed", "Artist", "seed", duration=200, elapsed=190))
    model = MockModel(script=[])
    said: list[str] = []
    dj = _dj(tmp_path, player, model, cap=0, said=said)
    await dj.start("focus")
    await dj.tick()
    await dj.tick()
    assert dj.state.upcoming.video_id == "r1"  # first unplayed candidate, no model call
    assert model.calls == [] and dj.state.intro is None
    await dj.stop()


async def test_should_resume_music_even_if_speaking_fails(tmp_path):
    player = MemoryPlayer(Song("Seed", "Artist", "seed", duration=200, elapsed=190))

    async def broken_say(text):
        raise RuntimeError("speaker gone")

    dj = DJ(player, MemoryLibrary(), None, SpendGuard(tmp_path / "u.json", 5), say=broken_say, run_loop=False)
    dj.state.upcoming, dj.state.intro, dj.state.last_video = Track("r1", "Next One"), "intro", "seed"
    player.current = Song("Next One", "Band", "r1", duration=180, elapsed=1)
    with pytest.raises(RuntimeError):
        await dj.tick()
    assert player.log == ["pause", "play"]


# ---------- tools ----------

def test_play_tool_reports_app_problems_as_results(tmp_path):
    p = YouTubeMusicPlayer("http://127.0.0.1:9", token_path=tmp_path / "t", timeout=1)
    library = MusicLibrary(tmp_path / "a.json", client_factory=lambda auth: FakeYT())
    result = PlayTool(p, library).run(query="song a")
    assert result.ok is False and "music connect" in result.output


async def test_dj_start_runs_through_agent_as_async_tool(tmp_path, store):
    player = MemoryPlayer()
    dj = _dj(tmp_path, player)
    registry = ToolRegistry(music_tools(player, MemoryLibrary(), dj))
    registry.enable("music")
    model = MockModel(script=[ModelResponse(tool_calls=[ToolCall("c1", "dj_start", {"mood": "rainy day"})]),
                              ModelResponse(text="Enjoy.")])
    events = [e async for e in Agent(model, "mock:m", tools=registry).run_turn(store.create(), "dj something rainy")]
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.result.ok and "rainy day" in finished.result.output
    assert dj.running
    await dj.stop()


def test_ducker_is_silent_when_music_not_connected(tmp_path):
    """B19: no token / app closed is normal; ducking just does nothing."""
    not_connected = YouTubeMusicPlayer("http://127.0.0.1:9", token_path=tmp_path / "missing", timeout=1)
    ducker = PlayerDucker(not_connected)
    ducker.duck()
    ducker.restore()
    closed_app = YouTubeMusicPlayer("http://127.0.0.1:9", token_path=tmp_path / "t", timeout=1)
    closed_app.token_path.write_text("x")
    PlayerDucker(closed_app).duck()
