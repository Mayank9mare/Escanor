"""
music/tools.py

Music tools (the "music" group, switched on with enable_tools or
automatically by dj_start's description). Controlling the music app is
reversible and low-stakes, so these are "write", not "external": asking
"Allow?" before every skip would make a DJ unusable (decision D27).
"""
from __future__ import annotations
from ..tools.base import READ, WRITE, AsyncTool, Tool, ToolResult
from .dj import DJ
from .library import MusicLibrary
from .player import PlayerNotAuthorized, PlayerUnavailable, YouTubeMusicPlayer

GROUP = "music"
GROUP_DESCRIPTION = "play, queue and control YouTube Music; DJ mode with spoken intros"
_ERRORS = (PlayerUnavailable, PlayerNotAuthorized, RuntimeError)


class _MusicTool(Tool):
    group = GROUP
    side_effect = WRITE

    def __init__(self, player: YouTubeMusicPlayer, library: MusicLibrary):
        self.player = player
        self.library = library


class NowPlayingTool(_MusicTool):
    name = "music_now_playing"
    description = "What's playing in YouTube Music right now."
    input_schema = {"type": "object", "properties": {}}
    side_effect = READ

    def run(self) -> ToolResult:
        try:
            song = self.player.song()
        except _ERRORS as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output=song.describe() if song else "Nothing is playing.")


class PlayTool(_MusicTool):
    name = "music_play"
    description = "Search YouTube Music and play the best match now, e.g. 'Tum Hi Ho Arijit Singh' or 'lofi beats'."
    input_schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    def run(self, query: str) -> ToolResult:
        try:
            tracks = self.library.search(query, 3)
            if not tracks:
                return ToolResult(ok=False, output=f"No songs found for '{query}'.")
            self.player.play_now(tracks[0].video_id)
        except _ERRORS as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output=f"Playing {tracks[0].describe()}.")


class QueueTool(_MusicTool):
    name = "music_queue"
    description = "Add the best match for a search to the queue (next=true to play it right after this song)."
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "next": {"type": "boolean"}},
        "required": ["query"],
    }

    def run(self, query: str, next: bool = False) -> ToolResult:  # noqa: A002 -- matches the schema
        try:
            tracks = self.library.search(query, 3)
            if not tracks:
                return ToolResult(ok=False, output=f"No songs found for '{query}'.")
            self.player.enqueue(tracks[0].video_id, next_up=bool(next))
        except _ERRORS as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output=f"Queued {tracks[0].describe()}{' up next' if next else ''}.")


class ControlTool(_MusicTool):
    name = "music_control"
    description = "Control playback: pause, resume, next, previous, like, dislike, or set volume (0-100)."
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["pause", "resume", "next", "previous", "like", "dislike", "volume"]},
            "volume": {"type": "integer", "description": "0-100, only for action=volume"},
        },
        "required": ["action"],
    }

    def run(self, action: str, volume: int | None = None) -> ToolResult:
        actions = {
            "pause": self.player.pause, "resume": self.player.play, "next": self.player.next,
            "previous": self.player.previous, "like": self.player.like, "dislike": self.player.dislike,
        }
        try:
            if action == "volume":
                if volume is None:
                    return ToolResult(ok=False, output="Give a volume from 0 to 100.")
                self.player.set_volume(volume)
                return ToolResult(ok=True, output=f"Volume {max(0, min(100, int(volume)))}.")
            if action not in actions:
                return ToolResult(ok=False, output=f"Unknown action '{action}'.")
            actions[action]()
        except _ERRORS as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output="Done.")


class DJStart(AsyncTool):
    name = "dj_start"
    description = (
        "Start DJ mode for a mood or vibe (e.g. 'chill evening', 'focus coding', '90s bollywood'): "
        "Escanor picks songs and introduces some of them."
    )
    input_schema = {"type": "object", "properties": {"mood": {"type": "string", "description": "optional; picked from the time of day if left out"}}}
    group = GROUP
    side_effect = WRITE

    def __init__(self, dj: DJ):
        self.dj = dj

    async def run_async(self, mood: str = "") -> ToolResult:
        try:
            return ToolResult(ok=True, output=await self.dj.start(mood))
        except _ERRORS as e:
            return ToolResult(ok=False, output=str(e))


class MoreLikeThisTool(_MusicTool):
    name = "music_more_like_this"
    description = "Queue songs like the one playing now, up next (YouTube Music's radio for it)."
    input_schema = {"type": "object", "properties": {"count": {"type": "integer", "description": "default 3"}}}

    def run(self, count: int = 3) -> ToolResult:
        try:
            song = self.player.song()
            if song is None:
                return ToolResult(ok=False, output="Nothing is playing.")
            similar = self.library.radio(song.video_id, 15)[: max(1, min(int(count or 3), 10))]
            for track in reversed(similar):  # each goes "up next", so add in reverse to keep the order
                self.player.enqueue(track.video_id, next_up=True)
        except _ERRORS as e:
            return ToolResult(ok=False, output=str(e))
        if not similar:
            return ToolResult(ok=False, output="No similar songs found.")
        return ToolResult(ok=True, output="Up next: " + "; ".join(t.describe() for t in similar))


class DJStop(AsyncTool):
    name = "dj_stop"
    description = "Stop DJ mode (the music keeps playing)."
    input_schema = {"type": "object", "properties": {}}
    group = GROUP
    side_effect = WRITE

    def __init__(self, dj: DJ):
        self.dj = dj

    async def run_async(self) -> ToolResult:
        return ToolResult(ok=True, output=await self.dj.stop())


class DJStatus(AsyncTool):
    name = "dj_status"
    description = "Is DJ mode on, what mood, and what's up next."
    input_schema = {"type": "object", "properties": {}}
    group = GROUP
    side_effect = READ

    def __init__(self, dj: DJ):
        self.dj = dj

    async def run_async(self) -> ToolResult:
        return ToolResult(ok=True, output=self.dj.status())


def music_tools(player: YouTubeMusicPlayer, library: MusicLibrary, dj: DJ) -> list[Tool]:
    return [
        NowPlayingTool(player, library), PlayTool(player, library), QueueTool(player, library),
        ControlTool(player, library), MoreLikeThisTool(player, library), DJStart(dj), DJStop(dj), DJStatus(dj),
    ]
