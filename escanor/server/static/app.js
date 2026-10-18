// Escanor HUD: one WebSocket per tab (protocol in server/app.py) + an animated core.
"use strict";

const $ = (id) => document.getElementById(id);
const log = $("log"), input = $("input"), mic = $("mic"), status = $("status"), caption = $("caption");
const params = new URLSearchParams(location.search);
let ws, current = null, conversation = params.get("conversation") || "latest";
let player = null, playerAnalyser = null;

// ---------- state: idle | listening | thinking | speaking ----------

const LABELS = { idle: "STANDING BY", listening: "LISTENING", thinking: "PROCESSING", speaking: "RESPONDING" };
let state = "idle", serverSpeaking = false;

function setState(next) {
  state = next;
  document.body.dataset.state = next;
  $("state-label").textContent = LABELS[next];
}

function settle() {
  // After a turn: speaking while the voice plays, otherwise idle.
  if (recorder) return;
  setState(serverSpeaking || (player && !player.paused) ? "speaking" : "idle");
}

// ---------- connection ----------

function connect() {
  const query = new URLSearchParams({
    conversation,
    speech: params.get("speech") || "local",
    speak_typed: $("speak-typed").checked ? "1" : "0",
  });
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?${query}`);
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => { setStatus("CONNECTION LOST · RETRYING"); setTimeout(connect, 1500); };
}

function send(message) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
}

function setStatus(text) { status.textContent = text || ""; }

function add(kind, text) {
  const div = document.createElement("div");
  div.className = kind.startsWith("tool") ? kind : `msg ${kind}`;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function op(text, failed = false) {
  const li = document.createElement("li");
  li.textContent = text;
  if (failed) li.className = "failed";
  const ops = $("ops");
  ops.prepend(li);
  while (ops.children.length > 6) ops.lastChild.remove();
}

// Live caption: the sentence Escanor is currently saying/writing.
let captionText = "";
function showCaption(text) {
  captionText = text;
  const sentences = text.trim().split(/(?<=[.!?])\s+/);
  caption.textContent = sentences.slice(-2).join(" ").slice(-260);
}

function handle(m) {
  switch (m.type) {
    case "hello":
      conversation = String(m.conversation);
      $("conversation-id").textContent = `#${m.conversation}`;
      log.replaceChildren();
      m.history.forEach((h) => add(h.role, h.text));
      selectModel(m.model);
      setStatus(m.speech === "off" ? "VOICE OUTPUT OFF" : "");
      setState("idle");
      break;
    case "user":
      add("user", m.text);
      current = null;
      showCaption("");
      setState("thinking");
      setStatus("");
      break;
    case "transcribing": setState("thinking"); setStatus("TRANSCRIBING"); break;
    case "text":
      if (!current) current = add("assistant", "");
      current.textContent += m.text;
      log.scrollTop = log.scrollHeight;
      showCaption(captionText + m.text);
      break;
    case "tool_started":
      add("tool", `› ${m.name} ${JSON.stringify(m.arguments)}`);
      op(`${m.name.toUpperCase()} ${JSON.stringify(m.arguments).slice(0, 60)}`);
      current = null;
      break;
    case "tool_finished": if (!m.ok) { add("tool failed", `✗ ${m.name}: ${m.output}`); op(`${m.name.toUpperCase()} FAILED`, true); } break;
    case "confirm_request": confirm(m); break;
    case "done":
      current = null;
      setStatus(m.stop_reason === "interrupted" ? "INTERRUPTED" : "");
      settle();
      break;
    case "error": add("error", m.message); current = null; setStatus(""); settle(); break;
    case "model_switched": selectModel(m.model); op(`MODEL → ${m.model}`); break;
    case "speaking":
      serverSpeaking = m.on;
      if (m.on) setState("speaking"); else if (state === "speaking") settle();
      break;
    case "audio": playAudio(m.data); break;
    case "stop_audio": stopAudio(); break;
  }
}

function confirm(m) {
  const dialog = $("confirm");
  $("confirm-text").textContent = `Escanor wants to: ${m.summary}`;
  const answer = (approved) => { send({ type: "confirm", id: m.id, approved }); dialog.close(); };
  $("allow").onclick = () => answer(true);
  $("deny").onclick = () => answer(false);
  dialog.oncancel = () => answer(false);
  dialog.showModal();
}

// ---------- models & controls ----------

async function loadModels() {
  const data = await (await fetch("/api/models")).json();
  const select = $("model");
  select.replaceChildren();
  for (const [alias, spec] of Object.entries(data.aliases)) {
    const option = new Option(`${alias} · ${spec}`, alias);
    option.dataset.spec = spec;
    select.add(option);
  }
  selectModel(data.current);
  select.onchange = () => send({ type: "model", name: select.value });
}

function selectModel(spec) {
  for (const option of $("model").options) if (option.dataset.spec === spec) option.selected = true;
}

$("composer").onsubmit = (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  stopAudio();
  send({ type: "text", text });
  input.value = "";
};
$("new").onclick = () => { conversation = "new"; ws.onclose = null; ws.close(); connect(); };
$("speak-typed").onchange = () => { ws.onclose = null; ws.close(); connect(); };
$("toggle-log").onclick = (e) => {
  const hidden = document.body.classList.toggle("log-hidden");
  e.currentTarget.setAttribute("aria-pressed", String(!hidden));
  try { localStorage.setItem("escanor.logHidden", hidden ? "1" : "0"); } catch (_) {}
};
try { if (localStorage.getItem("escanor.logHidden") === "1") document.body.classList.add("log-hidden"); } catch (_) {}

// ---------- voice in: hold to talk, 16-bit mono WAV ----------

let recorder = null;

async function startRecording() {
  if (recorder) return;
  stopAudio();
  send({ type: "interrupt" });
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
    const context = new AudioContext();
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    const node = context.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    node.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    source.connect(node);
    node.connect(context.destination);
    recorder = { stream, context, node, chunks, analyser };
    mic.classList.add("recording");
    showCaption("");
    setState("listening");
    setStatus("RELEASE TO SEND");
  } catch (err) {
    setStatus(`MICROPHONE UNAVAILABLE: ${err.message}`);
  }
}

function stopRecording() {
  if (!recorder) return;
  const { stream, context, node, chunks } = recorder;
  recorder = null;
  mic.classList.remove("recording");
  node.disconnect();
  stream.getTracks().forEach((t) => t.stop());
  const rate = context.sampleRate;
  context.close();
  const length = chunks.reduce((n, c) => n + c.length, 0);
  if (length < rate * 0.3) { setStatus("TOO SHORT · HOLD WHILE YOU SPEAK"); settle(); return; }
  setStatus("");
  setState("thinking");
  send({ type: "audio", data: encodeWav(chunks, length, rate) });
}

function encodeWav(chunks, length, rate) {
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const text = (offset, s) => [...s].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
  view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) for (const sample of chunk) {
    const s = Math.max(-1, Math.min(1, sample));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

mic.addEventListener("pointerdown", startRecording);
mic.addEventListener("pointerup", stopRecording);
mic.addEventListener("pointerleave", stopRecording);
document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && document.activeElement !== input && !e.repeat) { e.preventDefault(); startRecording(); }
});
document.addEventListener("keyup", (e) => {
  if (e.code === "Space" && document.activeElement !== input) { e.preventDefault(); stopRecording(); }
});

// ---------- voice out in the browser (speech=browser) ----------

function playAudio(base64) {
  stopAudio();
  player = new Audio(`data:audio/wav;base64,${base64}`);
  try {
    const context = new AudioContext();
    playerAnalyser = context.createAnalyser();
    playerAnalyser.fftSize = 256;
    const source = context.createMediaElementSource(player);
    source.connect(playerAnalyser);
    playerAnalyser.connect(context.destination);
  } catch (_) { playerAnalyser = null; }
  player.onended = () => { playerAnalyser = null; settle(); };
  setState("speaking");
  player.play().catch(() => setStatus("CLICK ANYWHERE TO ALLOW AUDIO"));
}

function stopAudio() {
  if (player) { player.pause(); player = null; playerAnalyser = null; }
}

// ---------- the core ----------

const canvas = $("core"), ctx = canvas.getContext("2d");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const PALETTE = {
  idle: [79, 209, 255], listening: [79, 209, 255], thinking: [155, 140, 255], speaking: [255, 181, 71],
};
const bins = new Uint8Array(128);
let colour = [...PALETTE.idle], level = 0, spin = 0, sweep = 0, last = performance.now();
const bars = new Float32Array(72);

function amplitude(t) {
  // Real audio when we have it (mic, or browser playback); otherwise a voice-like envelope.
  const analyser = state === "listening" && recorder ? recorder.analyser : (state === "speaking" ? playerAnalyser : null);
  if (analyser) {
    analyser.getByteFrequencyData(bins);
    let sum = 0;
    for (let i = 2; i < 64; i++) sum += bins[i];
    return Math.min(1, sum / (62 * 160));
  }
  if (state === "speaking") return 0.35 + 0.3 * Math.abs(Math.sin(t * 7.3) * Math.sin(t * 3.1 + 1)) + 0.15 * Math.random();
  if (state === "thinking") return 0.18;
  return 0.06 + 0.04 * Math.sin(t * 1.4);
}

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const size = canvas.getBoundingClientRect().width;
  canvas.width = canvas.height = Math.round(size * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener("resize", resize);

function rgba(alpha) { return `rgba(${colour[0] | 0}, ${colour[1] | 0}, ${colour[2] | 0}, ${alpha})`; }

function arc(r, start, length, width, alpha) {
  ctx.beginPath();
  ctx.arc(0, 0, r, start, start + length);
  ctx.lineWidth = width;
  ctx.strokeStyle = rgba(alpha);
  ctx.stroke();
}

function draw(now) {
  const dt = Math.min(0.05, (now - last) / 1000);
  last = now;
  const t = now / 1000;
  const size = canvas.getBoundingClientRect().width;
  const R = size / 2;
  const target = PALETTE[state];
  colour = colour.map((c, i) => c + (target[i] - c) * Math.min(1, dt * 4));
  level += (amplitude(t) - level) * Math.min(1, dt * (state === "speaking" ? 14 : 8));
  const speed = reduceMotion ? 0.15 : { idle: 0.25, listening: 0.6, thinking: 2.4, speaking: 0.9 }[state];
  spin += dt * speed;
  sweep += dt * (state === "thinking" ? 3.2 : 0.8) * (reduceMotion ? 0.2 : 1);

  ctx.clearRect(0, 0, size, size);
  ctx.save();
  ctx.translate(R, R);

  // core glow
  const coreR = R * (0.2 + 0.07 * level);
  const glow = ctx.createRadialGradient(0, 0, 0, 0, 0, coreR * 2.4);
  glow.addColorStop(0, rgba(0.95));
  glow.addColorStop(0.25, rgba(0.55));
  glow.addColorStop(0.6, rgba(0.12));
  glow.addColorStop(1, rgba(0));
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(0, 0, coreR * 2.4, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(0, 0, coreR * 0.55, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(235, 250, 255, 0.92)";
  ctx.fill();

  // inner segmented ring (rotates)
  for (let i = 0; i < 12; i++) arc(R * 0.36, spin + i * (Math.PI / 6), Math.PI / 9, 3, 0.55);

  // radial audio bars
  for (let i = 0; i < bars.length; i++) {
    const angle = (i / bars.length) * Math.PI * 2 - Math.PI / 2;
    const wobble = 0.5 + 0.5 * Math.sin(t * 6 + i * 0.7) * Math.sin(t * 2.3 + i * 1.3);
    const targetLen = R * (0.02 + level * (0.12 + 0.12 * wobble));
    bars[i] += (targetLen - bars[i]) * Math.min(1, dt * 12);
    const r0 = R * 0.46;
    ctx.beginPath();
    ctx.moveTo(Math.cos(angle) * r0, Math.sin(angle) * r0);
    ctx.lineTo(Math.cos(angle) * (r0 + bars[i]), Math.sin(angle) * (r0 + bars[i]));
    ctx.lineWidth = 2;
    ctx.strokeStyle = rgba(0.75);
    ctx.stroke();
  }

  // tick ring with a sweeping highlight
  const ticks = 120, r1 = R * 0.74;
  for (let i = 0; i < ticks; i++) {
    const angle = (i / ticks) * Math.PI * 2;
    const distance = Math.abs(((angle - (sweep % (Math.PI * 2)) + Math.PI * 3) % (Math.PI * 2)) - Math.PI);
    const lit = Math.max(0, 1 - distance / 0.9);
    const long = i % 10 === 0;
    ctx.beginPath();
    ctx.moveTo(Math.cos(angle) * r1, Math.sin(angle) * r1);
    ctx.lineTo(Math.cos(angle) * (r1 - (long ? 10 : 5)), Math.sin(angle) * (r1 - (long ? 10 : 5)));
    ctx.lineWidth = long ? 2 : 1;
    ctx.strokeStyle = rgba(0.18 + 0.75 * lit);
    ctx.stroke();
  }

  // outer counter-rotating arcs
  arc(R * 0.86, -spin * 0.6, Math.PI * 0.55, 2, 0.6);
  arc(R * 0.86, -spin * 0.6 + Math.PI, Math.PI * 0.35, 2, 0.45);
  arc(R * 0.93, spin * 0.35 + 1, Math.PI * 0.2, 1, 0.35);
  arc(R * 0.93, spin * 0.35 + 3.5, Math.PI * 0.6, 1, 0.25);
  // thin full rings
  arc(R * 0.6, 0, Math.PI * 2, 1, 0.12);
  arc(R * 0.97, 0, Math.PI * 2, 1, 0.08);

  ctx.restore();
  requestAnimationFrame(draw);
}

function tickClock() {
  $("clock").textContent = new Date().toLocaleString(undefined, {
    weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).toUpperCase();
}

resize();
requestAnimationFrame(draw);
tickClock();
setInterval(tickClock, 1000);
loadModels();
connect();
