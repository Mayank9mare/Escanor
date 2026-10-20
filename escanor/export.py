"""
export.py

Optional: send each turn's trace to an observability tool as
OpenTelemetry (OTLP over HTTP, JSON) -- Langfuse, Jaeger, Grafana Tempo,
Honeycomb, anything that takes OTLP. Off by default: traces stay in
~/.escanor/telemetry.db.

    [telemetry]
    export = "otlp"
    otlp_endpoint = "http://localhost:4318/v1/traces"      # e.g. a local Jaeger / OTel collector
    # Langfuse: "https://cloud.langfuse.com/api/public/otel/v1/traces" with
    # otlp_headers = { Authorization = "env:LANGFUSE_OTLP_AUTH" }   ("Basic <base64 pk:sk>")
    include_text = false     # true also sends what you said and the tool arguments

One turn becomes one trace: a root "turn" span, with a child span for
every model call (GenAI semantic conventions: gen_ai.system,
gen_ai.request.model, gen_ai.usage.*), tool, failover, compaction and
speech-to-text. Sentences spoken after the receipt is written aren't
included (their timings stay local). Sending happens on a background
thread; a slow or failing endpoint never delays or breaks a turn.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import threading
import urllib.request
from datetime import datetime
from . import telemetry

log = logging.getLogger(__name__)


def _ns(iso: str, plus_ms: float = 0.0) -> str:
    return str(int((datetime.fromisoformat(iso).timestamp() + plus_ms / 1000) * 1e9))


def _hex(text: str, length: int) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:length]


def _attrs(values: dict) -> list[dict]:
    out = []
    for key, value in values.items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            out.append({"key": key, "value": {"boolValue": value}})
        elif isinstance(value, int):
            out.append({"key": key, "value": {"intValue": str(value)}})
        elif isinstance(value, float):
            out.append({"key": key, "value": {"doubleValue": value}})
        else:
            out.append({"key": key, "value": {"stringValue": str(value)}})
    return out


def to_otlp(turn: dict, timeline: list[dict], include_text: bool = False) -> dict:
    """One turn as an OTLP/JSON ExportTraceServiceRequest."""
    trace_id = _hex(turn["trace_id"], 32)
    root_id = _hex(turn["trace_id"] + ":turn", 16)
    root = {
        "traceId": trace_id, "spanId": root_id, "name": f"turn ({turn['source']})", "kind": 2,
        "startTimeUnixNano": _ns(turn["started_at"]),
        "endTimeUnixNano": _ns(turn["started_at"], turn.get("duration_ms") or 0),
        "status": {"code": 2 if turn.get("outcome") == "error" else 1},
        "attributes": _attrs({
            "escanor.trace_id": turn["trace_id"], "escanor.source": turn["source"],
            "escanor.conversation_id": turn.get("conversation_id"), "gen_ai.request.model": turn.get("model"),
            "escanor.ttft_ms": turn.get("ttft_ms"), "escanor.outcome": turn.get("outcome"),
            "gen_ai.usage.input_tokens": turn.get("input_tokens"), "gen_ai.usage.output_tokens": turn.get("output_tokens"),
            "escanor.cost_usd": turn.get("usd"), "escanor.error": turn.get("error"),
            **({"input.value": turn.get("text")} if include_text else {}),
        }),
    }
    spans = [root]
    for i, item in enumerate(timeline):
        detail = item.get("detail") or {}
        if item["kind"] == "model":
            provider, _, model = item["name"].partition("/")
            attributes = {"gen_ai.system": provider, "gen_ai.request.model": model, "gen_ai.operation.name": "chat",
                          "gen_ai.usage.input_tokens": detail.get("in"), "gen_ai.usage.output_tokens": detail.get("out"),
                          "escanor.tool_requests": ", ".join(detail.get("asked_for") or []) or None,
                          "escanor.error": detail.get("error")}
            name = f"chat {model}"
        else:
            attributes = {"escanor.kind": item["kind"], "escanor.chars": detail.get("chars"),
                          "escanor.reason": detail.get("reason"),
                          "escanor.tokens_before": detail.get("tokens_before"),
                          "escanor.tokens_after": detail.get("tokens_after"),
                          **({"escanor.arguments": detail.get("arguments"), "escanor.output": detail.get("output")}
                             if include_text else {})}
            name = f"{item['kind']} {item['name']}"
        spans.append({
            "traceId": trace_id, "spanId": _hex(f"{turn['trace_id']}:{i}", 16), "parentSpanId": root_id,
            "name": name, "kind": 3 if item["kind"] == "model" else 1,
            "startTimeUnixNano": _ns(item["started_at"]), "endTimeUnixNano": _ns(item["started_at"], item["duration_ms"]),
            "status": {"code": 1 if item["status"] in ("ok", "declined") else 2},
            "attributes": _attrs(attributes),
        })
    return {"resourceSpans": [{
        "resource": {"attributes": _attrs({"service.name": "escanor"})},
        "scopeSpans": [{"scope": {"name": "escanor"}, "spans": spans}],
    }]}


def _headers(raw: dict | None) -> dict:
    out = {"Content-Type": "application/json"}
    for key, value in (raw or {}).items():
        value = str(value)
        out[key] = os.environ.get(value[4:], "") if value.startswith("env:") else value
    return out


class OTLPExporter:
    def __init__(self, endpoint: str, headers: dict | None = None, include_text: bool = False,
                 post=None, background: bool = True):
        self.endpoint = endpoint
        self.headers = _headers(headers)
        self.include_text = include_text
        self._post = post or self._http_post
        self._background = background

    def __call__(self, turn: dict) -> None:
        body = to_otlp(turn, telemetry.get_store().timeline(turn["trace_id"]), self.include_text)
        if self._background:
            threading.Thread(target=self._send, args=(body,), daemon=True).start()
        else:
            self._send(body)

    def _send(self, body: dict) -> None:
        try:
            self._post(self.endpoint, json.dumps(body).encode(), self.headers)
        except Exception as e:  # noqa: BLE001 -- an exporter must never matter to a turn
            log.warning("OTLP export to %s failed: %s", self.endpoint, e)

    @staticmethod
    def _http_post(url: str, data: bytes, headers: dict) -> None:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method="POST"), timeout=5):
            pass


def configure(settings: dict) -> None:
    """Register the exporter [telemetry] asks for (called once at start-up)."""
    options = settings.get("telemetry", {})
    if options.get("export") != "otlp" or not options.get("otlp_endpoint"):
        return
    if any(isinstance(e, OTLPExporter) for e in telemetry._exporters):
        return
    telemetry._exporters.append(OTLPExporter(options["otlp_endpoint"], options.get("otlp_headers"),
                                             bool(options.get("include_text", False))))
