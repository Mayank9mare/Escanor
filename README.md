# Escanor

A local, Jarvis-style personal assistant for your Mac. Escanor keeps a **second brain** of your notes,
answers questions with the web, runs your **reminders, calendar and timers**, plays **YouTube Music as an
AI DJ**, and **teaches you research papers**, by text or by voice.

It runs on your machine. Your notes, conversations and voice stay local; only the model calls go to
the provider you choose (Claude by default, or GPT, Gemini, or a fully local Ollama model).

```text
you › remember that I prefer window seats on flights
  · remember_fact({"fact": "Prefers window seats on flights"})
escanor › Got it! I've saved that you prefer window seats on flights.

you › explain this paper: https://arxiv.org/abs/1706.03762
  · enable_tools({"group": "research"})
  · research_ingest({"source": "https://arxiv.org/abs/1706.03762"})
  · use_skill({"name": "learn-anything"})
escanor › In plain words: attention lets every word look at every other word… What do you already know?
```

## What it can do

| Area | What you can say | How |
|---|---|---|
| **Chat & models** | "switch to sonnet", "think hard about…" | Claude Haiku by default; Sonnet, GPT, Gemini, Ollama; switch any time, mid-conversation |
| **Second brain** | "what did I write about project X?", "remember that…" | Markdown notes in `~/.escanor/brain`, keyword + semantic search, a `facts.md` it always keeps in mind |
| **Web & news** | "what's in the news?", "latest on…?", "read this link" | Google News headlines, DuckDuckGo search, page reading; web text is treated as data, never instructions |
| **Voice** | hold the mic, or say "hey Escanor" | Whisper (speech-to-text) and Piper (voice), both local; replies spoken sentence by sentence |
| **AI DJ** | "DJ mode, chill evening", "skip", "who is this?" | YouTube Music desktop app + your history and likes; short spoken intros between songs |
| **Assistant** | "remind me to call mom at 7", "what's on today?", "good morning" | Apple Reminders and Calendar, timers, notifications, morning briefing |
| **Research tutor** | "explain this paper <arXiv / link / PDF>" | Saves it to the brain, then teaches with the [learn-anything](https://github.com/Mayank9mare/mayank-awesome-skills) skill, with spaced review |
| **Any service (MCP)** | "check my GitHub notifications", "search Notion for…" | Plug in any [MCP](https://modelcontextprotocol.io) server: Gmail, Notion, GitHub, Slack, Drive… |
| **Proactive** | (it speaks first) | Meeting heads-ups with your related notes, an evening wrap-up, break nudges; quiet hours |
| **Your screen** | "what's on my screen?", "explain this error" | Screenshot to the model, after you say yes |

Anything that acts outside the conversation (creating a reminder, adding an event, opening a link)
**asks you first**. Music control, notes and timers don't.

## Run it

Already installed? Then it's one command:

```bash
escanor          # chat in the terminal
escanor serve    # the HUD in your browser at http://127.0.0.1:8770: hold Space (or the mic) to talk
escanor listen   # hands-free: say "hey Escanor"
escanor brief    # morning briefing, then chat
```

The HUD is a living sun, after Escanor, the Lion's Sin of Pride: its **solar power follows the real time
of day**, rising through the morning, **THE ONE** at high noon, a humble ember after dusk. Its flames pulse
with your voice while **listening**, burn ember-red while **deliberating**, and throw solar flares while
**proclaiming**, with live subtitles, a tool readout and a transcript panel.

Under the hood: the sun is a **WebGL shader** (churning plasma, limb darkening, a flame corona) with embers
drifting off it, and it **lip-syncs to the real voice**: the server sends the loudness curve of every spoken
sentence. Replies render with formatting and links. Keys: <kbd>Space</kbd> talk · <kbd>⌘K</kbd> type ·
<kbd>Esc</kbd> interrupt · <kbd>T</kbd> transcript.

The personality follows the sun too: proud and regal by day, magnificently confident at noon, polite and
modest at night, but always accurate and brief. Prefer a neutral assistant? Set `[persona] style = "plain"`.

## Quickstart

Needs macOS, Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Mayank9mare/Escanor.git && cd Escanor
uv venv --python 3.12
uv pip install -e ".[all-models,dev]"
mkdir -p ~/.escanor && cp .env.example ~/.escanor/.env   # add ANTHROPIC_API_KEY (or GEMINI_API_KEY / OPENAI_API_KEY)
ln -s "$PWD/.venv/bin/escanor" ~/.local/bin/escanor       # optional: `escanor` from anywhere
escanor                                                   # text chat in the terminal
```

Keys go in `~/.escanor/.env` so they're found wherever you run Escanor (a `.env` in the current folder works too).

No key yet? `escanor --model mock` runs everything with a fake model.
Only a Gemini key? Put `[models]` / `default = "gemini"` in `~/.escanor/config.toml` (free tier: `gemini-3.5-flash-lite`).

## Voice

```bash
uv pip install -e ".[voice,server]"
.venv/bin/escanor serve     # web UI at http://127.0.0.1:8770, hold the mic (or Space) to talk

uv pip install -e ".[listen]"
.venv/bin/escanor listen    # hands-free: say "hey Escanor" (or --enter to press Enter instead)
```

Say **"hey Escanor"**, alone or with your question in the same breath ("hey Escanor, what's the
time?"). The wake phrase is spotted by Whisper with a hint for the name, so any phrase works:
`[voice] wake_word = "hey escanor"`. His voice is a deep male Piper voice spoken a touch slowly
(`tts_voice = "en_US-ryan-high"`, `tts_speed = 1.1`); `en_GB-alan-medium` is a British alternative.

Pressing the mic, or sending a message while Escanor is talking, interrupts it. Talking over it in
`listen` mode needs headphones (`[voice] barge_in = "headphones"`), otherwise it hears itself.

## YouTube Music DJ

1. Install the [YouTube Music desktop app](https://github.com/th-ch/youtube-music), sign in, enable
   **Plugins → API Server**, and set its hostname to `127.0.0.1`.
2. `uv pip install -e ".[music]"`, then `.venv/bin/escanor music connect`: click **Allow** in the app.
   Optionally paste your browser's request headers so the DJ can use your history and liked songs.
3. Say or type "DJ mode, chill evening".

The DJ picks each next song from YouTube Music's radio and your likes, introduces some of them, and
turns the music down whenever Escanor talks or listens.

## Connect any service (MCP)

Add [MCP](https://modelcontextprotocol.io) servers to `~/.escanor/config.toml`; each becomes a tool group:

```toml
[mcp.servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_PERSONAL_ACCESS_TOKEN = "env:GITHUB_TOKEN" }   # read from your environment / .env
auto_approve = ["search_*", "get_*"]                            # run these without asking

[mcp.servers.notes]
url = "https://example.com/mcp"                                 # remote servers work too
```

Tools the server marks read-only run straight away; anything else asks first. `/mcp` shows what's connected.

## Proactive mode

While `escanor serve`, `listen` or the chat is running, Escanor speaks up on its own: a heads-up 10 minutes
before a calendar event (with your related note), an evening wrap-up at 21:00, and a nudge after two hours at
the keyboard. Quiet hours (22:30–07:30) and "be quiet for an hour" keep it polite; tune it under `[proactive]`.

## Research papers

```bash
uv pip install -e ".[research]"
```

Then "explain this paper https://arxiv.org/abs/1706.03762", a link to any article or PDF, or a PDF in
Downloads/Documents/Desktop. The source is saved under `research/` in your brain, the
[learn-anything](https://github.com/Mayank9mare/mayank-awesome-skills) skill teaches it, and its
learning log tells the morning briefing when a topic is due for review. Skills are read from
`~/projects/mayank-awesome-skills` and `~/.escanor/skills`.

## Commands

**In the chat**

| Command | What it does |
|---|---|
| `/model [name]` | show or switch the model: `haiku`, `sonnet`, `gpt`, `gemini`, `local`, `mock`, or `provider:model-id` |
| `/models`, `/skills`, `/tools` | list model aliases, loadable skills, tool groups |
| `/brain` | re-index notes and show their size |
| `/new`, `/quit` | new conversation, exit |

Ctrl-C stops a reply mid-way. `escanor --continue` resumes the last conversation.

**From the shell**

| Command | What it does |
|---|---|
| `escanor brief` | morning briefing (calendar, reminders, weather, reviews), then chat |
| `escanor serve` / `escanor listen` | web UI with push-to-talk / hands-free voice |
| `escanor music connect\|status` | connect YouTube Music |
| `escanor usage [--days 7]` | model calls, tokens and cost |
| `escanor eval [--delay 20]` | check each capability picks the right tool (stubbed tools, real model calls) |
| `escanor brain reindex\|stats\|backup` | rebuild the search index, show size, commit notes to git |
| `escanor service install\|uninstall\|status` | start the web UI at login (launchd) |

## Configuration

Everything works with no config. To change things, create `~/.escanor/config.toml`:

```toml
[models]
default = "haiku"            # or sonnet, gemini, gpt, local
effort = "low"               # Claude effort: low | medium | high | xhigh | max

[models.aliases]
local = "ollama:llama3.2"

[router]
escalate_to = "sonnet"       # "think hard" or long tool chains hand the turn to this model ("" = off)

[budget]
daily_usd = 3.0              # stop calling paid models after this much per day (0 = no cap)

[brain]
embedder = "ollama"          # semantic search via `ollama pull nomic-embed-text`; falls back to keywords
extra_dirs = ["~/Obsidian"]  # search an existing vault, read-only

[assistant]
city = "Bengaluru"           # weather in the morning briefing

[voice]
tts = "piper"                # or "sarvam" (cloud, Indian English voices)
barge_in = "off"             # "headphones" to interrupt by talking
```

All defaults are in [`escanor/config.py`](escanor/config.py). API keys only come from the
environment or `.env`, never from the config file.

**Your data** lives in `~/.escanor/`: `brain/` (your notes, plain Markdown), `brain-history/`
(old versions), `escanor.db` (conversations), `telemetry.db` (per-call tokens and latency), and the
YouTube Music tokens (owner-only permissions).

## How it works

```text
CLI · web UI · voice loop  ──events──►  Agent (async tool loop)  ──►  Claude / GPT / Gemini / Ollama
                                           │
                    permission gate (asks before external actions, fences untrusted web text)
                                           │
   core tools ─ time · web · brain · skills · timers · briefing
   groups     ─ brain editing · music/DJ · reminders/calendar/Mac · research   (switched on when needed)
```

- **One agent, many interfaces.** The terminal, web page and hands-free loop all consume the same
  event stream, so they behave identically.
- **Small requests.** Only core tools are sent on every call; other groups are switched on with
  `enable_tools` when a request needs them. Skills are loaded only when used.
- **Safe with outside text.** Web pages, papers, the clipboard and imported notes are fenced as
  untrusted. A page can't make Escanor remember things or act without your yes.
- **One speaker.** A single audio owner orders speech (confirmations > replies > timers > DJ) and
  ducks the music.

Design notes: [decisions](docs/escanor-core/decision.md) · [runtime flow](docs/escanor-core/flow.md) ·
[review guide](docs/escanor-core/review.md) · [bugs](docs/escanor-core/bugs.md) ·
[plan](PLAN.md) · [implementation plan](IMPLEMENTATION_PLAN.md).

## Status

All eight phases of the [implementation plan](IMPLEMENTATION_PLAN.md) are built, with 233 offline tests.

Verified live: Gemini end to end (10/10 tool choice on the eval), Piper and Whisper speech, the web UI,
YouTube Music search, arXiv ingestion. Still to verify on a real setup: Claude, the YouTube Music app
and DJ, Reminders/Calendar permissions, and the microphone and wake word.

## Development

```bash
uv pip install -e ".[all-models,dev,voice,server,music,research]"
.venv/bin/pytest -q
```

Tests run offline: models, speech, the YouTube Music app and AppleScript are all faked.

## Credits

The model layer and the speech engines started as copies of
[kmare-code](https://github.com/Mayank9mare/kmare-code) (kmare-voice). Built on
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Piper](https://github.com/rhasspy/piper),
[ytmusicapi](https://github.com/sigma67/ytmusicapi), [th-ch/youtube-music](https://github.com/th-ch/youtube-music)
and [openWakeWord](https://github.com/dscripka/openWakeWord).
