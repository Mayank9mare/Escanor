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
    system_tools.py      switch_model, get_time, set_timer

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

```python
class Agent:
    def __init__(self, model: Model, tools: ToolRegistry, prompts: PromptBuilder, gate: PermissionGate): ...
    def run_turn(self, session: Session, user_text: str) -> Iterator[Event]:
        session.append_user(user_text)
        for step in range(MAX_STEPS):            # MAX_STEPS = 8
            for ev in self.model.chat_stream_tools(prompts.build(session), tools.schemas()):
                if ev.kind == "text": yield TextDelta(ev.text)
            resp = ev.response
            session.append_assistant(resp)
            if not resp.tool_calls: yield TurnDone(usage); return
            for call in resp.tool_calls:
                yield ToolStarted(call)
                result = gate.check_and_dispatch(call)   # may yield a ConfirmRequest event
                session.append_tool_result(call, result)
                yield ToolFinished(call, result)
        yield Error("too many tool steps")
```

Every interface (CLI, web UI, voice loop) is just a consumer of this
event stream. Model switching = replace `agent.model` between turns;
the session history is provider-neutral (kmare's `{role, content}` +
tool-call shape), so a conversation can start on Haiku and continue on
Sonnet.

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

### 2.5 Skills

`skills/loader.py` scans `ESCANOR_SKILL_DIRS` (default:
`~/projects/mayank-awesome-skills`, `~/.escanor/skills`). The system
prompt lists only `name: description` for each skill. When the model
calls `use_skill("learn-anything")`, the SKILL.md body is returned as
the tool result; `read_skill_file("learn-anything", "references/socratic.md")`
lets it pull referenced files on demand. Skills written for Claude Code
refer to paths like `~/.learn/<topic>.md`; the prompt tells Escanor to
map those to `brain/research/learn/` via brain tools. Allow-list skills
in config (start with `learn-anything`, `grill-me`, `kb`), since most
of that repo is engineering/work tooling.

---

## 3. Phases

Each phase: tasks → done-when → tests. Commit at the end of each phase
(at minimum).

### Phase 0 — Spikes (½ day, throwaway, in `spikes/`)
Prove the three risky external pieces before building on them.
- [ ] th-ch app: install, enable **API Server** plugin, find port + auth flow
      (`auth.ts` exists — likely a token you approve in-app); curl `/api/v1/song`, `/search`, `POST /queue`, `/volume`.
- [ ] ytmusicapi: run browser auth, call `get_history()`, `get_liked_songs()`, `get_watch_playlist(videoId)` (radio-style recommendations).
- [ ] Anthropic streaming with tools on Haiku: stream text + one tool call round-trip.

**Done when:** a short `spikes/FINDINGS.md` records ports, auth steps, and response shapes.

### Phase 1 — Scaffold + text CLI (1 day)
- [ ] `pyproject.toml` (uv), extras: `anthropic`, `openai`, `voice`, `music`, `server`, `dev`.
- [ ] Copy `models/`, `tools/base.py`, `tools/registry.py`, `telemetry.py`, `config.py` from kmare-code; rename imports `kmare_code` → `escanor`; trim providers to anthropic/openai/gemini/ollama/mock (drop opencode/sarvam chat unless wanted; keep Sarvam STT/TTS).
- [ ] Add `resolve_alias` + `config.toml` loading; default `haiku`.
- [ ] `cli.py` REPL using `chat_stream` (text only for now), `/model`, `/new`, `/quit`.
- [ ] `.env.example`, `README.md` quickstart.

**Done when:** `uv run escanor` chats with Haiku; `/model gemini` and `/model local` switch mid-conversation.
**Tests:** alias resolution; registry builds each provider (with fake keys); CLI round-trip on mock provider.

### Phase 2 — Agent core, tools, skills (2 days)
- [ ] `StreamEvent` + `chat_stream_tools` for anthropic, openai(+gemini), ollama, mock (mock scripted to emit tool calls).
- [ ] `core/events.py`, `core/agent.py`, `core/session.py` (SQLite), `core/prompts.py` (persona: concise, spoken-friendly, British-butler-optional).
- [ ] `tools/permissions.py` + levels; `system_tools.py` (`switch_model`, `get_time`, `set_timer`).
- [ ] `assistant/web.py`: port `web_search`, add `web_fetch` (readability-style text extraction, size-capped).
- [ ] `skills/loader.py` + `use_skill`, `read_skill_file`; allow-list.
- [ ] CLI renders events (tool calls shown dimmed, confirmations as y/n).

**Done when:** "what's the latest on <topic>?" triggers web_search → answer with sources; "teach me how transformers work" loads learn-anything and starts a Socratic session.
**Tests:** agent loop with scripted mock model (0, 1, multi-step tool calls; MAX_STEPS); permission gate blocks `external` without confirm; OpenAI tool-call delta accumulation from recorded chunks; skill loader frontmatter parsing.

### Phase 3 — Second brain (2–3 days)
- [ ] `brain/vault.py`: create layout, safe paths (no escaping the vault), frontmatter (`tags`, `created`, `updated`).
- [ ] `brain/embeddings.py`: Ollama `nomic-embed-text` (port kmare's), mock embedder; if Ollama is down → keyword-only search.
- [ ] `brain/index.py`: chunk by heading (~400 tokens, overlap 50), store in `index.db` with FTS5 + sqlite-vec; hybrid score (BM25 + cosine, reciprocal-rank fusion); incremental reindex on mtime/hash; `escanor brain reindex`.
- [ ] `brain/tools.py`: search (returns path + heading + snippet), read, write, append (journal), `remember_fact`/`forget_fact` editing `facts.md`.
- [ ] `prompts.py` injects `facts.md` (capped ~1.5k tokens) every turn; instructs "search the brain before answering personal questions; cite note paths".
- [ ] Optional import: point at an existing Obsidian vault folder (read-only) via `BRAIN_EXTRA_DIRS`.

**Done when:** "remember that I prefer window seats" → in facts.md; next session "book-wise, where do I like to sit?" answers from memory; "what did I write about project X?" cites notes.
**Tests:** chunker on fixture notes; hybrid ranking on a small fixture vault; path-traversal rejection; incremental reindex only touches changed files.

### Phase 4 — Voice (3–4 days, three sub-steps)
**4a. Port kmare-voice onto the agent**
- [ ] Copy `voice/stt`, `voice/tts`, `speech.py`; `server/app.py` with WebSocket: client sends WAV (push-to-talk), server streams Events as JSON + audio chunks.
- [ ] UI: chat transcript, tool-activity chips, model picker, mic button, confirm dialog.

**4b. Streaming speech (biggest latency win)**
- [ ] `voice/chunker.py`: split TextDeltas into sentences (handle abbreviations, numbers, code/markdown stripped); synthesize + send each sentence as soon as complete.
- [ ] Spoken-style prompt variant when input came from voice (no markdown, no URLs read aloud, short answers + "want details?").
- [ ] Barge-in: client-side VAD (Silero via onnxruntime-web) while playing → stop playback, send `cancel`; server aborts the in-flight stream.
- [ ] Add Kokoro TTS engine as a higher-quality local option alongside Piper.

**4c. Hands-free native loop (Jarvis mode)**
- [ ] `voice/audio_loop.py` with `sounddevice`: openWakeWord → record until VAD silence → STT → agent → streamed TTS to speakers, barge-in via the same VAD.
- [ ] `escanor listen` command; menubar/launchd agent later.

**Done when:** time from end of speech to first audio < ~1.5 s on Haiku + faster-whisper small + Piper; you can interrupt it mid-sentence.
**Tests:** chunker cases; WebSocket protocol with mock STT/TTS/model; cancel aborts generation.

### Phase 5 — AI DJ on YouTube Music (2–3 days)
- [ ] `music/player.py`: typed client for th-ch API Server (play, pause, next, previous, song, queue get/add/clear, search, volume get/set, like). Clear error when the app isn't running ("open YouTube Music desktop").
- [ ] `music/library.py`: ytmusicapi (history, liked, search songs, `get_watch_playlist` for radio continuation); cache results 10 min.
- [ ] `music/tools.py`: thin tools over both; `music_play("something upbeat for coding")` = model chooses query → search → queue → play.
- [ ] `music/dj.py`: background task polling `/song` every 2 s. On track change (or ~15 s before end): pick next 1–3 tracks from history/likes/radio matching the session mood; generate a ≤25-word intro with a cheap model call (track, artist, one fact from metadata/web, optional time/weather); **duck** volume to ~25 %, speak via TTS, restore. Rate-limit patter (every 2–3 tracks, configurable).
- [ ] Voice commands: "Escanor, DJ mode, chill evening vibe", "skip", "who is this?", "more like this", "stop the DJ".

**Done when:** a 30-minute DJ session runs unattended with sensible picks and intros that don't talk over vocals at full volume.
**Tests:** fake th-ch server (FastAPI in tests/fakes) for player + DJ state machine; ducking restores volume even when TTS fails.

### Phase 6 — Personal assistant (2 days)
- [ ] `assistant/reminders.py`, `calendar.py` via AppleScript (read = `read`, create/modify = `external`).
- [ ] `assistant/mac.py`: open app/url, notification, clipboard read.
- [ ] Timers/alarms spoken through TTS; daily briefing command ("good morning": calendar, reminders, weather via web, top unread brain journal prompt).
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
| 0 Spikes | ½ d | — |
| 1 Scaffold + CLI | 1 d | — |
| 2 Agent + tools + skills | 2 d | 1 |
| 3 Brain | 2–3 d | 2 |
| 4 Voice | 3–4 d | 2 |
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
