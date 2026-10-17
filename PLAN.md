# Escanor — build plan

A local, Jarvis-style personal assistant: second brain, general Q&A,
personal assistant, AI DJ on YouTube Music, and research explainer —
by voice or text.

## Decisions (made 2026-10-17)

| Decision | Choice |
|---|---|
| Reuse of kmare-code | **Copy** `models/` and `voice/` into Escanor and evolve independently |
| Default brain model | **Claude Haiku** (`claude-haiku-5-5`), switchable at runtime |
| Other models | Claude Sonnet (`claude-sonnet-5-5`), OpenAI GPT, Ollama (local), Gemini — all already supported by kmare-code's registry |
| Music | **YouTube Music** (Premium) via the th-ch/youtube-music desktop app's API Server plugin + `ytmusicapi` for taste/history |
| Voice | kmare-voice engines (faster-whisper / Sarvam STT, Piper / Sarvam TTS) |
| Research tutor | `learn-anything` skill from mayank-awesome-skills |

## Architecture

```
            ┌─────────────── interfaces ───────────────┐
            │  voice UI (browser, push-to-talk → VAD)  │
            │  text chat UI  ·  CLI                    │
            └───────────────────┬──────────────────────┘
                                │ text
                     ┌──────────▼──────────┐
                     │   Escanor core      │  agent loop w/ tool calling
                     │   (models/registry) │  model switchable per turn
                     └──────────┬──────────┘
      ┌──────────────┬──────────┼───────────┬────────────────┐
   brain           skills      dj        assistant          web
 notes vault     SKILL.md    YT Music    reminders,       search /
 + index +       loader      control +   calendar,        fetch
 user facts     (learn-     taste +     Mac actions
                 anything)   DJ patter
```

- **Core**: one **async** agent loop that streams events to every
  interface. Async is needed so it can ask for confirmation mid-turn,
  be interrupted (cancel the turn), and let the DJ run while you
  talk. Every capability is a tool; tools come in groups that switch
  on when needed (≤15 sent per call). Skills are loaded as on-demand
  instructions (the SKILL.md body is injected when the model calls
  `use_skill(name)`), so the system prompt stays small and Haiku stays
  cheap.
- **Safety**: tools are `read` / `write` / `external`; `external`
  (calendar create, messages, Mac actions) always asks first. Web
  pages, papers and imported notes are marked untrusted, and in any
  turn that read them `remember_fact` needs confirmation too, so
  injected text can't plant a permanent "fact".
- **Model switching**: history is provider-neutral; on a switch,
  earlier tool calls are collapsed to plain text (Gemini rejects
  foreign tool-call history).
- **Brain** (`~/.escanor/brain/`): plain Markdown notes (folders:
  people, projects, processes, ideas, journal, research) + a local
  index (SQLite FTS5 for keywords + embeddings searched with numpy)
  + a `facts.md` of durable facts about you that's always in context.
  Tools: `brain_search`, `brain_read`, `brain_write`, `remember_fact`.
- **Voice**: one owner of the speaker plays everything (replies,
  timers, DJ intros) in priority order on the Mac itself, so it works
  with no browser open, and turns the music down whenever Escanor
  listens or speaks. Interrupting depends on echo cancellation (tested
  in Phase 0); headphones-only if speakers can't be made to work.
- **DJ**: tools `music_play/pause/next`, `music_queue_add`,
  `music_search`, `music_now_playing` (th-ch API server on
  localhost), plus `music_taste` (ytmusicapi: history, likes). DJ
  mode: shortly before a track ends, the model picks and queues the
  next tracks for the mood and pre-renders a 1–2 sentence intro. v1
  pauses → speaks → plays; radio-style talking over the intro is v2.
  A daily spend cap applies, since it runs unattended.
- **Research explainer**: `use_skill("learn-anything")` + web fetch;
  explanations and quiz state saved to `brain/research/`. The `kb`
  skill is *not* loaded (it writes to its own folder and would split
  the brain); its folder layout is used in the brain instead.

## Phases

Detailed tasks, interfaces and tests: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

0. **Spikes** — YouTube Music app API + login, ytmusicapi, Haiku
   streaming with tools, echo test on speakers.
1. **Scaffold** — copy `models/`, `voice/`, `config.py`, telemetry
   from kmare-code; rename package to `escanor`; Haiku default;
   `/model` switch command; async agent skeleton. Text CLI working end
   to end.
2. **Agent core + tools** — tool-calling loop with streaming
   (kmare's `chat_stream` has no tool support — add it), tool
   registry and groups, permission levels, skill loader, web search.
3. **Brain** — vault, indexer, search/write tools, facts memory.
4. **Voice** — mount kmare-voice on the core with the single audio
   owner; then sentence-level streaming TTS (big latency win); then
   VAD + interrupting; wake word last.
5. **DJ** — th-ch API server client, ytmusicapi taste, DJ mode
   (pause → speak → play), spend cap.
6. **Assistant** — reminders/calendar (macOS EventKit or Google),
   web search (port kmare's `web_search.py`), Mac actions with
   confirmation for anything side-effectful.
7. **Research tutor** — learn-anything integration, spaced-review
   reminders driven by its learning log.

## Setup you'll need to do

- `ANTHROPIC_API_KEY` (plus OpenAI/Gemini keys if you'll switch to them).
- Install th-ch/youtube-music, sign in with your Premium account,
  enable the **API Server** plugin, and set its hostname to
  `127.0.0.1` (the default `0.0.0.0` exposes it to your whole
  network). Approve Escanor's access dialog once.
- One-time `ytmusicapi` browser auth (cookie headers; re-run when
  they expire).
- Ollama, if you want offline models.

## Known risks

- `ytmusicapi` is unofficial and its auth cookies expire.
- The th-ch app is unofficial; check YouTube's ToS comfort level.
- The th-ch API's access tokens never expire — stored with owner-only
  permissions in `~/.escanor/`.
- Escanor may hear its own voice through laptop speakers and
  interrupt itself; the Phase 0 echo test decides whether interrupting
  works on speakers or needs headphones.
- The stock "hey jarvis" wake word false-triggers on partial phrases;
  a custom "hey escanor" model may be needed.
- Haiku is weaker at long multi-step tool chains — the planner can
  escalate hard turns to Sonnet automatically.
