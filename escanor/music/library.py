"""
music/library.py

What's in YouTube Music, via ytmusicapi (unofficial; it speaks the web
client's API):

- search() and radio() work without logging in.
- history() and liked() need your account: run `ytmusicapi browser` once
  and save the result as ~/.escanor/ytmusic_auth.json (escanor music
  connect explains the steps). Those cookies expire after days to weeks;
  when they do, these two return [] with a note instead of failing.

Results are cached for 10 minutes so the DJ doesn't hammer the API.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

CACHE_SECONDS = 600


@dataclass(frozen=True)
class Track:
    video_id: str
    title: str
    artists: tuple[str, ...] = ()
    album: str | None = None
    duration: str | None = None

    @property
    def artist(self) -> str:
        return ", ".join(self.artists) or "unknown artist"

    def describe(self) -> str:
        return f"{self.title} — {self.artist}" + (f" ({self.album})" if self.album else "")


def track_from(item: dict[str, Any]) -> Track | None:
    video_id = item.get("videoId")
    if not video_id:
        return None
    artists = tuple(a.get("name", "") for a in item.get("artists") or [] if a.get("name"))
    album = item.get("album")
    return Track(video_id=video_id, title=item.get("title", ""), artists=artists,
                 album=album.get("name") if isinstance(album, dict) else album,
                 duration=item.get("duration") or item.get("length"))


@dataclass
class _Cached:
    at: float
    value: Any


class MusicLibrary:
    def __init__(self, auth_path: Path | None = None, client_factory: Callable[[Path | None], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.auth_path = auth_path or Path.home() / ".escanor" / "ytmusic_auth.json"
        self._factory = client_factory or _default_client
        self._clock = clock
        self._public: Any = None
        self._account: Any = None
        self._cache: dict[tuple, _Cached] = {}
        self.last_note = ""

    @property
    def has_account(self) -> bool:
        return self.auth_path.is_file()

    def _client(self, account: bool) -> Any:
        if account:
            if not self.has_account:
                return None
            if self._account is None:
                self._account = self._factory(self.auth_path)
            return self._account
        if self._public is None:
            self._public = self._factory(None)
        return self._public

    def _cached(self, key: tuple, fetch: Callable[[], Any]) -> Any:
        hit = self._cache.get(key)
        if hit and self._clock() - hit.at < CACHE_SECONDS:
            return hit.value
        value = fetch()
        self._cache[key] = _Cached(self._clock(), value)
        return value

    def search(self, query: str, limit: int = 5) -> list[Track]:
        def fetch() -> list[Track]:
            items = self._client(False).search(query, filter="songs", limit=limit)
            return [t for t in (track_from(i) for i in items) if t][:limit]
        return self._cached(("search", query.lower(), limit), fetch)

    def radio(self, video_id: str, limit: int = 25) -> list[Track]:
        """Songs YouTube Music would play after this one (its 'radio')."""
        def fetch() -> list[Track]:
            data = self._client(False).get_watch_playlist(videoId=video_id, limit=limit)
            tracks = [t for t in (track_from(i) for i in data.get("tracks", [])) if t]
            return [t for t in tracks if t.video_id != video_id]
        return self._cached(("radio", video_id, limit), fetch)

    def history(self, limit: int = 50) -> list[Track]:
        return self._account_list("history", lambda c: c.get_history(), limit)

    def liked(self, limit: int = 100) -> list[Track]:
        return self._account_list("liked", lambda c: c.get_liked_songs(limit=limit).get("tracks", []), limit)

    def _account_list(self, name: str, fetch_items: Callable[[Any], list], limit: int) -> list[Track]:
        client = self._client(True)
        if client is None:
            self.last_note = "Not signed in to YouTube Music for history/likes (escanor music connect)."
            return []

        def fetch() -> list[Track]:
            try:
                items = fetch_items(client)
            except Exception as e:  # noqa: BLE001 -- expired cookies surface as assorted errors
                self.last_note = f"YouTube Music sign-in may have expired ({type(e).__name__}); re-run escanor music connect."
                return []
            return [t for t in (track_from(i) for i in items) if t][:limit]
        return self._cached((name, limit), fetch)


def _default_client(auth_path: Path | None) -> Any:
    try:
        from ytmusicapi import YTMusic
    except ImportError as e:
        raise RuntimeError("Install the music extra (`uv pip install -e '.[music]'`) for YouTube Music search.") from e
    return YTMusic(str(auth_path)) if auth_path else YTMusic()
