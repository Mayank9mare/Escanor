# Escanor

A local, Jarvis-style personal assistant: second brain, research tutor, AI DJ on YouTube Music,
and voice. See [PLAN.md](PLAN.md) for what it will be and
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for how it's being built.

**Status:** all eight plan phases built (live checks listed in the review guide): chat with switchable models (Haiku, Sonnet, GPT, Gemini, Ollama), web search
and reading, skills, a second brain of Markdown notes, voice (web push-to-talk and hands-free), an AI DJ
on YouTube Music, and Apple Reminders/Calendar, timers and a morning briefing (`escanor brief`).

## Quickstart

```bash
uv venv --python 3.12
uv pip install -e ".[all-models,dev]"
cp .env.example .env   # then add ANTHROPIC_API_KEY
.venv/bin/escanor
```

No key yet? `.venv/bin/escanor --model mock` runs the whole loop with a fake model.

## Voice

```bash
uv pip install -e ".[all-models,voice,server]"
.venv/bin/escanor serve            # http://127.0.0.1:8770 — hold the mic (or Space) to talk
uv pip install -e ".[listen]"
.venv/bin/escanor listen           # hands-free: say "hey Jarvis" (or --enter to press Enter)
```

Speech-to-text is Whisper and text-to-speech is Piper, both local; models already downloaded by
kmare-voice are reused.

## YouTube Music DJ

1. Install the [YouTube Music desktop app](https://github.com/th-ch/youtube-music), sign in, enable
   **Plugins → API Server**, and set its hostname to `127.0.0.1`.
2. `uv pip install -e ".[music]"` then `.venv/bin/escanor music connect` (click Allow in the app; optionally
   paste your browser headers so the DJ knows your history and likes).
3. Say or type "DJ mode, chill evening".

## In the chat

| Command | What it does |
|---|---|
| `/model` | show the current model |
| `/model sonnet` | switch: `haiku`, `sonnet`, `gpt`, `gemini`, `local`, `mock`, or `provider:model-id` |
| `/models` | list aliases |
| `/skills` | list skills Escanor can load (e.g. learn-anything) |
| `/tools` | list tool groups and whether they're on |
| `/new` | start a new conversation |
| `/quit` | exit (Ctrl-D works too) |

Ctrl-C stops a reply mid-way. `escanor --continue` resumes the last conversation.

## Housekeeping

| Command | What it does |
|---|---|
| `escanor brief` | morning briefing, then chat |
| `escanor usage [--days 7]` | model calls, tokens and cost; daily cap in `[budget]` |
| `escanor eval --model haiku` | does each capability pick the right tool? (stubbed tools, real model calls) |
| `escanor brain reindex / backup` | rebuild search index / commit notes to git |
| `escanor service install` | start the web UI at login (launchd) |

Say "think hard" to hand a turn to Sonnet; long tool chains escalate automatically.

## Configuration

Optional `~/.escanor/config.toml`:

```toml
[models]
default = "haiku"
effort = "low"          # Claude: low | medium | high | xhigh | max

[models.aliases]
local = "ollama:llama3.2"
```

Data lives in `~/.escanor/` (`escanor.db` for conversations, `telemetry.db` for per-call cost and latency).

## Development

```bash
.venv/bin/pytest -q
```

Design notes for the core: [decisions](docs/escanor-core/decision.md) ·
[flow](docs/escanor-core/flow.md) · [review guide](docs/escanor-core/review.md) ·
[bugs](docs/escanor-core/bugs.md).
