# Escanor

A local, Jarvis-style personal assistant: second brain, research tutor, AI DJ on YouTube Music,
and voice. See [PLAN.md](PLAN.md) for what it will be and
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for how it's being built.

**Status:** Phase 1 (text chat with switchable models) works.

## Quickstart

```bash
uv venv --python 3.12
uv pip install -e ".[all-models,dev]"
cp .env.example .env   # then add ANTHROPIC_API_KEY
.venv/bin/escanor
```

No key yet? `.venv/bin/escanor --model mock` runs the whole loop with a fake model.

## In the chat

| Command | What it does |
|---|---|
| `/model` | show the current model |
| `/model sonnet` | switch: `haiku`, `sonnet`, `gpt`, `gemini`, `local`, `mock`, or `provider:model-id` |
| `/models` | list aliases |
| `/new` | start a new conversation |
| `/quit` | exit (Ctrl-D works too) |

Ctrl-C stops a reply mid-way. `escanor --continue` resumes the last conversation.

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
