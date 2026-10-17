"""
core/session.py

One conversation: its messages in the provider-neutral shape every
Model understands, persisted to SQLite (~/.escanor/escanor.db) after
every append, so a crash or a Ctrl-C never loses what was said.

Message shapes (what Model.chat() / chat_stream_tools() accept):

  {"role": "user", "content": "..."}
  {"role": "assistant", "content": "...", "tool_calls": [ToolCall, ...],
   "raw": {...} | None, "model": "provider:model-id"}
  {"role": "tool", "tool_call_id": "...", "content": "...", "is_error": bool}

Two invariants the providers rely on, enforced here rather than trusted:

1. Every tool call has a result. Claude rejects a tool_use without a
   matching tool_result, so if a turn is interrupted between the model
   asking for a tool and the tool finishing, repair() fills the gap with
   a "cancelled" result before the next request.
2. History is append-only within one model. Claude's thinking blocks are
   only valid against the exact history that produced them, so the one
   rewrite this class does -- collapse_tool_history(), on a model switch
   -- only touches turns that the new model would not replay as-is anyway.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from ..models.base import ModelResponse, ToolCall
from ..tools.base import ToolResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'New conversation',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, id);
"""

CANCELLED_RESULT = "Cancelled: the turn was interrupted before this tool finished."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _encode(message: dict) -> str:
    body = dict(message)
    if body.get("tool_calls"):
        body["tool_calls"] = [
            {"id": c.id, "name": c.name, "arguments": c.arguments, "provider_extra": c.provider_extra}
            for c in body["tool_calls"]
        ]
    return json.dumps(body, ensure_ascii=False, default=str)


def _decode(text: str) -> dict:
    body = json.loads(text)
    if body.get("tool_calls"):
        body["tool_calls"] = [ToolCall(**c) for c in body["tool_calls"]]
    return body


class SessionStore:
    """The SQLite file holding every conversation."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)

    def create(self, title: str = "New conversation") -> "Session":
        cursor = self._db.execute("INSERT INTO conversations (title, created_at) VALUES (?, ?)", (title, _now()))
        self._db.commit()
        return Session(self, cursor.lastrowid, title, [])

    def load(self, conversation_id: int) -> "Session":
        row = self._db.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if row is None:
            raise KeyError(f"No conversation {conversation_id}")
        rows = self._db.execute(
            "SELECT body FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)
        ).fetchall()
        return Session(self, conversation_id, row[0], [_decode(r[0]) for r in rows])

    def latest_id(self) -> int | None:
        row = self._db.execute("SELECT id FROM conversations ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def _append(self, conversation_id: int, message: dict) -> None:
        self._db.execute(
            "INSERT INTO messages (conversation_id, body, created_at) VALUES (?, ?, ?)",
            (conversation_id, _encode(message), _now()),
        )
        self._db.commit()

    def _replace_all(self, conversation_id: int, messages: list[dict]) -> None:
        with self._db:
            self._db.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            self._db.executemany(
                "INSERT INTO messages (conversation_id, body, created_at) VALUES (?, ?, ?)",
                [(conversation_id, _encode(m), _now()) for m in messages],
            )

    def _rename(self, conversation_id: int, title: str) -> None:
        self._db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))
        self._db.commit()


class Session:
    def __init__(self, store: SessionStore, conversation_id: int, title: str, messages: list[dict]):
        self._store = store
        self.id = conversation_id
        self.title = title
        self.messages = messages

    def _add(self, message: dict) -> None:
        self.messages.append(message)
        self._store._append(self.id, message)

    def append_user(self, text: str) -> None:
        if not any(m["role"] == "user" for m in self.messages):
            self.title = text.strip()[:60] or self.title
            self._store._rename(self.id, self.title)
        self._add({"role": "user", "content": text})

    def append_assistant(self, response: ModelResponse, model_spec: str) -> None:
        self._add({
            "role": "assistant", "content": response.text, "tool_calls": list(response.tool_calls),
            "raw": response.raw, "model": model_spec,
        })

    def append_tool_result(self, call: ToolCall, result: ToolResult) -> None:
        self._add({"role": "tool", "tool_call_id": call.id, "content": result.output, "is_error": not result.ok})

    def pending_tool_calls(self) -> list[ToolCall]:
        """Tool calls in the latest assistant message that have no result yet."""
        for i in range(len(self.messages) - 1, -1, -1):
            message = self.messages[i]
            if message["role"] == "assistant":
                answered = {m["tool_call_id"] for m in self.messages[i + 1:] if m["role"] == "tool"}
                return [c for c in message.get("tool_calls") or [] if c.id not in answered]
        return []

    def repair(self) -> None:
        """Invariant 1: give every unanswered tool call a cancelled result."""
        for call in self.pending_tool_calls():
            self.append_tool_result(call, ToolResult(ok=False, output=CANCELLED_RESULT))

    def collapse_tool_history(self, new_model_spec: str) -> bool:
        """Invariant 2, on a model switch. Assistant turns produced by a
        different model than `new_model_spec` that used tools are
        rewritten as plain text, and their tool results folded into that
        text -- e.g. Gemini rejects replayed tool calls lacking its own
        thought_signature, and ids/formats differ per provider. Turns
        from new_model_spec itself are left untouched, so switching back
        to a model replays its own turns verbatim. Returns True if
        anything changed (and was persisted)."""
        results: dict[str, dict] = {m["tool_call_id"]: m for m in self.messages if m["role"] == "tool"}
        collapsed: list[dict] = []
        dropped_ids: set[str] = set()
        changed = False
        for message in self.messages:
            if message["role"] == "tool" and message["tool_call_id"] in dropped_ids:
                continue
            if (
                message["role"] == "assistant" and message.get("tool_calls")
                and message.get("model") != new_model_spec
            ):
                lines = [message["content"]] if message.get("content") else []
                for call in message["tool_calls"]:
                    dropped_ids.add(call.id)
                    result = results.get(call.id)
                    outcome = _summarise(result["content"]) if result else "no result"
                    lines.append(f"[used {call.name} {json.dumps(call.arguments, ensure_ascii=False)} -> {outcome}]")
                collapsed.append({"role": "assistant", "content": "\n".join(lines), "tool_calls": [], "raw": None,
                                  "model": message.get("model")})
                changed = True
                continue
            collapsed.append(message)
        if changed:
            self.messages = _merge_adjacent_assistants(collapsed)
            self._store._replace_all(self.id, self.messages)
        return changed


def _summarise(text: str, limit: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "..."


def _merge_adjacent_assistants(messages: list[dict]) -> list[dict]:
    """A collapsed tool turn is followed by the model's next assistant
    turn; providers expect user/assistant to alternate, so plain-text
    assistant messages that end up adjacent are joined into one."""
    merged: list[dict] = []
    for message in messages:
        previous = merged[-1] if merged else None
        if (
            previous and previous["role"] == "assistant" and message["role"] == "assistant"
            and not previous.get("tool_calls") and not previous.get("raw")
        ):
            joined = "\n".join(t for t in (previous.get("content"), message.get("content")) if t)
            # raw is dropped: the later turn's exact blocks no longer match
            # the joined content, so replaying them would contradict it.
            merged[-1] = {**message, "content": joined, "raw": None}
            continue
        merged.append(message)
    return merged


def default_store() -> SessionStore:
    from .. import config
    return SessionStore(config.data_dir() / "escanor.db")

