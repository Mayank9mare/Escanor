// Escanor HUD: one WebSocket per tab (protocol in server/app.py) + a living sun.
// Like the Lion's Sin of Pride, its power follows the real sun: rising through the
// morning, "THE ONE" at high noon, a humble ember after dusk.
//
// Layers: #sun is a WebGL shader (plasma surface + corona); #core is a 2D overlay
// (sundial, arcs, flares, embers). Without WebGL the overlay draws a simpler sun.
"use strict";

const $ = (id) => document.getElementById(id);
const log = $("log"), input = $("input"), mic = $("mic"), status = $("status"), caption = $("caption");
const params = new URLSearchParams(location.search);
let ws, current = null, conversation = params.get("conversation") || "latest";
let player = null, playerAnalyser = null;
const store = {
  get(key) { try { return localStorage.getItem(key); } catch (_) { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch (_) {} },
};
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------- state: idle | listening | thinking | speaking ----------

const LABELS = { listening: "LISTENING", thinking: "DELIBERATING", speaking: "PROCLAIMING" };
const IDLE_LABEL = { day: "BASKING IN THE SUN", noon: "THE ONE", night: "RESTING" };
const sun = { phase: "day", power: 0.6 };
let state = "idle", serverSpeaking = false;

function setState(next) {
  if (next !== state) cue(next);
  state = next;
  document.body.dataset.state = next;
  $("state-label").textContent = next === "idle" ? IDLE_LABEL[sun.phase] : LABELS[next];
}

function settle() {
  if (recorder) return;
  setState(serverSpeaking || (player && !player.paused) ? "speaking" : "idle");
}

// ---------- rich text (safe: everything is escaped first) ----------

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function inline(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
}

function renderMarkdown(raw) {
  const out = [];
  const parts = escapeHtml(raw).split(/```/);
  parts.forEach((part, i) => {
    if (i % 2 === 1) { out.push(`<pre><code>${part.replace(/^[a-z0-9]*\n/, "")}</code></pre>`); return; }
    let list = null;
    for (const line of part.split("\n")) {
      const bullet = line.match(/^\s*[-*•]\s+(.*)/), numbered = line.match(/^\s*\d+[.)]\s+(.*)/);
      const item = bullet || numbered;
      if (item) {
        const kind = bullet ? "ul" : "ol";
        if (list !== kind) { if (list) out.push(`</${list}>`); out.push(`<${kind}>`); list = kind; }
        out.push(`<li>${inline(item[1])}</li>`);
        continue;
      }
      if (list) { out.push(`</${list}>`); list = null; }
      const heading = line.match(/^\s*#{1,6}\s+(.*)/);
      if (heading) out.push(`<h3>${inline(heading[1])}</h3>`);
      else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
    }
    if (list) out.push(`</${list}>`);
  });
  return out.join("");
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
  if (kind === "assistant") { div.dataset.raw = text; div.innerHTML = renderMarkdown(text); }
  else div.textContent = text;
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

let captionText = "";
function showCaption(text) {
  captionText = text;
  const plain = text
    .replace(/```[\s\S]*?(```|$)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "link")
    .replace(/^\s*([-*•]|\d+[.)])\s+/gm, "")
    .replace(/[*_`#>]/g, "")
    .replace(/\n+/g, ". ")
    .replace(/:\./g, ":");
  caption.textContent = plain.trim().split(/(?<=[.!?])\s+/).slice(-2).join(" ").slice(-260);
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
      current.dataset.raw += m.text;
      current.innerHTML = renderMarkdown(current.dataset.raw);
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
      if (!m.on) envelope = null;
      if (m.on) setState("speaking"); else if (state === "speaking") settle();
      break;
    case "envelope": envelope = { start: performance.now(), step: m.step * 1000, values: m.values }; break;
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

function interrupt() {
  stopAudio();
  send({ type: "interrupt" });
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

function toggleTranscript() {
  const hidden = document.body.classList.toggle("log-hidden");
  $("toggle-log").setAttribute("aria-pressed", String(!hidden));
  store.set("escanor.logHidden", hidden ? "1" : "0");
  requestAnimationFrame(resize);
}
$("toggle-log").onclick = toggleTranscript;
if (store.get("escanor.logHidden") === "1") document.body.classList.add("log-hidden");

let soundOn = store.get("escanor.sound") !== "0";
function renderSoundButton() { $("sound").setAttribute("aria-pressed", String(soundOn)); $("sound").textContent = soundOn ? "Sound" : "Muted"; }
$("sound").onclick = () => { soundOn = !soundOn; store.set("escanor.sound", soundOn ? "1" : "0"); renderSoundButton(); };
renderSoundButton();

// ---------- keyboard ----------

document.addEventListener("keydown", (e) => {
  const typing = document.activeElement === input;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); input.focus(); return; }
  if (e.key === "Escape" && !$("confirm").open) { if (typing) input.blur(); interrupt(); setStatus("INTERRUPTED"); return; }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.code === "Space" && !e.repeat) { e.preventDefault(); startRecording(); }
  else if (e.key.toLowerCase() === "t") toggleTranscript();
});
document.addEventListener("keyup", (e) => {
  if (e.code === "Space" && document.activeElement !== input) { e.preventDefault(); stopRecording(); }
});

// ---------- sound cues (synthesised; nothing to download) ----------

let cueContext = null;
function cue(next) {
  if (!soundOn || reduceMotion) return;
  const notes = { listening: [523, 784], speaking: [392, 523] }[next];
  if (!notes) return;
  try {
    cueContext = cueContext || new AudioContext();
    const t0 = cueContext.currentTime;
    notes.forEach((freq, i) => {
      const osc = cueContext.createOscillator(), gain = cueContext.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, t0 + i * 0.07);
      gain.gain.linearRampToValueAtTime(0.05, t0 + i * 0.07 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + i * 0.07 + 0.35);
      osc.connect(gain).connect(cueContext.destination);
      osc.start(t0 + i * 0.07);
      osc.stop(t0 + i * 0.07 + 0.4);
    });
  } catch (_) { /* audio not allowed yet */ }
}

// ---------- voice in: hold to talk, 16-bit mono WAV ----------

let recorder = null;

async function startRecording() {
  if (recorder) return;
  interrupt();
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

// ---------- loudness: mic, browser audio, or the server's envelope ----------

const bins = new Uint8Array(128);
let envelope = null;  // {start, step (ms), values} for speech playing on the Mac

function amplitude(t) {
  const analyser = state === "listening" && recorder ? recorder.analyser : (state === "speaking" ? playerAnalyser : null);
  if (analyser) {
    analyser.getByteFrequencyData(bins);
    let sum = 0;
    for (let i = 2; i < 64; i++) sum += bins[i];
    return Math.min(1, sum / (62 * 160));
  }
  if (state === "speaking" && envelope) {
    const position = (performance.now() - envelope.start) / envelope.step;
    const i = Math.floor(position);
    if (i < envelope.values.length - 1) {
      const frac = position - i;
      return envelope.values[i] * (1 - frac) + envelope.values[i + 1] * frac;
    }
    return 0.05;  // between sentences
  }
  if (state === "speaking") return 0.35 + 0.3 * Math.abs(Math.sin(t * 7.3) * Math.sin(t * 3.1 + 1)) + 0.15 * Math.random();
  if (state === "thinking") return 0.22;
  return 0.06 + 0.04 * Math.sin(t * 1.2);
}

// ---------- the sun: WebGL shader ----------

const SUN_FRAGMENT = `
precision highp float;
uniform vec2 u_res;
uniform float u_time, u_power, u_level, u_radius, u_think;
uniform vec3 u_color;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 5; i++) { v += a * noise(p); p = p * 2.03 + vec2(1.7, 9.2); a *= 0.5; }
  return v;
}

void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * u_res) / (0.5 * min(u_res.x, u_res.y));
  float r = length(p);
  vec2 dir = r > 0.0 ? p / r : vec2(0.0);  // direction, not angle: no seam where atan wraps
  float t = u_time * (0.12 + 0.5 * u_think);
  float R = u_radius;
  vec3 hot = vec3(1.0, 0.97, 0.88);
  vec3 col = vec3(0.0);
  float alpha = 0.0;

  if (r < R) {
    // the disc: churning plasma with limb darkening
    vec2 q = p / R;
    float limb = sqrt(max(0.0, 1.0 - dot(q, q)));
    vec2 warp = vec2(fbm(q * 3.0 + t), fbm(q * 3.0 - t + 4.0));
    float plasma = fbm(q * 4.0 + warp * 1.6 + vec2(t * 0.6, -t * 0.4));
    float cells = smoothstep(0.35, 0.85, plasma);
    vec3 deep = u_color * vec3(0.95, 0.62, 0.3);
    col = mix(deep, mix(u_color, hot, 0.55), cells);
    col = mix(col, hot, pow(limb, 2.0) * (0.45 + 0.4 * u_power));
    col *= 0.8 + 0.3 * limb + 0.25 * u_level;
    alpha = 1.0 - smoothstep(R - 0.012, R, r);  // smoothstep needs edge0 < edge1
  }
  // corona: flame tongues that lengthen with power and voice
  float edge = max(r - R, 0.0);
  float tongues = fbm(dir * 2.6 + vec2(edge * 5.0 - t * 1.8, t * 0.4));
  float reach = 0.10 + 0.32 * u_power + 0.28 * u_level;
  float flame = (1.0 - smoothstep(0.0, reach * (0.35 + tongues), edge)) * (0.45 + 0.55 * tongues);
  float glow = exp(-edge * (7.0 - 3.0 * u_power)) * (0.35 + 0.35 * u_power);
  float halo = flame * 0.9 + glow;
  vec3 coronaColor = mix(u_color, hot, 0.25 * flame);
  float outside = smoothstep(R - 0.02, R + 0.005, r);  // blend the rim into the corona, no dark outline
  col = mix(col, coronaColor, outside);
  alpha = max(alpha, outside * clamp(halo, 0.0, 1.0));
  gl_FragColor = vec4(col * alpha, alpha);
}`;

function createSunGL(canvas) {
  const gl = canvas.getContext("webgl", { premultipliedAlpha: true, alpha: true, antialias: true });
  if (!gl) return null;
  const compile = (type, source) => {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
    return shader;
  };
  try {
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, "attribute vec2 a; void main(){ gl_Position = vec4(a, 0.0, 1.0); }"));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, SUN_FRAGMENT));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, "a");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    const u = {};
    for (const name of ["u_res", "u_time", "u_power", "u_level", "u_radius", "u_think", "u_color"]) u[name] = gl.getUniformLocation(program, name);
    return {
      draw({ time, power, level, radius, think, color }) {
        gl.viewport(0, 0, canvas.width, canvas.height);
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.uniform2f(u.u_res, canvas.width, canvas.height);
        gl.uniform1f(u.u_time, time);
        gl.uniform1f(u.u_power, power);
        gl.uniform1f(u.u_level, level);
        gl.uniform1f(u.u_radius, radius);
        gl.uniform1f(u.u_think, think);
        gl.uniform3f(u.u_color, color[0] / 255, color[1] / 255, color[2] / 255);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      },
    };
  } catch (err) {
    console.warn("WebGL sun unavailable, using 2D:", err.message);
    return null;
  }
}

// ---------- the overlay: sundial, arcs, flares, embers ----------

const glCanvas = $("sun"), canvas = $("core"), ctx = canvas.getContext("2d");
const sunGL = createSunGL(glCanvas);
const PALETTE = {
  idle: [255, 176, 0], listening: [255, 205, 90], thinking: [255, 72, 40], speaking: [255, 120, 20],
  night: [205, 140, 90],
};
const RAYS = 96, rays = new Float32Array(RAYS);
const embers = [];
let colour = [...PALETTE.idle], level = 0, spin = 0, sweep = 0, think = 0, last = performance.now();

function solar(now = new Date()) {
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

function resize() {
  const size = canvas.getBoundingClientRect().width;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = canvas.height = Math.round(size * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const glScale = Math.min(window.devicePixelRatio || 1, 1.5);  // the shader is the expensive layer
  glCanvas.width = glCanvas.height = Math.round(size * glScale);
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

function drawFallbackSun(R, base, power, t, dt) {
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
  const glow = ctx.createRadialGradient(0, 0, 0, 0, 0, base * (2.2 + power));
  glow.addColorStop(0, rgba(0.95));
  glow.addColorStop(0.3, rgba(0.45 + 0.3 * power));
  glow.addColorStop(1, rgba(0));
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(0, 0, base * (2.2 + power), 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(0, 0, base, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(255, 245, 225, 0.95)";
  ctx.fill();
}

function updateEmbers(R, base, power, dt) {
  if (!reduceMotion) {
    const rate = (sun.phase === "night" && state === "idle" ? 0.4 : 2 + power * 10) + level * 30;
    let spawn = rate * dt;
    while (spawn > 0 && embers.length < 160) {
      if (Math.random() < spawn) {
        const a = Math.random() * Math.PI * 2, speed = R * (0.05 + Math.random() * 0.12) * (1 + level);
        embers.push({ x: Math.cos(a) * base, y: Math.sin(a) * base, vx: Math.cos(a) * speed, vy: Math.sin(a) * speed - R * 0.02,
                      life: 1, decay: 0.25 + Math.random() * 0.45, size: 0.6 + Math.random() * 1.6 });
      }
      spawn -= 1;
    }
  }
  ctx.globalCompositeOperation = "lighter";
  for (let i = embers.length - 1; i >= 0; i--) {
    const e = embers[i];
    e.life -= e.decay * dt;
    if (e.life <= 0) { embers.splice(i, 1); continue; }
    e.vy -= R * 0.03 * dt;  // embers drift upward
    e.x += e.vx * dt; e.y += e.vy * dt;
    ctx.beginPath();
    ctx.arc(e.x, e.y, e.size, 0, Math.PI * 2);
    ctx.fillStyle = rgba(e.life * 0.85);
    ctx.fill();
  }
  ctx.globalCompositeOperation = "source-over";
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
  level += (amplitude(t) - level) * Math.min(1, dt * (state === "speaking" ? 16 : 8));
  const power = state === "idle" ? sun.power : Math.max(sun.power, 0.55);  // he rises to the occasion
  const motion = reduceMotion ? 0.2 : 1;
  spin += dt * motion * { idle: 0.08, listening: 0.2, thinking: 1.6, speaking: 0.35 }[state];
  sweep += dt * motion * (state === "thinking" ? 3 : 0.6);
  think += ((state === "thinking" ? 1 : 0) - think) * Math.min(1, dt * 3);
  const radiusFraction = 0.17 + 0.07 * power + 0.05 * level;
  const base = R * radiusFraction;

  if (sunGL) sunGL.draw({ time: t * motion, power, level, radius: radiusFraction, think, color: colour });

  ctx.clearRect(0, 0, size, size);
  ctx.save();
  ctx.translate(R, R);
  if (!sunGL) drawFallbackSun(R, base, power, t, dt);
  updateEmbers(R, base, power, dt);

  if (state === "speaking" && !reduceMotion) {  // solar flares while proclaiming
    for (let k = 0; k < 3; k++) {
      const a = t * 0.7 + k * 2.1;
      const reach = R * (0.45 + 0.12 * Math.sin(t * 3 + k) + level * 0.18);
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * base, Math.sin(a) * base);
      ctx.quadraticCurveTo(Math.cos(a + 0.35) * reach, Math.sin(a + 0.35) * reach, Math.cos(a + 0.7) * base, Math.sin(a + 0.7) * base);
      ctx.lineWidth = 2;
      ctx.strokeStyle = rgba(0.3 + level * 0.45);
      ctx.stroke();
    }
  }

  // sundial: hour marks, a sweep while deliberating, and the sun's place right now
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
  const d = new Date();
  // midnight at the bottom, sunrise left, noon top, sunset right (canvas angles run clockwise)
  const dayAngle = ((d.getHours() + d.getMinutes() / 60) / 24) * Math.PI * 2 - Math.PI * 1.5;
  ctx.beginPath();
  ctx.arc(Math.cos(dayAngle) * r1, Math.sin(dayAngle) * r1, 4, 0, Math.PI * 2);
  ctx.fillStyle = rgba(0.95);
  ctx.shadowColor = rgba(0.9);
  ctx.shadowBlur = 12;
  ctx.fill();
  ctx.shadowBlur = 0;

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
