# Escanor — implementation plan

Companion to [PLAN.md](PLAN.md) (the *what* and *why*). This file is the
*how*: package layout, interfaces, and a phased task list where every
phase ends in something you can run and a test that proves it.

Conventions: Python ≥3.11, `uv` for env + deps, `pytest`, FastAPI for
the server. Every external dependency (LLM, STT, TTS, YouTube Music,
embeddings) sits behind an interface with a **mock** implementation, so
the full test suite runs offline with no keys.

---

## 1. Target layout

```
escanor/
  __main__.py            `python -m escanor` → CLI chat (Phase 1), `--serve` → server (Phase 4)
  cli.py                 REPL: /model, /new, /skills, /brain, /quit
  config.py              env vars + ~/.escanor/config.toml (default model, aliases, paths)
  telemetry.py           copied from kmare-code (token/latency per call)

  models/                COPIED from kmare_code/models
    base.py              Model, ModelResponse, ToolCall  (+ StreamEvent, chat_stream_tools)
    registry.py          get_model(provider, name)        (+ resolve_alias("sonnet"))
    anthropic_provider.py openai_provider.py ollama_provider.py mock_provider.py instrumented.py

  core/
    agent.py             Agent.run_turn(session, user_text) -> Iterator[Event]
    events.py            TextDelta | ToolStarted | ToolFinished | ModelSwitched | TurnDone | Error
    session.py           conversation persistence (SQLite, ~/.escanor/escanor.db)
    prompts.py           system prompt = persona + facts.md + skill index + tool guidance
    router.py            optional escalation: Haiku → Sonnet for hard turns

  tools/
    base.py registry.py  COPIED from kmare_code/tools (Tool, ToolResult, ToolRegistry)
    permissions.py       side_effect levels: read | write | external → confirm before external
    system_tools.py      switch_model, get_time, enable_tools (set_timer: Phase 6)

  skills/
    loader.py            scans skill dirs, parses SKILL.md frontmatter
    tools.py             use_skill(name), read_skill_file(name, path)

  brain/
    vault.py             Markdown vault at ~/.escanor/brain/ (people/ projects/ ideas/ journal/ research/ facts.md)
    embeddings.py        Embedder interface: Ollama nomic-embed-text | mock
    index.py             heading-aware chunking → SQLite (FTS5 + sqlite-vec), incremental by mtime+hash
    tools.py             brain_search, brain_read, brain_write, brain_append, remember_fact, forget_fact

  music/
    player.py            YTMusicPlayer: client for th-ch API Server (localhost REST)
    library.py           ytmusicapi wrapper: history, liked, search, recommendations (watch playlist)
    dj.py                DJ mode: pick tracks, write patter, duck → speak → restore
    tools.py             music_play, music_pause, music_next, music_now_playing, music_queue, music_search, dj_start, dj_stop

  assistant/
    web.py               web_search (ported kmare chatbox/web_search.py), web_fetch (HTML → text)
    reminders.py         macOS Reminders via AppleScript (osascript)
    calendar.py          macOS Calendar via AppleScript (read; create = external → confirm)
    mac.py               open_app, open_url, notify, now_playing volume etc.

  voice/                 COPIED from kmare_code/voice
    stt/ tts/ speech.py  STTEngine / TTSEngine + faster-whisper, Piper, Sarvam, mock
    chunker.py           sentence splitter for streaming TTS
    audio_out.py         single speaker owner: priority queue, music auto-duck (Phase 4a)
    vad.py               Silero VAD wrapper (Phase 4b)
    audio_loop.py        native mic/speaker loop via sounddevice: wake word → listen → reply (Phase 4c)
    wakeword.py          openWakeWord ("hey escanor" custom model or "hey jarvis" stock)

  server/
    app.py               FastAPI: REST + one WebSocket per session streaming Events and audio
    static/              UI adapted from kmare voice/static (chat + mic + now-playing card)

tests/                   mirrors package; fakes in tests/fakes/ (fake th-ch server, fake model scripts)
```

Data lives in `~/.escanor/` (gitignored by design): `escanor.db`,
`brain/`, `index.db`, `ytmusic_auth.json`, `voices/`, `logs/`.

---

## 2. Key interfaces

### 2.1 Streaming with tools (the one real gap in the copied code)

kmare's `Model.chat_stream(messages)` is text-only. Voice needs the
first sentence spoken while the model is still generating, *and* tool
calls in the same turn. Add to `models/base.py`:

```python
@dataclass
class StreamEvent:
    kind: Literal["text", "done"]
    text: str = ""
    response: ModelResponse | None = None   # set on "done": full text + tool_calls + usage

class Model(ABC):
    def chat_stream_tools(self, messages, tools=None) -> Iterator[StreamEvent]:
        # default fallback: one non-streaming call
        r = self.chat(messages, tools)
        if r.text: yield StreamEvent("text", r.text)
        yield StreamEvent("done", response=r)
```

Provider overrides:
- **Anthropic**: `client.messages.stream(..., tools=...)`; yield `text` deltas; on
  finish build ModelResponse from `get_final_message()`. Keep kmare's prompt-caching
  breakpoints (system + second-to-last message).
- **OpenAI / Gemini** (same file): `stream=True`; accumulate `delta.tool_calls[i]`
  fragments by index; preserve Gemini `thought_signature` into `provider_extra`.
- **Ollama**: `/api/chat` with `stream: true` + tools; keep kmare's fallback parser
  for models that emit tool calls as JSON text.

### 2.2 Agent loop

The core is **async** (kmare-code is fully sync; that can't support
confirmations mid-turn, barge-in cancellation, or a DJ running while
you talk). Providers keep their sync SDK calls but run in a worker
thread, bridged to an async iterator; switch to native async clients
later only if profiling says so.

```python
class Confirmer(Protocol):              # one per interface: CLI y/n, WS round-trip, voice "yes"
    async def confirm(self, call: ToolCall, summary: str) -> bool: ...

class Agent:
    def __init__(self, model: Model, tools: ToolRegistry, prompts: PromptBuilder, gate: PermissionGate): ...
    async def run_turn(self, session: Session, user_text: str, confirmer: Confirmer) -> AsyncIterator[Event]:
        session.append_user(user_text)
        for step in range(MAX_STEPS):            # MAX_STEPS = 8
            resp = None
            async for ev in astream(self.model.chat_stream_tools, prompts.build(session), tools.schemas()):
                if ev.kind == "text": yield TextDelta(ev.text)
                else: resp = ev.response
            session.append_assistant(resp)
            if not resp.tool_calls: yield TurnDone(usage); return
            for call in resp.tool_calls:
                yield ToolStarted(call)
                result = await gate.dispatch(call, confirmer)   # awaits confirmer for `external`
                session.append_tool_result(call, result)
                yield ToolFinished(call, result)
        yield Error("too many tool steps")
```

Cancellation (barge-in, "stop") = cancel the asyncio task consuming
`run_turn`; the session records the partial reply as interrupted.

Every interface (CLI, web UI, voice loop) is just a consumer of this
event stream. Model switching = replace `agent.model` between turns.
History is provider-neutral, **but tool-call history is not fully
portable**: Gemini rejects replayed tool calls without its
`thought_signature`, and ids differ per provider. On a model switch,
`session.py` collapses completed earlier turns' tool calls into plain
text (`[web_search "x" → 3 results: …]`); only the current turn keeps
native tool-call blocks.

### 2.2a Tool groups (keep the tool list small)

Sending ~30 tool schemas on every call costs tokens on every Haiku turn
and makes small Ollama models pick tools badly. Tools are grouped:
`core` (time, timers, switch_model, use_skill, brain_search,
remember_fact, web_search) is always on; `music`, `assistant`,
`brain_edit`, `research` are enabled by a `enable_tools(group)` meta-tool
(same idea as skills), and stay on for the rest of the session. DJ mode
enables `music` automatically. Target: ≤15 schemas per call.

### 2.3 Model aliases (config.toml)

```toml
[models]
default = "haiku"
[models.aliases]
haiku  = "anthropic:claude-haiku-5-5"
sonnet = "anthropic:claude-sonnet-5-5"
gpt    = "openai:<model id>"
gemini = "gemini:<model id>"
local  = "ollama:qwen3:8b"
```

`/model sonnet`, `/model ollama:llama3.2`, or saying "switch to
sonnet" (→ `switch_model` tool) all go through `registry.resolve_alias`.

### 2.4 Tool side-effect levels

| Level | Examples | Behaviour |
|---|---|---|
| `read` | brain_search, web_search, music_now_playing | run silently |
| `write` | brain_write, remember_fact, music_queue, set_timer | run, announce in event stream |
| `external` | calendar create, anything sending a message, mac shell actions | **ask for confirmation** (CLI y/n, UI button, voice "yes") |

**Untrusted content.** Web pages, fetched papers, imported notes and
YouTube metadata can contain text that tries to instruct the model.
- Tool results from `web_*`, `research_*` and imported vaults are wrapped as
  `<untrusted source="…">…</untrusted>`, and the system prompt says they are data.
- Once a turn has read untrusted content, `write` tools that persist into
  every future prompt (`remember_fact`, `forget_fact`) need confirmation
  in that turn. Otherwise injected text could plant a permanent "fact".
- `external` tools always confirm, whatever the source.

### 2.5 Skills

`skills/loader.py` scans `ESCANOR_SKILL_DIRS` (default:
`~/projects/mayank-awesome-skills`, `~/.escanor/skills`). The system
prompt lists only `name: description` for each skill. When the model
calls `use_skill("learn-anything")`, the SKILL.md body is returned as
the tool result; `read_skill_file("learn-anything", "references/socratic.md")`
lets it pull referenced files on demand. Skills written for Claude Code
refer to paths like `~/.learn/<topic>.md`; the prompt tells Escanor to
map those to `brain/research/learn/` via brain tools. Allow-list skills
in config (start with `learn-anything`, `grill-me`), since most of
that repo is engineering/work tooling. **Don't load `kb`**: it writes
to `~/.claude/knowledgebase/` and would split the second brain in two.
Its category layout (people/projects/processes) is adopted by
`brain/vault.py` instead. Skills name Claude Code tools (Read, Write,
Grep); `prompts.py` carries an explicit map (Read → `brain_read` /
`read_skill_file`, Write → `brain_write`, Grep → `brain_search`).

---

## 3. Phases

Each phase: tasks → done-when → tests. Commit at the end of each phase
(at minimum).

### Phase 0 — Spikes (½ day, throwaway, in `spikes/`)
Prove the three risky external pieces before building on them.
- [ ] th-ch app: install, enable **API Server** plugin. Known from source: port **26538**,
      hostname defaults to **0.0.0.0** (reachable from your whole network) → change it to
      `127.0.0.1` in the plugin settings. Auth strategy `AUTH_AT_FIRST`: `POST /auth/escanor`
      pops an allow/deny dialog in the app once, returns `{accessToken}` (JWT, **no expiry**,
      so store it in `~/.escanor/ytm_token` with 0600 perms). Confirm the header format
      (likely `Authorization: Bearer`), then curl `/api/v1/song`, `/search`, `POST /queue`, `/volume`.
- [ ] ytmusicapi: run browser auth, call `get_history()`, `get_liked_songs()`, `get_watch_playlist(videoId)` (radio-style recommendations).
- [ ] Anthropic streaming with tools on Haiku: stream text + one tool call round-trip.
- [ ] Echo test: play TTS out of the laptop speakers with music on, and measure whether
      Silero VAD / openWakeWord fire on Escanor's own voice. Try (a) the browser's
      `echoCancellation: true`, (b) the macOS voice-processing audio unit (AEC) through a tiny
      Swift helper, (c) headphones. The result picks the Phase 4b/4c design.

**Done when:** a short `spikes/FINDINGS.md` records ports, auth steps, and response shapes.

### Phase 1 — Scaffold + text CLI (1 day) ✅ done 2026-10-17
- [x] `pyproject.toml` (uv), extras: `anthropic`, `openai`, `voice`, `music`, `server`, `dev`.
- [x] Copy `models/`, `tools/base.py`, `tools/registry.py`, `telemetry.py`, `config.py` from kmare-code; rename imports `kmare_code` → `escanor`; trim providers to anthropic/openai/gemini/ollama/mock (drop opencode/sarvam chat unless wanted; keep Sarvam STT/TTS).
- [x] Add `resolve_alias` + `config.toml` loading; default `haiku`.
- [x] Async skeleton: `core/agent.py` with zero tools + `astream` thread bridge, so the CLI is
      built on the real event stream from day one and isn't rewritten in Phase 2.
- [x] `cli.py` REPL consuming `Agent.run_turn` events, `/model`, `/new`, `/quit`.
- [x] `.env.example`, `README.md` quickstart.

**Done when:** `uv run escanor` chats with Haiku; `/model gemini` and `/model local` switch mid-conversation.
**Tests:** alias resolution; registry builds each provider (with fake keys); CLI round-trip on mock provider.

### Phase 2 — Agent core, tools, skills (2 days) ✅ done 2026-10-17
- [x] `StreamEvent` + `chat_stream_tools` for anthropic, openai(+gemini), ollama, mock (mock scripted to emit tool calls).
- [x] `core/events.py`, `core/agent.py`, `core/session.py` (SQLite), `core/prompts.py` (persona: concise, spoken-friendly, British-butler-optional).
- [x] `tools/permissions.py` + levels; `system_tools.py` (`switch_model`, `get_time`, `enable_tools`). `set_timer` moved to Phase 6 (decision D15).
- [x] `assistant/web.py`: port `web_search`, add `web_fetch` (readability-style text extraction, size-capped).
- [x] `skills/loader.py` + `use_skill`, `read_skill_file`; allow-list.
- [x] CLI renders events (tool calls shown dimmed, confirmations as y/n).

**Done when:** "what's the latest on <topic>?" triggers web_search → answer with sources; "teach me how transformers work" loads learn-anything and starts a Socratic session.
**Tests:** agent loop with scripted mock model (0, 1, multi-step tool calls; MAX_STEPS); permission gate blocks `external` without confirm; OpenAI tool-call delta accumulation from recorded chunks; skill loader frontmatter parsing; switching Haiku → Gemini after a tool-using turn sends collapsed history (no foreign tool-call blocks); cancelling a turn mid-stream leaves a consistent session; tool groups keep schemas ≤15.

### Phase 3 — Second brain (2–3 days) ✅ done 2026-10-17
- [x] `brain/vault.py`: create layout, safe paths (no escaping the vault), frontmatter (`tags`, `created`, `updated`).
- [x] `brain/embeddings.py`: Ollama `nomic-embed-text` (port kmare's), mock embedder; if Ollama is down → keyword-only search.
- [x] `brain/index.py`: chunk by heading (~400 tokens, overlap 50), store in `index.db`: FTS5 for keywords,
      embeddings as float32 blobs searched with a numpy brute-force cosine (milliseconds at personal-vault
      scale, <50k chunks; avoids `sqlite-vec`'s loadable-extension requirement, which some macOS Python
      builds disable). Move to sqlite-vec only if the vault outgrows it. Hybrid score (BM25 + cosine, reciprocal-rank fusion); incremental reindex on mtime/hash; `escanor brain reindex`.
- [x] `brain/tools.py`: search (returns path + heading + snippet), read, write, append (journal), `remember_fact`/`forget_fact` editing `facts.md`.
- [x] `prompts.py` injects `facts.md` (capped ~1.5k tokens) every turn; instructs "search the brain before answering personal questions; cite note paths".
- [x] Optional import: point at an existing Obsidian vault folder (read-only) via `[brain] extra_dirs` in config.toml.

**Done when:** "remember that I prefer window seats" → in facts.md; next session "which seat do I like on flights?" answers from memory; "what did I write about project X?" cites notes.
**Tests:** chunker on fixture notes; hybrid ranking on a small fixture vault; path-traversal rejection; incremental reindex only touches changed files; `remember_fact` after a `web_fetch` in the same turn requires confirmation.

### Phase 4 — Voice (3–4 days, three sub-steps) ✅ done 2026-10-17 (live mic/wake word: [YOU])
**4a. Port kmare-voice onto the agent**
- [x] Copy `voice/stt`, `voice/tts`, `speech.py`; `server/app.py` with WebSocket: client sends WAV (push-to-talk), server streams Events as JSON + audio chunks.
- [x] `voice/audio_out.py`: **one** audio output owner, a priority queue (confirmation prompts >
      replies > timers > DJ patter), with no two utterances overlapping. It auto-ducks YouTube Music
      (via the player volume API) whenever Escanor is listening or speaking, not only in DJ mode,
      and always restores the volume in a `finally`. Speech plays **server-side** (sounddevice),
      so timers and the DJ still talk with the browser tab closed; the browser only streams it when remote.
- [x] UI: chat transcript, tool-activity chips, model picker, mic button, confirm dialog.

**4b. Streaming speech (biggest latency win)**
- [x] `voice/chunker.py`: split TextDeltas into sentences (handle abbreviations, numbers, code/markdown stripped); synthesize + send each sentence as soon as complete.
- [x] Spoken-style prompt variant when input came from voice (no markdown, no URLs read aloud, short answers + "want details?").
- [x] Barge-in: VAD while speaking → stop playback, cancel the turn task. Uses the echo-cancellation approach
      picked by the Phase 0 echo test; without AEC, barge-in is **headphones-only** and the speaker
      path falls back to a push-to-talk hotkey while Escanor speaks (otherwise it interrupts itself).
- [ ] Add Kokoro TTS engine as a higher-quality local option alongside Piper. (Deferred, D25.)

**4c. Hands-free native loop (Jarvis mode)**
- [x] `voice/audio_loop.py` with `sounddevice`: openWakeWord → record until VAD silence → STT → agent → streamed TTS to speakers, barge-in via the same VAD.
      The stock `hey_jarvis` model is known to false-trigger on partial phrases, so require a
      confidence threshold plus a short confirmation chime, and expect to train a custom "hey escanor" model.
- [x] `escanor listen` command; menubar/launchd agent later.

**Done when:** time from end of speech to first audio < ~1.5 s on Haiku + faster-whisper small + Piper; you can interrupt it mid-sentence.
**Tests:** chunker cases; WebSocket protocol with mock STT/TTS/model; cancel aborts generation.

### Phase 5 — AI DJ on YouTube Music (2–3 days) ✅ done 2026-10-17 (live app: [YOU])
- [x] `music/player.py`: typed client for th-ch API Server (play, pause, next, previous, song, queue get/add/clear, search, volume get/set, like). Clear error when the app isn't running ("open YouTube Music desktop").
- [x] `music/library.py`: ytmusicapi (history, liked, search songs, `get_watch_playlist` for radio continuation); cache results 10 min.
- [x] `music/tools.py`: thin tools over both; `music_play("something upbeat for coding")` = model chooses query → search → queue → play.
- [x] `music/dj.py`: background asyncio task polling `/song` every 2 s. ~20 s before a track ends: pick the next 1–3 tracks from history/likes/radio matching the session mood and queue them; pre-generate a ≤25-word intro with a cheap model call (track, artist, one fact from metadata/web, optional time/weather) and **pre-synthesize** it. At the boundary, v1 = **pause → speak → play** (reliable, no talking over vocals); v2 = radio-style volume duck over the intro. Rate-limit patter (every 2–3 tracks, configurable). Patter goes through `audio_out` at the lowest priority; a conversation always wins.
- [x] Spend guard: count DJ model calls per hour and enforce a daily cap from config, since DJ mode runs unattended (the general cost dashboard stays in Phase 8).
- [x] Voice commands: "Escanor, DJ mode, chill evening vibe", "skip", "who is this?", "more like this", "stop the DJ".

**Done when:** a 30-minute DJ session runs unattended with sensible picks and intros that don't talk over vocals at full volume.
**Tests:** fake th-ch server (FastAPI in tests/fakes) for player + DJ state machine; ducking restores volume even when TTS fails.

### Phase 6 — Personal assistant (2 days) ✅ done 2026-10-17 (live Apple permissions: [YOU])
- [x] `assistant/apple.py` (reminders + calendar) via AppleScript (read = `read`, create/modify = `external`).
- [x] Mac actions in `assistant/apple.py`: open app/url, notification, clipboard read.
- [x] `set_timer` (moved from Phase 2, D15) + timers/alarms spoken through TTS; daily briefing command ("good morning": calendar, reminders, weather via web, top unread brain journal prompt).
- [ ] Optional later: Gmail/Google Calendar via OAuth, behind the same `external` confirmation.

**Done when:** "remind me to call mom at 7" creates a reminder after confirmation; "what's on today?" reads the calendar.
**Tests:** AppleScript builders produce correct scripts (subprocess mocked); permissions enforced.

### Phase 7 — Research explainer (1–2 days)
- [ ] `research` flow: given a URL/PDF/arXiv id → `web_fetch` / PDF text extraction (pypdf) → saved to `brain/research/<slug>/source.md` → `use_skill("learn-anything")` drives the explanation.
- [ ] Learning log stored at `brain/research/learn/<topic>.md` (learn-anything's template), so it is also searchable second-brain content.
- [ ] Spaced review: on startup / morning briefing, list topics whose review date in the log is due; "quiz me" resumes.

**Done when:** "explain this paper <arXiv link>" → plain-language walkthrough with checks for understanding, and a log appears in the brain.
**Tests:** source ingestion on fixture HTML/PDF; due-review detection from fixture logs.

### Phase 8 — Polish (ongoing)
- Router: escalate to Sonnet when a turn exceeds N tool steps or the user says "think hard"; show the switch in the UI.
- Cost/latency dashboard from telemetry.db; daily spend cap per provider.
- launchd service + menubar icon; backup of `~/.escanor/brain` to git.
- Evals: a small scripted set of prompts per capability run against each model alias.

---

## 4. Order and estimates

| Phase | Effort | Depends on |
|---|---|---|
| 0 Spikes | 1 d | — |
| 1 Scaffold + async CLI | 1–2 d | — |
| 2 Agent + tools + skills | 2–3 d | 1 |
| 3 Brain | 2–3 d | 2 |
| 4 Voice | 5–7 d (4c depends on the echo spike) | 2, 0 |
| 5 DJ | 2–3 d | 2, 0 (4 for spoken patter) |
| 6 Assistant | 2 d | 2 |
| 7 Research | 1–2 d | 2, 3 |

Phases 3–6 are independent once Phase 2 lands; suggested order 3 → 4 → 5 → 6 → 7
(brain and voice make everything after them feel like Escanor).

## 5. Open questions (decide when the phase arrives)

1. Persona: name it Escanor in replies, and a voice/personality style (dry butler, friendly, terse)?
2. Default local TTS voice: Piper `en_US-lessac`, Kokoro, or Sarvam Bulbul (cloud, Indian English)?
3. Wake word: stock "hey jarvis" (openWakeWord ships it) or train "hey escanor"?
4. Existing Obsidian/Notion notes to import into the brain?
5. Google Calendar/Gmail vs. macOS Calendar/Reminders only?
