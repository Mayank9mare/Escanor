"""
music/player.py

Controls playback in the YouTube Music desktop app (th-ch/youtube-music)
through its API Server plugin. Playback stays in that app, signed in to
your Premium account; Escanor only presses its buttons.

Facts from the plugin's source (IMPLEMENTATION_PLAN.md Phase 0):
- REST under /api/v1, default port 26538.
- Auth: POST /auth/<client id> pops an allow/deny dialog in the app once
  and returns {"accessToken": <JWT>} (no expiry). Every /api call then
  sends "Authorization: Bearer <token>"; a revoked/unknown token -> 401.
- GET /api/v1/song -> {title, artist, album, videoId, songDuration,
  elapsedSeconds, isPaused, ...} or 204 when nothing is loaded.
- POST /api/v1/queue {videoId, insertPosition: INSERT_AT_END |
  INSERT_AFTER_CURRENT_VIDEO}; GET/POST /api/v1/volume ({state, isMuted} / {volume}).

The token is stored in ~/.escanor/ytm_token with owner-only permissions.
Set the plugin's hostname to 127.0.0.1 so the API isn't reachable from
your network (its default is 0.0.0.0).
"""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://127.0.0.1:26538"
CLIENT_ID = "escanor"


class PlayerUnavailable(RuntimeError):
    """The app isn't running, or its API Server plugin is off."""


class PlayerNotAuthorized(RuntimeError):
    """No token yet, or the app no longer accepts it."""


@dataclass
class Song:
    title: str
    artist: str
    video_id: str
    album: str | None = None
    duration: float = 0.0
    elapsed: float = 0.0
    paused: bool = False

    @property
    def remaining(self) -> float:
        return max(0.0, self.duration - self.elapsed)

    def describe(self) -> str:
        album = f" ({self.album})" if self.album else ""
        state = "paused" if self.paused else "playing"
        return f"{self.title} by {self.artist}{album} — {state}, {int(self.elapsed)}s of {int(self.duration)}s"


class YouTubeMusicPlayer:
    def __init__(self, base_url: str = DEFAULT_URL, token_path: Path | None = None, timeout: float = 5):
        self.base_url = base_url.rstrip("/")
        self.token_path = token_path or Path.home() / ".escanor" / "ytm_token"
        self.timeout = timeout

    # ---------- auth ----------

    def token(self) -> str | None:
        try:
            return self.token_path.read_text().strip() or None
        except OSError:
            return None

    def authorize(self) -> None:
        """Ask the app for a token. The app shows an allow/deny dialog: click Allow."""
        data = self._request("POST", f"/auth/{CLIENT_ID}", auth=False, timeout=120)
        token = (data or {}).get("accessToken")
        if not token:
            raise PlayerNotAuthorized("The YouTube Music app didn't return a token (was the request denied?).")
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)

    # ---------- playback ----------

    def song(self) -> Song | None:
        data = self._api("GET", "/song")
        if not data:
            return None
        return Song(
            title=data.get("title", ""), artist=data.get("artist", ""), video_id=data.get("videoId", ""),
            album=data.get("album"), duration=float(data.get("songDuration") or 0),
            elapsed=float(data.get("elapsedSeconds") or 0), paused=bool(data.get("isPaused")),
        )

    def play(self) -> None:
        self._api("POST", "/play")

    def pause(self) -> None:
        self._api("POST", "/pause")

    def toggle(self) -> None:
        self._api("POST", "/toggle-play")

    def next(self) -> None:
        self._api("POST", "/next")

    def previous(self) -> None:
        self._api("POST", "/previous")

    def like(self) -> None:
        self._api("POST", "/like")

    def dislike(self) -> None:
        self._api("POST", "/dislike")

    def enqueue(self, video_id: str, next_up: bool = False) -> None:
        position = "INSERT_AFTER_CURRENT_VIDEO" if next_up else "INSERT_AT_END"
        self._api("POST", "/queue", {"videoId": video_id, "insertPosition": position})

    def play_now(self, video_id: str) -> None:
        """Put a song right after the current one and skip to it."""
        current = self.song()
        self.enqueue(video_id, next_up=True)
        if current is None:
            self.play()
        else:
            self.next()

    def queue(self) -> Any:
        return self._api("GET", "/queue")

    def clear_queue(self) -> None:
        self._api("DELETE", "/queue")

    def volume(self) -> int:
        data = self._api("GET", "/volume") or {}
        return int(round(float(data.get("state", 0))))

    def set_volume(self, volume: int) -> None:
        self._api("POST", "/volume", {"volume": max(0, min(100, int(volume)))})

    # ---------- http ----------

    def _api(self, method: str, path: str, body: dict | None = None) -> Any:
        return self._request(method, f"/api/v1{path}", body)

    def _request(self, method: str, path: str, body: dict | None = None, auth: bool = True,
                 timeout: float | None = None) -> Any:
        headers = {"Content-Type": "application/json"}
        if auth:
            token = self.token()
            if not token:
                raise PlayerNotAuthorized("Escanor isn't connected to YouTube Music yet. Run `escanor music connect`.")
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise PlayerNotAuthorized("YouTube Music refused Escanor's token. Run `escanor music connect` again.") from e
            raise RuntimeError(f"YouTube Music API error {e.code} on {method} {path}.") from e
        except (urllib.error.URLError, OSError) as e:
            raise PlayerUnavailable(
                "Can't reach the YouTube Music app. Open it and enable Plugins → API Server (port 26538)."
            ) from e
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None


class PlayerDucker:
    """The AudioOut Ducker (voice/audio_out.py): lowers the music while
    Escanor talks or listens and puts it back afterwards. Failures are
    swallowed by AudioOut, so speech never waits on the music app."""

    def __init__(self, player: YouTubeMusicPlayer, level: int = 20):
        self._player = player
        self._level = level
        self._saved: int | None = None

    def duck(self) -> None:
        # Not connected / app not open is a normal state: there's simply no
        # music to turn down, so do nothing rather than complain (B19).
        try:
            current = self._player.volume()
            if current > self._level:
                self._saved = current
                self._player.set_volume(self._level)
        except (PlayerNotAuthorized, PlayerUnavailable):
            self._saved = None

    def restore(self) -> None:
        if self._saved is not None:
            saved, self._saved = self._saved, None
            try:
                self._player.set_volume(saved)
            except (PlayerNotAuthorized, PlayerUnavailable):
                pass
