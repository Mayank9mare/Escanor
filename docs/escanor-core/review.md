# Escanor core — Review Guide

**Branch:** `main` · **Scope:** Phases 1–3 (scaffold, async CLI, tools, permissions, web, skills, second brain) ·
**Tests:** 122 passing · **Updated:** 2026-10-17

## TL;DR (read this first)

Escanor is a terminal chat that can switch between Claude (Haiku by default, Sonnet), GPT,
Gemini, a local Ollama model or a fake model, mid-conversation, by `/model` or just by asking.
Phase 2 gave it its first tools: the time, web search, reading a web page, switching models, and
loading your skills (`learn-anything`, `grill-me`) from mayank-awesome-skills. Every tool call goes
through a permission gate that asks before anything acts on the outside world, and web content is
fenced so a page can't instruct Escanor. The riskiest spots are the permission gate
([permissions.py](../../escanor/tools/permissions.py)), `web_fetch`'s address checks
([web.py](../../escanor/assistant/web.py)), and Claude's thinking replay
([anthropic_provider.py](../../escanor/models/anthropic_provider.py)), which is unit-tested but not yet run live.

## How to review in ~40 minutes

Read in this order: it follows a message from the model interface up to the terminal. ⭐ = spend most time here.

| # | File | What it does (simple words) | What to check | Size |
|---|---|---|---|---|
| 1 | [models/base.py](../../escanor/models/base.py) | The common model interface + `StreamEvent`, `raw` | `chat_stream_tools`: text events, then exactly one done | small |
| 2 ⭐ | [models/anthropic_provider.py](../../escanor/models/anthropic_provider.py) | Talks to Claude | raw replay only for same model; tool-result merging; errors → RuntimeError | 150 |
| 3 | [models/openai_provider.py](../../escanor/models/openai_provider.py) | GPT and Gemini | fragment assembly by index; `thought_signature` kept | +70 |
| 4 | [models/ollama_provider.py](../../escanor/models/ollama_provider.py) | Local models | JSON hold-back ([D11](decision.md#d11-ollama-hold-back-text-that-starts-like-a-json-tool-call)) | +70 |
| 5 | [models/instrumented.py](../../escanor/models/instrumented.py), [registry.py](../../escanor/models/registry.py), [mock_provider.py](../../escanor/models/mock_provider.py) | Telemetry wrapper, factory, fake model | streaming forwarded ([B3](bugs.md)) | small |
| 6 | [config.py](../../escanor/config.py) | Keys, `config.toml`, aliases, skills/web settings | `.env` never overrides exported vars | 150 |
| 7 ⭐ | [core/session.py](../../escanor/core/session.py) | Saves the conversation, keeps it valid | `repair()`; `collapse_tool_history()` only touches other models' turns | 230 |
| 8 | [core/prompts.py](../../escanor/core/prompts.py), [core/events.py](../../escanor/core/events.py) | System prompt, event types | nothing time-varying in the prompt ([D9](decision.md#d9-no-clock-time-in-the-system-prompt)) | small |
| 9 ⭐ | [core/agent.py](../../escanor/core/agent.py) | The turn loop | `astream` early close; max_tokens/refusal; deferred switch in `finally` | 200 |
| 10 | [tools/base.py](../../escanor/tools/base.py), [tools/registry.py](../../escanor/tools/registry.py) | Tool shape (side effect, group, untrusted) and lookup | disabled-group calls refused | 140 |
| 11 ⭐ | [tools/permissions.py](../../escanor/tools/permissions.py) | Asks / refuses / fences each call | taint is per turn; closing tag neutralised | 76 |
| 12 | [tools/system_tools.py](../../escanor/tools/system_tools.py) | get_time, enable_tools, switch_model | switch is requested, not applied | 100 |
| 13 ⭐ | [assistant/web.py](../../escanor/assistant/web.py) | web_search, web_fetch | non-global addresses refused, redirects re-checked ([D12](decision.md#d12-web_fetch-refuses-local-and-private-addresses-re-checked-on-every-redirect)) | 230 |
| 14 | [skills/loader.py](../../escanor/skills/loader.py), [skills/tools.py](../../escanor/skills/tools.py) | Finds and loads SKILL.md skills | path confinement in `Skill.read` | 170 |
| 15 | [brain/vault.py](../../escanor/brain/vault.py) | Notes and facts on disk | path confinement; backup before replace/forget ([D17](decision.md#d17-brain-writes-need-no-confirmation-overwrites-are-backed-up-instead)) | 170 |
| 16 ⭐ | [brain/index.py](../../escanor/brain/index.py) | Search over notes | incremental sync; embedder back-off; RRF merge | 250 |
| 17 | [brain/tools.py](../../escanor/brain/tools.py), [brain/embeddings.py](../../escanor/brain/embeddings.py) | Brain tools, embedders | facts tools are `affects_prompt`; attached folders fenced ([D18](decision.md#d18-your-own-vault-is-trusted-attached-folders-are-fenced-as-untrusted)) | 300 |
| 18 | [app.py](../../escanor/app.py) | Puts it all together | which tools and sections are installed | 110 |
| 19 | [cli.py](../../escanor/cli.py) | Terminal chat | `StdinLines` ([B8](bugs.md)); Ctrl-C cancels only the turn | 270 |
| 20 | [tests/](../../tests/) | 122 offline tests | error paths, not just happy paths | — |

Skip or skim: [telemetry.py](../../escanor/telemetry.py) (copied, path renamed),
[assistant/web_search.py](../../escanor/assistant/web_search.py) (copied DuckDuckGo scraper).

## The change, file by file

### 2. [anthropic_provider.py](../../escanor/models/anthropic_provider.py) ⭐

**In simple words:** turns Escanor's conversation into Claude's format, streams the reply, and
keeps Claude's exact reply blocks so they can be sent back next time.

**Why it's built this way:** [D3](decision.md#d3-replay-claudes-exact-reply-blocks-including-thinking-for-same-model-turns), [D6](decision.md#d6-no-eager_input_streaming-on-claude-tools).

**Look closely at:** [L85–L107](../../escanor/models/anthropic_provider.py#L85-L107) (blocks kept in `raw`),
[L109–L151](../../escanor/models/anthropic_provider.py#L109-L151) (replay condition, tool-result merging).

- [ ] **VERIFY** a Haiku tool loop works live once a key is set (e.g. "what time is it?").
- [ ] **QUESTION** default effort `low` ([D7](decision.md#d7-default-claude-effort-low)): OK for you?

### 7. [session.py](../../escanor/core/session.py) ⭐

**In simple words:** saves every message straight away and fixes two problems before each turn:
a tool call left without an answer, and tool turns from a model you've switched away from.

**Look closely at:** [L160–L194](../../escanor/core/session.py#L160-L194) `collapse_tool_history`.

- [ ] **VERIFY** collapsed history reads sensibly to the next model: ask for a web search on Haiku, then `/model gemini` and ask a follow-up.

### 9. [agent.py](../../escanor/core/agent.py) ⭐

**In simple words:** the loop: ask the model, stream the reply, run requested tools, repeat.

**Look closely at:** [L60–L101](../../escanor/core/agent.py#L60-L101) `astream`;
[L137–L144](../../escanor/core/agent.py#L137-L144) the deferred switch ([D13](decision.md#d13-switch_model-takes-effect-after-the-current-turn)).

### 11. [permissions.py](../../escanor/tools/permissions.py) ⭐

**In simple words:** for each tool call, decides "just do it", "ask first" or "no", and wraps web
text so the model treats it as data.

**Look closely at:** [L46–L64](../../escanor/tools/permissions.py#L46-L64) the decision;
[L66–L76](../../escanor/tools/permissions.py#L66-L76) per-turn taint ([D16](decision.md#d16-untrusted-taint-is-computed-from-the-session-per-turn)).

- [ ] **VERIFY** no Phase 2 tool is `external`, so nothing asks yet. The first askers arrive in Phases 3 and 6.

### 13. [web.py](../../escanor/assistant/web.py) ⭐

**In simple words:** search and read web pages, refusing anything on your own machine or home network.

**Look closely at:** [L49–L75](../../escanor/assistant/web.py#L49-L75) `check_url` and redirect re-check.

- [ ] **QUESTION** DNS rebinding is not covered (D12 trade-off). OK while every fetch is visible in the chat?

## Things to decide together

- [D7](decision.md#d7-default-claude-effort-low): default Claude effort `low`.
- [D8](decision.md#d8-gptgemini-default-ids-gpt-5--gemini-36-flash): GPT/Gemini default ids.

## Known issues

| ID | Severity | One line | Blocks merge? |
|---|---|---|---|
| B5 | LOW | No token usage from OpenAI/Gemini streams | No |
| B6 | LOW | Cancelled turn holds its stream until the next chunk | No (Phase 4) |
| B9 | LOW | Telemetry stores whole conversations per call | No (Phase 8) |
| B10 | LOW | Model-switch collapse resets message timestamps | No |

## How to verify locally

```bash
.venv/bin/pytest -q
.venv/bin/escanor --model mock          # no key needed; try /skills and /tools
.venv/bin/escanor                       # Haiku, needs ANTHROPIC_API_KEY in .env
```

Then, with a key: "what's the time?", "search the web for Kokoro TTS", "read https://example.com",
"teach me how attention works in transformers" (loads learn-anything), "switch to sonnet",
"remember that I prefer window seats" then `/new` and "which seat do I like?".
For semantic search: `brew install ollama && ollama pull nomic-embed-text`.
