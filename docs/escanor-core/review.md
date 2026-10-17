# Escanor core — Review Guide

**Branch:** `main` · **Scope:** Phase 1 (scaffold + async CLI) · **Tests:** 53 passing ·
**Updated:** 2026-10-17

## TL;DR (read this first)

Escanor now has a working text chat in the terminal that can switch between Claude (Haiku by
default, Sonnet), GPT, Gemini, a local Ollama model, or a fake model, mid-conversation. The model
code is copied from kmare-code and extended so replies stream while still allowing tool calls.
On top sits a new async agent loop that the web UI and voice will reuse. No real tools exist yet;
Phase 2 adds them. The riskiest spots are the Claude thinking replay
([anthropic_provider.py](../../escanor/models/anthropic_provider.py)) and the history rewrite on a
model switch ([session.py](../../escanor/core/session.py)), since both must keep provider rules exactly.

## How to review in ~25 minutes

Read in this order: it follows a message from the model interface up to the terminal. ⭐ = spend most time here.

| # | File | What it does (simple words) | What to check | Size |
|---|---|---|---|---|
| 1 | [models/base.py](../../escanor/models/base.py) | The common model interface + new `StreamEvent`, `raw` | `chat_stream_tools` contract: text events, then exactly one done | +40 |
| 2 ⭐ | [models/anthropic_provider.py](../../escanor/models/anthropic_provider.py) | Talks to Claude | raw replay only for same model; tool-result merging; errors → RuntimeError | rewrite |
| 3 | [models/openai_provider.py](../../escanor/models/openai_provider.py) | GPT and Gemini | fragment assembly by index; `thought_signature` kept | +70 |
| 4 | [models/ollama_provider.py](../../escanor/models/ollama_provider.py) | Local models | JSON hold-back ([D11](decision.md#d11-ollama-hold-back-text-that-starts-like-a-json-tool-call)) | +70 |
| 5 | [models/instrumented.py](../../escanor/models/instrumented.py), [registry.py](../../escanor/models/registry.py), [mock_provider.py](../../escanor/models/mock_provider.py) | Telemetry wrapper, factory, fake model | streaming forwarded ([B3](bugs.md)) | small |
| 6 | [config.py](../../escanor/config.py) | Keys, `config.toml`, aliases | aliases, `.env` never overrides exported vars | new |
| 7 ⭐ | [core/session.py](../../escanor/core/session.py) | Saves the conversation, keeps it valid | `repair()`; `collapse_tool_history()` only touches other models' turns | new |
| 8 | [core/prompts.py](../../escanor/core/prompts.py), [core/events.py](../../escanor/core/events.py) | System prompt, event types | nothing time-varying in the prompt ([D9](decision.md#d9-no-clock-time-in-the-system-prompt)) | new |
| 9 ⭐ | [core/agent.py](../../escanor/core/agent.py) | The turn loop | `astream` closes the stream on early exit; max_tokens/refusal handling | new |
| 10 | [cli.py](../../escanor/cli.py) | Terminal chat | Ctrl-C cancels only the turn | new |
| 11 | [tests/](../../tests/) | 53 offline tests | error paths, not just happy paths | new |

Skip or skim: [telemetry.py](../../escanor/telemetry.py) (copied, path renamed),
[tools/base.py](../../escanor/tools/base.py) (copied), [assistant/web_search.py](../../escanor/assistant/web_search.py)
(copied, wired up in Phase 2).

## The change, file by file

### 2. [anthropic_provider.py](../../escanor/models/anthropic_provider.py) ⭐

**In simple words:** turns Escanor's conversation into Claude's format, streams the reply, and
keeps Claude's exact reply blocks so they can be sent back next time.

**Why it's built this way:** [D3](decision.md#d3-replay-claudes-exact-reply-blocks-including-thinking-for-same-model-turns), [D6](decision.md#d6-no-eager_input_streaming-on-claude-tools).

**Look closely at:**
- [L85–L107](../../escanor/models/anthropic_provider.py#L85-L107): which block types are kept in `raw`.
- [L109–L151](../../escanor/models/anthropic_provider.py#L109-L151): replay condition and tool-result merging.

- [ ] **VERIFY** a Haiku tool loop works live once a key is set (thinking replay is only unit-tested).
- [ ] **QUESTION** default effort `low` ([D7](decision.md#d7-default-claude-effort-low)): OK for you?

### 7. [session.py](../../escanor/core/session.py) ⭐

**In simple words:** saves every message straight away and fixes two problems before each turn:
a tool call left without an answer, and tool turns from a model you've switched away from.

**Look closely at:** [L160–L194](../../escanor/core/session.py#L160-L194) `collapse_tool_history`.

- [ ] **VERIFY** collapsed history reads sensibly to the next model (try `/model gemini` after a tool turn, Phase 2).

### 9. [agent.py](../../escanor/core/agent.py) ⭐

**In simple words:** the loop: ask the model, stream the reply, run requested tools, repeat.

**Look closely at:** [L60–L101](../../escanor/core/agent.py#L60-L101) `astream` (thread bridge and early close),
then `run_turn`'s handling of `max_tokens` and `refusal`.

## Things to decide together

- [D7](decision.md#d7-default-claude-effort-low): default Claude effort `low`.
- [D8](decision.md#d8-gptgemini-default-ids-gpt-5--gemini-36-flash): GPT/Gemini default ids.

## Known issues

| ID | Severity | One line | Blocks merge? |
|---|---|---|---|
| B5 | LOW | No token usage from OpenAI/Gemini streams | No |
| B6 | LOW | Cancelled turn holds its stream until the next chunk | No |

## How to verify locally

```bash
.venv/bin/pytest -q
.venv/bin/escanor --model mock          # no key needed
.venv/bin/escanor                       # Haiku, needs ANTHROPIC_API_KEY
```
