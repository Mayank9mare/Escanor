// Escanor Traces: recent turns (GET /api/traces) and one turn's timeline
// (GET /api/traces/<id>) as a waterfall. Same data as `escanor trace`.
"use strict";

const $ = (id) => document.getElementById(id);
const KINDS = ["model", "tool", "stt", "tts", "failover", "compaction"];
let selected = null;

const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const secs = (ms) => (ms == null ? "—" : ms >= 100 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`);
const toks = (n) => (!n ? "—" : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
const usd = (v) => (v == null ? "?" : v === 0 ? "$0" : `$${v.toFixed(4)}`);
const when = (iso) => new Date(iso).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });

async function loadTurns() {
  const limit = $("slow").checked ? 100 : 40;
  let turns;
  try { turns = await (await fetch(`/api/traces?limit=${limit}`)).json(); } catch (_) { return; }
  if ($("slow").checked) turns = turns.filter((t) => (t.duration_ms || 0) > 10000);
  const list = $("turns");
  if (!turns.length) { list.innerHTML = '<div class="empty">No turns yet. Talk to Escanor, then come back.</div>'; return; }
  list.innerHTML = turns.map((t) => {
    const cls = t.outcome === "error" ? "bad" : (t.duration_ms || 0) > 10000 ? "slow" : "";
    return `<div class="turn${t.trace_id === selected ? " selected" : ""}" role="listitem" tabindex="0" data-id="${esc(t.trace_id)}">
      <span class="when">${esc(when(t.started_at))}</span><span class="src">${esc(t.source)}</span>
      <span class="total ${cls}">${secs(t.duration_ms)}</span><span class="said" title="${esc(t.text)}">${esc(t.text)}</span></div>`;
  }).join("");
  list.querySelectorAll(".turn").forEach((row) => {
    row.onclick = () => show(row.dataset.id);
    row.onkeydown = (e) => { if (e.key === "Enter") show(row.dataset.id); };
  });
  if (!selected) show(turns[0].trace_id);
}

function info(item) {
  const d = item.detail || {};
  if (item.kind === "model") return [d.in || d.out ? `in ${toks(d.in)} out ${toks(d.out)}` : "", d.asked_for ? `→ ${d.asked_for.join(", ")}` : "", d.error || ""].filter(Boolean).join(" · ");
  if (item.kind === "tool") return [d.arguments, d.output].filter(Boolean).join(" · ");
  if (item.kind === "compaction") return `${toks(d.tokens_before)} → ${toks(d.tokens_after)} tokens`;
  if (item.kind === "failover") return d.reason || "";
  return d.chars ? `${d.chars} chars` : "";
}

async function show(id) {
  selected = id;
  document.querySelectorAll(".turn").forEach((r) => r.classList.toggle("selected", r.dataset.id === id));
  let data;
  try { data = await (await fetch(`/api/traces/${encodeURIComponent(id)}`)).json(); } catch (_) { return; }
  if (!data.turn) { $("detail").innerHTML = '<div class="empty">That turn is gone.</div>'; return; }
  const t = data.turn, start = new Date(t.started_at).getTime();
  const end = Math.max(t.duration_ms || 0, ...data.timeline.map((i) => new Date(i.started_at).getTime() - start + i.duration_ms), 1);
  const rows = data.timeline.map((i) => {
    const offset = Math.max(0, new Date(i.started_at).getTime() - start);
    const left = (offset / end) * 100, width = Math.max((i.duration_ms / end) * 100, 0.3);
    const status = i.status === "ok" ? "" : ` <span class="status">[${esc(i.status)}]</span>`;
    const text = info(i);
    return `<div class="row"><span class="kind">+${secs(offset)} ${esc(i.kind)}</span><span class="name" title="${esc(i.name)}">${esc(i.name)}${status}</span>
      <span class="track"><span class="bar${i.duration_ms >= 5000 ? " slow" : ""}" style="left:${left}%;width:${width}%;background:var(--${KINDS.includes(i.kind) ? i.kind : "accent"})"></span></span>
      <span class="ms">${secs(i.duration_ms)}</span>${text ? `<span class="info" title="${esc(text)}">${esc(text)}</span>` : ""}</div>`;
  }).join("");
  $("detail").innerHTML = `
    <p class="said">“${esc(t.text)}”</p>
    <div class="meta">${esc(t.trace_id)} · ${esc(t.source)} · ${esc(when(t.started_at))} · ${esc(t.model)} · ended ${esc(t.outcome)}</div>
    <div class="stats">
      <div><b>${secs(t.duration_ms)}</b>total</div><div><b>${secs(t.ttft_ms)}</b>first word</div>
      <div><b>${secs(t.first_audio_ms)}</b>first sound</div><div><b>${t.steps ?? 0}</b>model calls</div>
      <div><b>${t.tools ?? 0}</b>tools</div><div><b>${toks((t.input_tokens || 0) + (t.output_tokens || 0))}</b>tokens</div>
      <div><b>${usd(t.usd)}</b>cost</div>
    </div>
    ${t.error ? `<p class="status">${esc(t.error)}</p>` : ""}
    ${rows || '<div class="empty">Nothing recorded inside this turn.</div>'}
    <div class="legend">${KINDS.map((k) => `<span><i style="background:var(--${k})"></i>${k}</span>`).join("")}</div>`;
}

$("slow").onchange = () => { selected = null; loadTurns(); };
loadTurns();
setInterval(loadTurns, 5000);
