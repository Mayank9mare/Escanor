"""
telemetry.py

Metrics + tracing for every LLM call Escanor makes. Copied from kmare-code, where it spanned three
components (the `kmare` CLI/harness, kmare-chat, kmare-voice). Hand-rolled
like everything else here: one SQLite table and a thin Model wrapper
(models/instrumented.py) that records around every chat()/chat_stream()
call. No tracing library, no metrics agent -- this is local dev telemetry
for a personal project, not a production SaaS.

Two concepts, matching what a real tracing system calls them:

  trace -- one user-visible unit of work: one harness request (planner +
           every generate/evaluate round), one chat message, one voice
           turn. Just a plain string id (new_trace_id()) -- see below
           for why it isn't ambient context.

  span  -- one LLM call within a trace. A single harness request makes
           several (one planner call, then a generator + evaluator call
           per round). Recorded with record_call()/record_span(),
           tagged with the trace_id it belongs to, so the dashboard can
           group spans back into a timeline per trace.

Why trace_id is threaded explicitly instead of ambient (contextvars)
---------------------------------------------------------------------
The first version of this used contextvars.ContextVar to carry "which
trace is this call part of" implicitly, the way OpenTelemetry does.
That broke for kmare-chat's streaming path specifically: Starlette
drives a sync generator's response chunk-by-chunk via
`anyio.to_thread.run_sync(next, iterator)`, and each of those
dispatches runs the generator's next step in its own *copy* of the
context -- a `ContextVar.set()` made during one chunk isn't visible
(and its Token isn't even resettable -- "was created in a different
Context") during a later chunk. Confirmed by actually running it against
a real streaming call, not just reasoned about.

So instead: whoever opens a unit of work calls new_trace_id() and hands
that plain string to Model.with_trace(trace_id, component) (see
models/base.py's default no-op and models/instrumented.py's real
version) on every Model instance that should be attributed to it,
before making any calls. A harness run reuses the same three model
objects across many requests (the REPL), so with_trace() is called
again -- mutating trace attribution -- at the start of every run_pge()
call, right before the planner/generator/evaluator rounds that request
will make.

Turns (added for Escanor; see `escanor trace` and the HUD's Traces page)
-----------------------------------------------------------------------
A turn is a trace: Agent.run_turn() mints its trace_id and attributes
every model call to it (InstrumentedModel.traced() makes a per-turn
copy, so two turns at once never mix). Beside the model calls:

  spans  -- everything else a turn does, one row each: a tool call,
            speech-to-text, a sentence of text-to-speech, a failover, a
            compaction. kind + name + start + duration + status + detail.
  turns  -- one row per turn, written when it ends (the "receipt", an
            idea from JBIQ): where it came from, total time, time to the
            first word and to the first sound, steps, tools, tokens,
            cost, and how it ended.

Storage is ~/.escanor/telemetry.db by default (ESCANOR_TELEMETRY_DB to
override) -- separate from chatbox.db/voice.db since this is call-level
data, not conversation data, and every component writes to the same one
file so the dashboard has a single place to read from.
"""
from __future__ import annotations
import dataclasses
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _json_default(obj: Any) -> Any:
    # messages/responses can carry ToolCall dataclasses (see
    # models/base.py) that json.dumps doesn't know how to serialize on
    # its own.
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return str(obj)


def _to_json(value: Any) -> str | None:
    # Recording a call must never be the thing that crashes a call --
    # this sits in the hot path of every single LLM request across all
    # three components. Something unexpectedly unserializable becomes a
    # string, not an exception.
    if value is None:
        return None
    try:
        return json.dumps(value, default=_json_default)
    except (TypeError, ValueError) as e:
        return json.dumps({"telemetry_serialization_error": str(e)})


class TelemetryStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()   # speech is recorded from worker threads
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                span_id TEXT NOT NULL,
                component TEXT NOT NULL,
                role TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                started_at TEXT NOT NULL,
                duration_ms REAL NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                streamed INTEGER NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                meta TEXT
            )
        """)
        # Added after the first version shipped -- ALTER TABLE rather
        # than baking into CREATE TABLE, so a telemetry.db from before
        # this existed keeps working instead of erroring on missing
        # columns.
        existing_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(calls)")}
        for column in ("request_json", "response_json"):
            if column not in existing_columns:
                self._conn.execute(f"ALTER TABLE calls ADD COLUMN {column} TEXT")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS spans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                duration_ms REAL NOT NULL,
                status TEXT NOT NULL,
                detail TEXT
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS turns (
                trace_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                source TEXT NOT NULL,
                conversation_id INTEGER,
                model TEXT,
                text TEXT,
                duration_ms REAL,
                ttft_ms REAL,
                first_audio_ms REAL,
                steps INTEGER,
                tools INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                usd REAL,
                outcome TEXT,
                error TEXT
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_started ON turns(started_at)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_trace ON calls(trace_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started_at)")
        self._conn.commit()

    def insert_call(
        self, *, trace_id: str, span_id: str, component: str, role: str, provider: str, model: str,
        started_at: str, duration_ms: float, status: str, error: str | None, streamed: bool,
        input_tokens: int | None, output_tokens: int | None, meta: dict[str, Any] | None,
        request: Any = None, response: Any = None,
    ) -> None:
        with self._lock:
            self._insert_call(trace_id, span_id, component, role, provider, model, started_at, duration_ms,
                              status, error, streamed, input_tokens, output_tokens, meta, request, response)

    def _insert_call(self, trace_id, span_id, component, role, provider, model, started_at, duration_ms,
                     status, error, streamed, input_tokens, output_tokens, meta, request, response) -> None:
        self._conn.execute(
            """INSERT INTO calls
               (trace_id, span_id, component, role, provider, model, started_at, duration_ms,
                status, error, streamed, input_tokens, output_tokens, meta, request_json, response_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trace_id, span_id, component, role, provider, model, started_at, duration_ms,
                status, error, int(streamed), input_tokens, output_tokens,
                json.dumps(meta) if meta else None, _to_json(request), _to_json(response),
            ),
        )
        self._conn.commit()

    # ---- turns and their spans ----

    def insert_span(self, *, trace_id: str, kind: str, name: str, started_at: str, duration_ms: float,
                    status: str = "ok", detail: dict | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO spans (trace_id, kind, name, started_at, duration_ms, status, detail) VALUES (?,?,?,?,?,?,?)",
                (trace_id, kind, name, started_at, duration_ms, status, _to_json(detail) if detail else None))
            self._conn.commit()

    def insert_turn(self, turn: dict) -> None:
        columns = ", ".join(turn)
        with self._lock:
            self._conn.execute(f"INSERT OR REPLACE INTO turns ({columns}) VALUES ({', '.join('?' * len(turn))})",
                               tuple(turn.values()))
            self._conn.commit()

    def set_first_audio(self, trace_id: str, ms: float) -> bool:
        """False if the turn has no receipt yet (it's still running)."""
        with self._lock:
            cursor = self._conn.execute("UPDATE turns SET first_audio_ms = ? WHERE trace_id = ? AND first_audio_ms IS NULL",
                                        (ms, trace_id))
            self._conn.commit()
            return cursor.rowcount > 0 or self._conn.execute(
                "SELECT 1 FROM turns WHERE trace_id = ?", (trace_id,)).fetchone() is not None

    def recent_turns(self, limit: int = 30) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM turns ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def turn(self, trace_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM turns WHERE trace_id = ? OR trace_id LIKE ?",
                                 (trace_id, f"{trace_id}%")).fetchone()
        return dict(row) if row else None

    def timeline(self, trace_id: str) -> list[dict]:
        """Every model call and span of a turn, in start order, as
        {kind, name, started_at, duration_ms, status, detail}."""
        items = []
        for c in self._conn.execute("SELECT * FROM calls WHERE trace_id = ?", (trace_id,)).fetchall():
            response = json.loads(c["response_json"]) if c["response_json"] else {}
            tools = [t.get("name") for t in (response.get("tool_calls") or []) if isinstance(t, dict)]
            items.append({"kind": "model", "name": f"{c['provider']}/{c['model']}", "started_at": c["started_at"],
                          "duration_ms": c["duration_ms"], "status": c["status"],
                          "detail": {"in": c["input_tokens"], "out": c["output_tokens"], "role": c["role"],
                                     **({"asked_for": tools} if tools else {}),
                                     **({"error": c["error"][:200]} if c["error"] else {})}})
        for s in self._conn.execute("SELECT * FROM spans WHERE trace_id = ?", (trace_id,)).fetchall():
            items.append({"kind": s["kind"], "name": s["name"], "started_at": s["started_at"],
                          "duration_ms": s["duration_ms"], "status": s["status"],
                          "detail": json.loads(s["detail"]) if s["detail"] else {}})
        return sorted(items, key=lambda i: i["started_at"])

    def summary(self) -> list[dict]:
        """One row per (component, provider, model, role): call count,
        error count, average/total latency, total tokens -- the
        dashboard's top-level aggregate view."""
        rows = self._conn.execute("""
            SELECT component, provider, model, role,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                   AVG(duration_ms) AS avg_duration_ms,
                   SUM(duration_ms) AS total_duration_ms,
                   SUM(COALESCE(input_tokens, 0)) AS total_input_tokens,
                   SUM(COALESCE(output_tokens, 0)) AS total_output_tokens
            FROM calls
            GROUP BY component, provider, model, role
            ORDER BY calls DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def usage_since(self, started_at_iso: str) -> list[dict]:
        """Calls and tokens per provider/model since a UTC ISO timestamp --
        what the spend cap and `escanor usage` read."""
        rows = self._conn.execute("""
            SELECT provider, model, COUNT(*) AS calls,
                   SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                   SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                   AVG(duration_ms) AS avg_duration_ms
            FROM calls WHERE started_at >= ? AND status = 'ok'
            GROUP BY provider, model ORDER BY calls DESC
        """, (started_at_iso,)).fetchall()
        return [dict(r) for r in rows]

    def recent_traces(self, limit: int = 50) -> list[dict]:
        """One row per trace: its component, when it started, how many
        spans it made, total duration, and whether anything in it
        errored -- the dashboard's trace list."""
        rows = self._conn.execute("""
            SELECT trace_id,
                   component,
                   MIN(started_at) AS started_at,
                   COUNT(*) AS spans,
                   SUM(duration_ms) AS total_duration_ms,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                   GROUP_CONCAT(DISTINCT provider || '/' || model) AS models
            FROM calls
            GROUP BY trace_id
            ORDER BY started_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def trace_spans(self, trace_id: str) -> list[dict]:
        """Every span in one trace, in the order they ran -- what the
        dashboard's timeline view for a single trace renders."""
        rows = self._conn.execute(
            "SELECT * FROM calls WHERE trace_id = ? ORDER BY started_at ASC", (trace_id,),
        ).fetchall()
        return [dict(r) for r in rows]


_store: TelemetryStore | None = None


def get_store() -> TelemetryStore:
    global _store
    if _store is None:
        db_path = Path(os.environ.get("ESCANOR_TELEMETRY_DB", str(Path.home() / ".escanor" / "telemetry.db")))
        _store = TelemetryStore(db_path)
    return _store


def record_call(
    *, trace_id: str | None, component: str | None, role: str, provider: str, model: str,
    duration_ms: float, status: str, error: str | None = None, streamed: bool = False,
    input_tokens: int | None = None, output_tokens: int | None = None,
    request: Any = None, response: Any = None,
) -> None:
    # A call nobody attributed to a trace (with_trace() never called on
    # that Model instance) still gets recorded -- just as a one-span
    # trace of its own, tagged "untraced", so nothing silently goes
    # missing if some future call site forgets to attribute it.
    get_store().insert_call(
        trace_id=trace_id or new_trace_id(), span_id=new_trace_id(), component=component or "untraced",
        role=role, provider=provider, model=model, started_at=_iso(time.time() - duration_ms / 1000), duration_ms=duration_ms,
        status=status, error=error, streamed=streamed, input_tokens=input_tokens, output_tokens=output_tokens,
        meta=None, request=request, response=response,
    )


@contextmanager
def record_span(
    *, trace_id: str | None, component: str | None, role: str, provider: str, model: str, streamed: bool = False,
) -> Iterator[dict]:
    """Times a single call and records it on exit, success or failure.
    Yields a dict the caller fills in:
      request        -- the messages sent (set this before making the
                         call, so it's still there if the call raises)
      response       -- the text/tool_calls that came back (success only)
      input_tokens / output_tokens -- if the provider reported them
    """
    result: dict[str, Any] = {}
    started = time.monotonic()
    try:
        yield result
    except Exception as e:
        record_call(
            trace_id=trace_id, component=component, role=role, provider=provider, model=model, streamed=streamed,
            duration_ms=(time.monotonic() - started) * 1000, status="error", error=str(e),
            request=result.get("request"),
        )
        raise
    else:
        record_call(
            trace_id=trace_id, component=component, role=role, provider=provider, model=model, streamed=streamed,
            duration_ms=(time.monotonic() - started) * 1000, status="ok",
            input_tokens=result.get("input_tokens"), output_tokens=result.get("output_tokens"),
            request=result.get("request"), response=result.get("response"),
        )


# ---------- turns and their spans (Escanor) ----------

def record_event(trace_id: str | None, kind: str, name: str, duration_ms: float = 0.0, status: str = "ok",
                 detail: dict | None = None, started: float | None = None) -> None:
    """Record a span that already happened. `started` is a time.time();
    by default it ended just now. Never raises: tracing must not break a turn."""
    if not trace_id:
        return
    try:
        begin = started if started is not None else time.time() - duration_ms / 1000
        get_store().insert_span(trace_id=trace_id, kind=kind, name=name, started_at=_iso(begin),
                                duration_ms=duration_ms, status=status, detail=detail)
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def span(trace_id: str | None, kind: str, name: str) -> Iterator[dict]:
    """Time a block as a span; fill the yielded dict with detail (and
    "status" to override "ok"). An exception marks it "error" and re-raises."""
    detail: dict[str, Any] = {}
    started, clock = time.time(), time.monotonic()
    status = "ok"
    try:
        yield detail
    except BaseException as e:
        status = "cancelled" if type(e).__name__ == "CancelledError" else "error"
        detail.setdefault("error", str(e)[:200])
        raise
    finally:
        record_event(trace_id, kind, name, (time.monotonic() - clock) * 1000, detail.pop("status", status),
                     detail or None, started)


_exporters: list = []   # callables(turn: dict); see export.py
# First sounds heard before their turn's receipt exists (the usual case:
# Escanor starts speaking while the turn is still running).
_first_audio: dict[str, float] = {}


def record_turn(turn: dict) -> None:
    """Write a turn's receipt, then hand it to any exporter. Never raises."""
    early = _first_audio.pop(turn["trace_id"], None)
    if early is not None and turn.get("first_audio_ms") is None:
        turn = {**turn, "first_audio_ms": early}
    try:
        get_store().insert_turn(turn)
    except Exception:  # noqa: BLE001
        return
    for exporter in list(_exporters):
        try:
            exporter(turn)
        except Exception:  # noqa: BLE001
            pass


def record_first_audio(trace_id: str | None, ms: float) -> None:
    if trace_id:
        try:
            if not get_store().set_first_audio(trace_id, ms):
                _first_audio.setdefault(trace_id, ms)
                if len(_first_audio) > 200:          # turns that never wrote a receipt: don't grow forever
                    _first_audio.pop(next(iter(_first_audio)))
        except Exception:  # noqa: BLE001
            pass

