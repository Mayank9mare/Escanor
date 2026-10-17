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

- **Core**: one agent loop. Every capability is a tool; skills are
  loaded as on-demand instructions (the SKILL.md body is injected when
  the model calls `use_skill(name)`), so the system prompt stays small
  and Haiku stays cheap.
- **Brain** (`~/.escanor/brain/`): plain Markdown notes (kb-style
  folders: people, projects, ideas, journal, research) + a local
  embedding index (SQLite + sqlite-vec) for semantic search, + a
  `facts.md` of durable facts about you that's always in context.
  Tools: `brain_search`, `brain_read`, `brain_write`, `remember_fact`.
- **DJ**: tools `music_play/pause/next`, `music_queue_add`,
  `music_search`, `music_now_playing` (th-ch API server on
  localhost), plus `music_taste` (ytmusicapi: history, likes). DJ
  mode: model picks the next tracks for a mood, writes a 1–2 sentence
  intro, Escanor ducks the volume, speaks it via TTS, resumes.
- **Research explainer**: `use_skill("learn-anything")` + web fetch;
  explanations and quiz state saved to `brain/research/`.

## Phases

1. **Scaffold** — copy `models/`, `voice/`, `config.py`, telemetry
   deps from kmare-code; rename package to `escanor`; Haiku default;
   `/model` switch command. Text CLI working end to end.
2. **Agent core + tools** — tool-calling loop with streaming
   (kmare's `chat_stream` has no tool support — add it), tool
   registry, skill loader.
3. **Brain** — vault, indexer, search/write tools, facts memory.
4. **Voice** — mount kmare-voice UI on the core; then sentence-level
   streaming TTS (big latency win); then VAD + barge-in; wake word last.
5. **DJ** — th-ch API server client, ytmusicapi taste, DJ mode with
   volume ducking.
6. **Assistant** — reminders/calendar (macOS EventKit or Google),
   web search (port kmare's `web_search.py`), Mac actions with
   confirmation for anything side-effectful.
7. **Research tutor** — learn-anything integration, spaced-review
   reminders driven by its learning log.

## Setup you'll need to do

- `ANTHROPIC_API_KEY` (plus OpenAI/Gemini keys if you'll switch to them).
- Install th-ch/youtube-music, sign in with your Premium account,
  enable the **API Server** plugin.
- One-time `ytmusicapi` browser auth (cookie headers; re-run when
  they expire).
- Ollama, if you want offline models.

## Known risks

- `ytmusicapi` is unofficial and its auth cookies expire.
- The th-ch app is unofficial; check YouTube's ToS comfort level.
- Haiku is weaker at long multi-step tool chains — the planner can
  escalate hard turns to Sonnet automatically.
