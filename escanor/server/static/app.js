// Escanor HUD: one WebSocket per tab (protocol in server/app.py) + a living sun.
// Like the Lion's Sin of Pride, its power follows the real sun: rising through the
// morning, "THE ONE" at high noon, a humble ember after dusk.
"use strict";

const $ = (id) => document.getElementById(id);
const log = $("log"), input = $("input"), mic = $("mic"), status = $("status"), caption = $("caption");
const params = new URLSearchParams(location.search);
let ws, current = null, conversation = params.get("conversation") || "latest";
let player = null, playerAnalyser = null;

// ---------- state: idle | listening | thinking | speaking ----------

const LABELS = { listening: "LISTENING", thinking: "DELIBERATING", speaking: "PROCLAIMING" };
const IDLE_LABEL = { day: "BASKING IN THE SUN", noon: "THE ONE", night: "RESTING" };
let state = "idle", serverSpeaking = false;

function setState(next) {
  state = next;
  document.body.dataset.state = next;
  $("state-label").textContent = next === "idle" ? IDLE_LABEL[sun.phase] : LABELS[next];
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

// ---------- the sun ----------

const canvas = $("core"), ctx = canvas.getContext("2d");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const PALETTE = {
  idle: [255, 176, 0], listening: [255, 205, 90], thinking: [255, 72, 40], speaking: [255, 120, 20],
  night: [205, 140, 90],
};
const bins = new Uint8Array(128);
const RAYS = 96;
const rays = new Float32Array(RAYS);
let colour = [...PALETTE.idle], level = 0, spin = 0, sweep = 0, last = performance.now();
const sun = { phase: "day", power: 0.6 };

function solar(now = new Date()) {
  // 0..1 power from the time of day: rises from 06:00, peaks at 12:00, sets at 18:00.
  const hours = now.getHours() + now.getMinutes() / 60;
  if (hours >= 11.5 && hours < 12.5) return { phase: "noon", power: 1 };
  if (hours < 6 || hours >= 18) return { phase: "night", power: 0.08 };
  return { phase: "day", power: 0.15 + 0.85 * Math.sin(((hours - 6) / 12) * Math.PI) };
}

function updateSun() {
  Object.assign(sun, solar());
  document.body.classList.toggle("night", sun.phase === "night");
  document.body.classList.toggle("the-one", sun.phase === "noon");
  $("power-name").textContent = sun.phase === "noon" ? "THE ONE" : sun.phase === "night" ? "SUN HAS SET" : "SOLAR POWER";
  $("power-value").textContent = sun.phase === "noon" ? "MAX" : `${Math.round(sun.power * 100)}%`;
  $("power-fill").style.width = `${Math.round(sun.power * 100)}%`;
  if (state === "idle") $("state-label").textContent = IDLE_LABEL[sun.phase];
}

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
  if (state === "thinking") return 0.22;
  return 0.06 + 0.04 * Math.sin(t * 1.2);
}

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const size = canvas.getBoundingClientRect().width;
  canvas.width = canvas.height = Math.round(size * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener("resize", resize);

function rgba(alpha, c = colour) { return `rgba(${c[0] | 0}, ${c[1] | 0}, ${c[2] | 0}, ${alpha})`; }

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
  const night = sun.phase === "night";
  const target = state === "idle" && night ? PALETTE.night : PALETTE[state];
  colour = colour.map((c, i) => c + (target[i] - c) * Math.min(1, dt * 3));
  level += (amplitude(t) - level) * Math.min(1, dt * (state === "speaking" ? 14 : 8));
  const power = state === "idle" ? sun.power : Math.max(sun.power, 0.55);  // he rises to the occasion
  const motion = reduceMotion ? 0.2 : 1;
  spin += dt * motion * { idle: 0.08, listening: 0.2, thinking: 1.6, speaking: 0.35 }[state];
  sweep += dt * motion * (state === "thinking" ? 3 : 0.6);

  ctx.clearRect(0, 0, size, size);
  ctx.save();
  ctx.translate(R, R);

  // corona: flickering flame rays, longer with solar power and voice
  const base = R * (0.17 + 0.07 * power + 0.05 * level);
  for (let i = 0; i < RAYS; i++) {
    const angle = (i / RAYS) * Math.PI * 2 + spin;
    const flicker = 0.55 + 0.45 * Math.sin(t * (2.1 + (i % 7) * 0.37) + i * 2.3) * Math.sin(t * 1.3 + i * 0.9);
    const targetLen = R * (0.03 + power * 0.16 * flicker + level * 0.22 * (0.4 + flicker));
    rays[i] += (targetLen - rays[i]) * Math.min(1, dt * 10);
    const r0 = base * 1.05, r1 = r0 + rays[i] * (i % 2 ? 0.65 : 1);
    const gradient = ctx.createLinearGradient(Math.cos(angle) * r0, Math.sin(angle) * r0, Math.cos(angle) * r1, Math.sin(angle) * r1);
    gradient.addColorStop(0, rgba(0.9 * (0.4 + power * 0.6)));
    gradient.addColorStop(1, rgba(0));
    ctx.beginPath();
    ctx.moveTo(Math.cos(angle) * r0, Math.sin(angle) * r0);
    ctx.lineTo(Math.cos(angle) * r1, Math.sin(angle) * r1);
    ctx.lineWidth = i % 2 ? 1.5 : 3;
    ctx.lineCap = "round";
    ctx.strokeStyle = gradient;
    ctx.stroke();
  }

  // solar flares while proclaiming
  if (state === "speaking" && !reduceMotion) {
    for (let k = 0; k < 3; k++) {
      const a = t * 0.7 + k * 2.1;
      const reach = R * (0.45 + 0.12 * Math.sin(t * 3 + k) + level * 0.15);
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * base, Math.sin(a) * base);
      ctx.quadraticCurveTo(Math.cos(a + 0.35) * reach, Math.sin(a + 0.35) * reach, Math.cos(a + 0.7) * base, Math.sin(a + 0.7) * base);
      ctx.lineWidth = 2;
      ctx.strokeStyle = rgba(0.35 + level * 0.4);
      ctx.stroke();
    }
  }

  // the sun itself
  const glowR = base * (2.2 + power);
  const glow = ctx.createRadialGradient(0, 0, 0, 0, 0, glowR);
  glow.addColorStop(0, rgba(0.95));
  glow.addColorStop(0.3, rgba(0.45 + 0.3 * power));
  glow.addColorStop(0.65, rgba(0.1 + 0.1 * power));
  glow.addColorStop(1, rgba(0));
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(0, 0, glowR, 0, Math.PI * 2);
  ctx.fill();
  const disc = ctx.createRadialGradient(-base * 0.25, -base * 0.25, base * 0.1, 0, 0, base);
  disc.addColorStop(0, night && state === "idle" ? "rgba(255, 214, 170, 0.85)" : "rgba(255, 251, 235, 1)");
  disc.addColorStop(0.55, rgba(1, night && state === "idle" ? [230, 150, 90] : [255, 214, 102]));
  disc.addColorStop(1, rgba(0.9));
  ctx.fillStyle = disc;
  ctx.beginPath();
  ctx.arc(0, 0, base, 0, Math.PI * 2);
  ctx.fill();

  // sundial ring: hour marks, with a sweep while deliberating
  const ticks = 72, r1 = R * 0.78;
  for (let i = 0; i < ticks; i++) {
    const angle = (i / ticks) * Math.PI * 2;
    const distance = Math.abs(((angle - (sweep % (Math.PI * 2)) + Math.PI * 3) % (Math.PI * 2)) - Math.PI);
    const lit = Math.max(0, 1 - distance / 0.8);
    const long = i % 6 === 0;
    ctx.beginPath();
    ctx.moveTo(Math.cos(angle) * r1, Math.sin(angle) * r1);
    ctx.lineTo(Math.cos(angle) * (r1 - (long ? 12 : 5)), Math.sin(angle) * (r1 - (long ? 12 : 5)));
    ctx.lineWidth = long ? 2 : 1;
    ctx.strokeStyle = rgba(0.15 + 0.7 * lit);
    ctx.stroke();
  }
  // the sun's place on the dial right now (noon at the top)
  const d = new Date();
  // midnight at the bottom, sunrise left, noon top, sunset right (canvas angles run clockwise)
  const dayAngle = ((d.getHours() + d.getMinutes() / 60) / 24) * Math.PI * 2 - Math.PI * 1.5;
  ctx.beginPath();
  ctx.arc(Math.cos(dayAngle) * r1, Math.sin(dayAngle) * r1, 4, 0, Math.PI * 2);
  ctx.fillStyle = rgba(0.95);
  ctx.fill();

  // outer arcs, turning slowly the other way
  arc(R * 0.88, -spin * 0.8, Math.PI * 0.5, 2, 0.5);
  arc(R * 0.88, -spin * 0.8 + Math.PI, Math.PI * 0.3, 2, 0.35);
  arc(R * 0.95, spin * 0.5 + 1, Math.PI * 0.25, 1, 0.3);
  arc(R * 0.95, spin * 0.5 + 3.6, Math.PI * 0.55, 1, 0.22);
  arc(R * 0.62, 0, Math.PI * 2, 1, 0.1);

  ctx.restore();
  requestAnimationFrame(draw);
}

function tickClock() {
  $("clock").textContent = new Date().toLocaleString(undefined, {
    weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).toUpperCase();
  updateSun();
}

resize();
requestAnimationFrame(draw);
tickClock();
setInterval(tickClock, 1000);
loadModels();
connect();
