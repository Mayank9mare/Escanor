// Escanor web UI: one WebSocket per tab (see server/app.py for the protocol).
"use strict";

const $ = (id) => document.getElementById(id);
const log = $("log"), input = $("input"), mic = $("mic"), status = $("status");
const params = new URLSearchParams(location.search);
let ws, current = null, conversation = params.get("conversation") || "latest";
let player = null;

function connect() {
  const query = new URLSearchParams({
    conversation,
    speech: params.get("speech") || "local",
    speak_typed: $("speak-typed").checked ? "1" : "0",
  });
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?${query}`);
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => { setStatus("Disconnected. Reconnecting…"); setTimeout(connect, 1500); };
}

function send(message) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
}

function add(kind, text) {
  const div = document.createElement("div");
  div.className = kind.startsWith("tool") ? kind : `msg ${kind}`;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function setStatus(text) { status.textContent = text || ""; }

function handle(m) {
  switch (m.type) {
    case "hello":
      conversation = String(m.conversation);
      log.replaceChildren();
      m.history.forEach((h) => add(h.role, h.text));
      selectModel(m.model);
      setStatus(m.speech === "off" ? "Voice output off" : "");
      break;
    case "user": add("user", m.text); current = null; setStatus("Thinking…"); break;
    case "transcribing": setStatus("Transcribing…"); break;
    case "text":
      if (!current) current = add("assistant", "");
      current.textContent += m.text;
      log.scrollTop = log.scrollHeight;
      setStatus("");
      break;
    case "tool_started":
      add("tool", `${m.name} ${JSON.stringify(m.arguments)}`);
      current = null;
      break;
    case "tool_finished": if (!m.ok) add("tool failed", `${m.name}: ${m.output}`); break;
    case "confirm_request": confirm(m); break;
    case "done": current = null; setStatus(m.stop_reason === "interrupted" ? "Stopped" : ""); break;
    case "error": add("error", m.message); current = null; setStatus(""); break;
    case "model_switched": selectModel(m.model); add("tool", `switched to ${m.model}`); break;
    case "speaking": document.body.classList.toggle("speaking", m.on); break;
    case "audio": playAudio(m.data); break;
    case "stop_audio": stopAudio(); break;
  }
}

function confirm(m) {
  const dialog = $("confirm");
  $("confirm-text").textContent = `Allow Escanor to run: ${m.summary}?`;
  const answer = (approved) => { send({ type: "confirm", id: m.id, approved }); dialog.close(); };
  $("allow").onclick = () => answer(true);
  $("deny").onclick = () => answer(false);
  dialog.oncancel = () => answer(false);
  dialog.showModal();
}

// ---------- models ----------

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

// ---------- text ----------

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

// ---------- voice in: hold to talk, recorded as 16-bit mono WAV ----------

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
    const node = context.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    node.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    source.connect(node);
    node.connect(context.destination);
    recorder = { stream, context, node, chunks };
    mic.classList.add("recording");
    setStatus("Listening… release to send");
  } catch (err) {
    setStatus(`Microphone unavailable: ${err.message}`);
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
  if (length < rate * 0.3) { setStatus("Too short. Hold the mic while you speak."); return; }
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

// ---------- voice out (speech=browser) ----------

function playAudio(base64) {
  stopAudio();
  player = new Audio(`data:audio/wav;base64,${base64}`);
  player.play().catch(() => setStatus("Click anywhere to allow audio playback."));
}

function stopAudio() {
  if (player) { player.pause(); player = null; }
}

loadModels();
connect();
