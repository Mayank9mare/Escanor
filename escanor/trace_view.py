"""
trace_view.py

`escanor trace`: recent turns, and one turn as a timeline -- where the
seconds went. The HUD's Traces page (/traces) shows the same data.

    escanor trace              the last 20 turns
    escanor trace 3f2a91       one turn (any unique prefix of its id)
    escanor trace --slow       only turns that took over 10 s
"""
from __future__ import annotations
from datetime import datetime
from . import telemetry

SLOW_SPAN_MS = 5000


def seconds(ms: float | None) -> str:
    if ms is None:
        return "—"
    return f"{ms / 1000:.1f}s" if ms >= 100 else f"{ms:.0f}ms"


def tokens(n: int | None) -> str:
    if not n:
        return "—"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def dollars(usd: float | None) -> str:
    return "?" if usd is None else ("$0" if usd == 0 else f"${usd:.4f}")


def local_time(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%d %b %H:%M:%S")


def format_turns(turns: list[dict]) -> str:
    if not turns:
        return "No turns recorded yet. Talk to Escanor, then try again."
    lines = [f"{'id':<8} {'when':<15} {'from':<8} {'total':>6} {'1st word':>8} {'1st sound':>9} "
             f"{'steps':>5} {'tools':>5} {'tokens':>6} {'cost':>7}  {'ended':<11} said"]
    for t in turns:
        said = " ".join((t.get("text") or "").split())
        lines.append(
            f"{t['trace_id'][:8]:<8} {local_time(t['started_at']):<15} {t['source'][:8]:<8} "
            f"{seconds(t['duration_ms']):>6} {seconds(t['ttft_ms']):>8} {seconds(t['first_audio_ms']):>9} "
            f"{t['steps'] or 0:>5} {t['tools'] or 0:>5} {tokens((t['input_tokens'] or 0) + (t['output_tokens'] or 0)):>6} "
            f"{dollars(t['usd']):>7}  {(t['outcome'] or '')[:11]:<11} {said[:50]}{'…' if len(said) > 50 else ''}")
    return "\n".join(lines)


def _detail(item: dict) -> str:
    d = item.get("detail") or {}
    if item["kind"] == "model":
        parts = []
        if d.get("in") or d.get("out"):
            parts.append(f"in {tokens(d.get('in'))} out {tokens(d.get('out'))}")
        if d.get("asked_for"):
            parts.append("→ " + ", ".join(d["asked_for"]))
        if d.get("error"):
            parts.append(d["error"][:90])
        return " · ".join(parts)
    if item["kind"] == "tool":
        return (d.get("arguments") or "")[:60] + (f" · {d['output'][:60]}" if d.get("output") else "")
    if item["kind"] == "compaction":
        return f"{tokens(d.get('tokens_before'))} → {tokens(d.get('tokens_after'))} tokens"
    if item["kind"] == "failover":
        return (d.get("reason") or "")[:90]
    if item["kind"] in ("tts", "stt"):
        return f"{d.get('chars', '?')} chars"
    return ""


def format_timeline(turn: dict, items: list[dict]) -> str:
    start = datetime.fromisoformat(turn["started_at"])
    head = (f"Turn {turn['trace_id']} · {turn['source']} · {local_time(turn['started_at'])} · {turn['model']} · "
            f"{seconds(turn['duration_ms'])} · {turn['outcome']}")
    lines = [head, f"  “{' '.join((turn.get('text') or '').split())[:120]}”", ""]
    for item in items:
        offset = (datetime.fromisoformat(item["started_at"]) - start).total_seconds() * 1000
        slow = "  ← slow" if item["duration_ms"] >= SLOW_SPAN_MS else ""
        status = "" if item["status"] == "ok" else f" [{item['status']}]"
        lines.append(f"  +{seconds(max(offset, 0)):>6}  {item['kind']:<10} {item['name'][:44]:<44} "
                     f"{seconds(item['duration_ms']):>7}{status}  {_detail(item)}{slow}")
    if turn.get("error"):
        lines.append(f"\n  error: {turn['error']}")
    lines.append(f"\n  first word {seconds(turn['ttft_ms'])} · first sound {seconds(turn['first_audio_ms'])} · "
                 f"tokens {tokens((turn['input_tokens'] or 0) + (turn['output_tokens'] or 0))} · cost {dollars(turn['usd'])}")
    return "\n".join(lines)


def main(trace_id: str | None = None, limit: int = 20, slow: bool = False) -> int:
    store = telemetry.get_store()
    if trace_id:
        turn = store.turn(trace_id)
        if turn is None:
            print(f"No turn '{trace_id}'. `escanor trace` lists recent ones.")
            return 1
        print(format_timeline(turn, store.timeline(turn["trace_id"])))
        return 0
    turns = store.recent_turns(limit * 5 if slow else limit)
    if slow:
        turns = [t for t in turns if (t["duration_ms"] or 0) > 10_000][:limit]
    print(format_turns(turns))
    return 0
