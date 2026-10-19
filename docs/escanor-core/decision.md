# Escanor core — Decisions

Every non-trivial choice made while building the core (Phases 1–2 of
[IMPLEMENTATION_PLAN.md](../../IMPLEMENTATION_PLAN.md)): what was chosen, what was rejected, and
what we're trading away. Newest at the bottom. Reversed decisions are marked **Superseded by Dn**.

## Index

| ID | Decision | Status | Date |
|---|---|---|---|
| D1 | Copy kmare-code's models/tools/telemetry instead of depending on it | Accepted (you) | 2026-10-17 |
| D2 | Async agent loop; blocking providers/tools run on worker threads | Accepted | 2026-10-17 |
| D3 | Replay Claude's exact reply blocks (incl. thinking) for same-model turns | Accepted | 2026-10-17 |
| D4 | Collapse other models' tool turns to text on a model switch | Refined by D38 | 2026-10-17 |
| D5 | Build streaming-with-tools in Phase 1, not Phase 2 | Accepted | 2026-10-17 |
| D6 | No `eager_input_streaming` on Claude tools | Accepted | 2026-10-17 |
| D7 | Default Claude effort `low` | **Proposed** (your call) | 2026-10-17 |
| D8 | GPT/Gemini default ids `gpt-5` / `gemini-3.5-flash-lite` | Accepted (you, Gemini part) | 2026-10-17 |
| D9 | No clock time in the system prompt | Accepted | 2026-10-17 |
| D10 | Store each message as one JSON body in SQLite | Accepted | 2026-10-17 |
| D11 | Ollama: hold back text that starts like a JSON tool call | Accepted | 2026-10-17 |
| D12 | `web_fetch` refuses local/private addresses, re-checked on redirects | Accepted | 2026-10-17 |
| D13 | `switch_model` takes effect after the current turn | Accepted | 2026-10-17 |
| D14 | Tool groups exist now, though only `core` has tools | Accepted | 2026-10-17 |
| D15 | `set_timer` moves from Phase 2 to Phase 6 | Accepted | 2026-10-17 |
| D16 | Untrusted taint is computed from the session, per turn | Accepted | 2026-10-17 |
| D17 | Brain writes need no confirmation; overwrites are backed up instead | Accepted | 2026-10-17 |
| D18 | Your own vault is trusted; attached folders are fenced as untrusted | Accepted | 2026-10-17 |
| D19 | Brain tools split: search/read/remember in core, editing in a `brain` group | Accepted | 2026-10-17 |
| D20 | facts.md is a plain bullet list, newest kept when over the prompt budget | Accepted | 2026-10-17 |
| D21 | Speech plays through `afplay`, owned by one `AudioOut` | Accepted | 2026-10-17 |
| D22 | Energy-based voice detection instead of Silero | Accepted | 2026-10-17 |
| D23 | Only replies to voice input are spoken (typed: opt-in) | Accepted | 2026-10-17 |
| D24 | Barge-in: always by mic/new message in the web UI; by voice only with headphones | Accepted | 2026-10-17 |
| D25 | Kokoro TTS deferred | Accepted | 2026-10-17 |
| D26 | Web UI on port 8770 (8765 is TradeMare's) | Accepted | 2026-10-17 |
| D27 | Music tools are `write`, not `external` (no "Allow?" per skip) | Accepted | 2026-10-17 |
| D28 | Search via ytmusicapi, playback via the desktop app | Accepted | 2026-10-17 |
| D29 | DJ v1: pause → speak → play; model picks from radio + likes | Accepted | 2026-10-17 |
| D30 | ytmusicapi browser-cookie login (not OAuth) | Accepted | 2026-10-17 |
| D31 | Reminders/Calendar through AppleScript, not EventKit | Accepted | 2026-10-17 |
| D32 | `open_url` asks first; clipboard text is untrusted | Accepted | 2026-10-17 |
| D33 | Timers live in the running process | Accepted | 2026-10-17 |
| D34 | The morning briefing is a tool the model summarises | Accepted | 2026-10-17 |
| D35 | Ingested sources stay untrusted inside the brain | Accepted | 2026-10-17 |
| D36 | Local files only from allowed folders | Accepted | 2026-10-17 |
| D37 | Explaining is learn-anything's job; Escanor supplies ingest, paging, reviews | Accepted | 2026-10-17 |
| D38 | Collapse tool history only when the provider changes | Accepted | 2026-10-17 |
| D39 | Escalate to Sonnet on "think hard" or after 4 tool steps, then hand back | Accepted | 2026-10-17 |
| D40 | Daily spend cap from telemetry, priced conservatively | Accepted | 2026-10-17 |
| D41 | Evals grade tool choice with stubbed tools | Accepted | 2026-10-17 |
| D42 | MCP tools: permission from the server's read-only hint, otherwise ask | Accepted | 2026-10-18 |
| D43 | Async start/stop hooks on the app for long-lived connections | Accepted | 2026-10-18 |
| D44 | Proactive checks are local and rule-based, no model calls | Accepted | 2026-10-18 |
| D45 | Screenshots ask first and stay in history as files | Accepted | 2026-10-18 |
| D46 | Images reach non-Claude models as a follow-up user message | Accepted | 2026-10-18 |
| D47 | DJ v2 talks over the song's opening; v1 pause kept as an option | Accepted | 2026-10-18 |
| D48 | Shortcuts: always ask, except names you allow | Accepted | 2026-10-18 |
| D49 | Menubar polls a small local REST API | Accepted | 2026-10-18 |
| D50 | Same-origin check on the WebSocket and control API | Accepted | 2026-10-18 |

---

## D1. Copy kmare-code's models/tools/telemetry instead of depending on it

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 1 · **Decided by:** You

### Context
kmare-code already has a provider-neutral `Model` interface (Anthropic, OpenAI, Gemini, Ollama,
mock), a tool interface, telemetry, and the kmare-voice engines. Escanor needs all of them, plus
changes kmare-code doesn't need (tool streaming, thinking replay).

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Copy and evolve** | Free to change interfaces; no cross-repo release dance | Fixes don't flow back automatically |
| B. Depend on kmare-code | One copy of the code | Escanor's changes would have to land in kmare-code first; kmare-code is sync |

### Decision
A, your choice: Escanor needs to change the core interfaces (D2, D3, D5).

### Trade-offs accepted
- Bugs fixed here (B1, B2, B4 in [bugs.md](bugs.md)) are still present in kmare-code until ported back.

### Consequences
- **Code:** [models/](../../escanor/models/), [tools/base.py](../../escanor/tools/base.py), [telemetry.py](../../escanor/telemetry.py).
- **Follow-ups:** consider porting B1/B2/B4 fixes to kmare-code.

### Revisit if
Both projects keep needing the same provider fixes.

---

## D2. Async agent loop; blocking providers and tools run on worker threads

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 2

### Context
The assistant must ask for confirmation mid-turn, stop instantly when interrupted, and later run
the DJ alongside a conversation. kmare-code is fully synchronous.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Async loop + `astream()` thread bridge** | Providers stay as copied; cancellation = cancel a task | One worker thread per in-flight model call |
| B. Native async SDK clients (`AsyncAnthropic`, `AsyncOpenAI`, httpx for Ollama) | No threads; cancellation closes the socket immediately | Rewrite all four providers twice (sync + async) or drop sync |
| C. Stay sync | Simplest | Can't do confirmations, barge-in or background DJ cleanly |

### Decision
A: it buys all the async behaviour for ~50 lines ([agent.py#L60-L101](../../escanor/core/agent.py#L60-L101)).

### Trade-offs accepted
- After a cancel, the worker thread runs until the provider's next chunk before it closes the
  stream (see [B6](bugs.md#b6-a-cancelled-turn-keeps-its-provider-stream-open-until-the-next-chunk)).
- A running tool can't be interrupted; its thread finishes and the result is discarded.

### Revisit if
Barge-in latency (Phase 4) is dominated by waiting for the next chunk → move the hot provider to its async client.

---

## D3. Replay Claude's exact reply blocks (including thinking) for same-model turns

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 1

### Context
Haiku 5.5 and Sonnet 5.5 think by default. In a tool loop, the assistant turn's thinking blocks
must be passed back unchanged. kmare-code's generic `{content, tool_calls}` shape drops them.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Keep provider `raw` blocks on each turn; replay when the model matches** | Thinking preserved; other providers unaffected | Extra JSON per message in the DB |
| B. Disable thinking | No replay needed | Not allowed on Haiku 5.5 above `high` effort, and costs quality |
| C. Store full native history per provider | Exact for every provider | Two parallel histories to keep consistent |

### Decision
A ([anthropic_provider.py#L85-L107](../../escanor/models/anthropic_provider.py#L85-L107), replay at
[#L109-L151](../../escanor/models/anthropic_provider.py#L109-L151)).

### Trade-offs accepted
- Messages carry a `raw` field that only Claude reads.
- History must stay append-only for Claude turns (enforced by [session.py](../../escanor/core/session.py)).

### Revisit if
Another provider needs verbatim replay → generalise `raw` beyond Anthropic.

---

## D4. Collapse other models' tool turns to text on a model switch

**Status:** Refined by [D38](#d38-collapse-tool-history-only-when-the-provider-changes) · **Date:** 2026-10-17 · **Step:** 2

### Context
You want to switch between Haiku, Sonnet, GPT, Gemini and Ollama mid-conversation. Tool-call
history doesn't port: Gemini rejects replayed calls without its `thought_signature`, ids differ.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Rewrite foreign tool turns as text (`[used web_search {...} -> result]`)** | Works for every pair of providers | Old tool turns lose their structure for the new model |
| B. Start a new conversation on switch | Trivially safe | Loses context, which defeats the point of switching |
| C. Keep native shapes, hope providers accept them | No rewrite | Fails on Gemini today; fragile |

### Decision
A ([session.py#L160-L194](../../escanor/core/session.py#L160-L194)), applied at the start of the next
turn. The new model's own earlier turns are left untouched, so switching back replays them verbatim.

### Trade-offs accepted
- Tool results are truncated to 300 chars in the collapsed text.
- The rewrite is persisted: switching back to the original model sees the collapsed text, not the original tool calls.

### Revisit if
Long tool outputs matter after a switch → raise the limit or keep a pointer to the full result.

---

## D5. Build streaming-with-tools in Phase 1, not Phase 2

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 1

### Context
The plan put `chat_stream_tools` in Phase 2 and a text-only CLI in Phase 1, which would mean writing
the CLI twice.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Implement it for every provider in Phase 1** | CLI is built once on the real event stream | Phase 1 is bigger |
| B. Follow the plan literally | Smaller Phase 1 | CLI rewritten in Phase 2 |

### Decision
A. Phase 2 adds tools, not plumbing.

---

## D6. No `eager_input_streaming` on Claude tools

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 1

### Context
The Claude API guidance defaults to `eager_input_streaming: true` for streaming with client tools,
so large tool inputs stream as they're generated. With it on, the server stops validating inputs
and the client must validate.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Leave it off** | Server validates tool inputs; nothing to re-check | A large tool input arrives in one burst |
| B. Turn it on | Big inputs stream | Must validate every input against its schema ourselves |

### Decision
A: Escanor's tool inputs are small (a query, a note title). The latency gain is ~zero and the
validation burden is real.

### Revisit if
A tool takes large inputs (e.g. `brain_write` with long notes in Phase 3).

---

## D7. Default Claude effort `low`

**Status:** **Proposed (your call)** · **Date:** 2026-10-17 · **Step:** 1

### Context
Haiku 5.5 defaults to `medium` effort. Effort trades thoroughness for latency and cost.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `low`** | Fastest first word, cheapest: good for voice and chit-chat | Weaker on multi-step tool use |
| B. `medium` (model default) | Better multi-step reasoning | Slower replies |

### Decision
A for now, set in `[models] effort` ([config.py](../../escanor/config.py)). Override in `~/.escanor/config.toml`.

### Revisit if
Haiku fumbles tool sequences in Phase 2/3. Then try `medium`, or route hard turns to Sonnet (Phase 8).

---

## D8. GPT/Gemini default ids `gpt-5` / `gemini-3.6-flash`

**Status:** **Proposed (your call)** · **Date:** 2026-10-17 · **Step:** 1

### Context
The `gpt` and `gemini` aliases need model ids. I can't check which ids your accounts have.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. The ids kmare-code ran live (`gpt-5`, `gemini-3.6-flash`)** | Known to have worked for you | May not be the newest/cheapest |
| B. Guess newer ids | Possibly newer | May not exist → 404 |

### Decision
A. Change them in `~/.escanor/config.toml` under `[models.aliases]`.

**Live note (2026-10-17):** `gemini-3.6-flash`'s free tier allows only 20 requests/day; one agent turn
uses 2–4. `gemini-3.5-flash-lite` passed the remaining eval cases and has its own quota. **Decision update (you, 2026-10-17):** the `gemini` alias now points to
`gemini-3.5-flash-lite`. Use `/model gemini:gemini-3.6-flash` for the stronger model when you have quota.

---

## D9. No clock time in the system prompt

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 2

### Context
The system prompt is the front of Claude's cached prefix. Any change to it invalidates the cache
for the whole conversation.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Date only; time via a `get_time` tool (Phase 2)** | Cache survives every turn of a day | One tool call when time matters |
| B. Put the current time in the prompt | No tool call | Cache misses on every turn: slower and pricier |

### Decision
A ([prompts.py](../../escanor/core/prompts.py)).

---

## D10. Store each message as one JSON body in SQLite

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 2

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. One `body` JSON column per message** | Shape can evolve (raw, model, is_error) without migrations | Can't SQL-query fields directly |
| B. One column per field | Queryable | A migration for every new field |

### Decision
A ([session.py#L34-L47](../../escanor/core/session.py#L34-L47)). Search over conversations comes
from the brain index (Phase 3), not SQL.

---

## D11. Ollama: hold back text that starts like a JSON tool call

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 1

### Context
Some local models print a tool call as JSON text instead of using native tool calls. kmare-code
recovers these at the end, but when streaming the JSON would already have been printed (or spoken).

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. If the reply's first character is `{` or `` ` ``, buffer until the end** | JSON never reaches the user | Replies that genuinely start with code arrive all at once |
| B. Stream everything, parse at the end | Simplest | Voice would read JSON aloud |

### Decision
A ([ollama_provider.py](../../escanor/models/ollama_provider.py), `chat_stream_tools`).

---

## D12. `web_fetch` refuses local and private addresses, re-checked on every redirect

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 7

### Context
The model chooses which URL to fetch, and the URL may come from a page it just read. Escanor runs on
your Mac next to local services (the YouTube Music app's API on `localhost:26538`, your router admin
page) that a web page must not be able to reach through it.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Resolve the host; refuse any non-global address; check each redirect too** | Blocks the obvious paths to localhost/LAN | DNS rebinding (host resolves public at check, private at connect) not covered |
| B. Allow everything | Simplest | A page could steer Escanor at local services |
| C. Pin the checked IP for the connection | Also closes DNS rebinding | Needs a custom HTTP connection class; TLS SNI handling |

### Decision
A ([web.py#L49-L75](../../escanor/assistant/web.py#L49-L75)), switchable with `[web] allow_private_hosts`.

### Trade-offs accepted
- DNS rebinding stays possible. Acceptable for a personal assistant whose fetches you can see in the CLI.

### Revisit if
Escanor fetches unattended (e.g. DJ looking up artist facts in the background) → do C.

---

## D13. `switch_model` takes effect after the current turn

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 6

### Context
Saying "switch to Sonnet" makes the current model call the `switch_model` tool mid-turn. If the switch
happened immediately, the next step would send the old model's in-flight tool calls to the new model,
which can be rejected (D4).

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Queue it; apply when the turn ends; emit `ModelSwitched`** | A turn never mixes models | The confirmation sentence still comes from the old model |
| B. Switch immediately and collapse history mid-turn | New model answers at once | Rewrites history inside a live Claude tool loop (breaks thinking replay) |

### Decision
A ([agent.py#L121-L144](../../escanor/core/agent.py#L121-L145)). Applied in a `finally`, so a cancelled turn still switches.

---

## D14. Tool groups exist now, though only `core` has tools

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 6

### Context
The plan caps tool schemas at ~15 per call via groups switched on by `enable_tools`. Phase 2 has
seven tools, all core.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Build the mechanism now** | Phases 3–6 just set `group = "music"` etc.; tested once | `enable_tools` is in every request with nothing to enable |
| B. Add it in Phase 5 when music arrives | Less code now | Retrofit across several phases' tools |

### Decision
A ([registry.py](../../escanor/tools/registry.py)). `enable_tools` says "no extra groups yet" until there are some.

---

## D15. `set_timer` moves from Phase 2 to Phase 6

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 6

### Context
A timer has to announce itself later, outside any turn. The CLI has no channel for that yet; voice
(Phase 4) adds the audio owner that timers should speak through.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Build it with reminders in Phase 6** | Uses the real notification path (speaker + macOS notification) | No timers until then |
| B. CLI-only timer that prints | Available now | Thrown away once voice exists |

### Decision
A. Added to Phase 6 in [IMPLEMENTATION_PLAN.md](../../IMPLEMENTATION_PLAN.md).

---

## D16. Untrusted taint is computed from the session, per turn

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 6

### Context
`remember_fact` (Phase 3) must ask for confirmation if the same turn already read untrusted content
(plan §2.4). Something has to know whether the turn is "tainted".

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Scan the session back to the latest user message for untrusted tool calls** | Stateless; correct after restarts and interruptions | O(turn length) per gated call (tiny) |
| B. A flag on the gate, reset per turn | O(1) | Wrong after a crash/resume; one more piece of state to keep in sync |

### Decision
A ([permissions.py#L66-L76](../../escanor/tools/permissions.py#L66-L77)).

---

## D17. Brain writes need no confirmation; overwrites are backed up instead

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 3

### Context
Escanor writes notes, appends to the journal and replaces notes. Asking "Allow?" for each one would
make note-taking tedious, especially by voice.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `write` level (no prompt) + copy the old version to `~/.escanor/brain-history/` before any replace or fact removal** | Fluid note-taking; nothing is ever lost | History folder grows (text, tiny) |
| B. Confirm every overwrite | Explicit | Constant prompts; voice-hostile |
| C. Git-commit the vault on each change | Full history, diffable | Needs git; slower; noisy history |

### Decision
A ([vault.py `_backup`](../../escanor/brain/vault.py)). Creating a note never overwrites one unless `overwrite=true`.

### Revisit if
You want a browsable history → C in Phase 8 (the plan already lists git backup of the brain).

---

## D18. Your own vault is trusted; attached folders are fenced as untrusted

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 3

### Context
`[brain] extra_dirs` can attach an existing Obsidian vault read-only. Such notes may contain pasted
web pages or shared documents.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Fence hits from attached folders inside the `brain_search` result** | Imported text can't instruct the model | Doesn't taint the turn for `remember_fact` (the gate keys on tool type, not content) |
| B. Treat all brain content as untrusted | Uniform | Every personal question would make `remember_fact` ask for confirmation |
| C. Split into two search tools | Taint works exactly | Model must choose between two near-identical tools |

### Decision
A ([tools.py `BrainSearchTool`](../../escanor/brain/tools.py)).

### Trade-offs accepted
- An attached note saying "remember X" is fenced as data but wouldn't trigger the confirmation rule. The fence instruction is the defence there.

---

## D19. Brain tools split: search/read/remember in core, editing in a `brain` group

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 3

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `brain_search`, `brain_read`, `remember_fact` always on; `brain_write`, `brain_append`, `brain_list`, `forget_fact` via `enable_tools("brain")`** | Personal questions and remembering work with no extra step; core stays at 10 tools | Writing a note costs one `enable_tools` call |
| B. All seven in core | No extra step | 14 core tools before music/assistant arrive |

### Decision
A. The skill runtime note tells skills like learn-anything to enable the group before saving.

---

## D20. facts.md is a plain bullet list, newest kept when over the prompt budget

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 3

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `- fact  _(added date)_` lines in Markdown; prompt gets the newest that fit in ~6000 chars** | You can edit it by hand; dated; bounded prompt cost | Oldest facts silently drop out of the prompt once you have hundreds (still searchable) |
| B. JSON store with categories | Structured | Not hand-editable; overkill now |

### Decision
A ([vault.py `facts_section`](../../escanor/brain/vault.py)). Facts change rarely, so the prompt cache survives most turns.

---

## D21. Speech plays through `afplay`, owned by one `AudioOut`

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 4

### Context
Replies, confirmations, timers and DJ patter all need the speaker, and must never talk over each
other. Speech should play even with no browser open (timers, DJ).

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. One `AudioOut` with a priority queue; `afplay` subprocess per sentence** | Built into macOS, no audio dependency; stop = kill the process | ~50 ms process start per sentence; macOS only |
| B. `sounddevice` stream | Gapless, cross-platform | Extra native dependency; device handling |
| C. Browser plays everything | Simple server | No speech without the page open |

### Decision
A ([audio_out.py](../../escanor/voice/audio_out.py)). The browser is only a sink when used from another device (`speech=browser`).

### Revisit if
Gaps between sentences are noticeable → B behind the same `Player` interface.

---

## D22. Energy-based voice detection instead of Silero

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 4

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. RMS above an adaptive noise floor** | No model, no dependency, testable with synthetic audio | Fooled by loud music/TV; less precise endpoints |
| B. Silero VAD | Robust to noise | Model download + onnx/torch dependency |

### Decision
A ([listen.py `EnergyVAD`](../../escanor/voice/listen.py)) behind a `VAD` protocol, so Silero can drop in.

### Revisit if
`escanor listen` cuts you off or never stops recording in a normal room.

---

## D23. Only replies to voice input are spoken (typed: opt-in)

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 4

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Speak replies to voice input; "speak typed replies" toggle for the rest** | Typing stays quiet; answers keep their full written form | One more toggle |
| B. Speak everything | Consistent | Noisy, and every reply gets the short spoken style |

### Decision
A. Spoken turns also get the spoken style section in the system prompt ([prompts.py `VOICE_STYLE`](../../escanor/core/prompts.py)).

---

## D24. Barge-in: always by mic/new message in the web UI; by voice only with headphones

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 4

### Context
Through laptop speakers the mic hears Escanor and would interrupt it constantly. The Phase 0 echo
test (still to run, needs you) decides whether software echo cancellation fixes that.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Web UI: pressing the mic or sending anything interrupts. `listen`: voice barge-in only with `[voice] barge_in = "headphones"`** | Never interrupts itself; explicit interrupt always works | No talking-over on speakers yet |
| B. Voice barge-in everywhere | Most natural | Self-interruption on speakers |

### Decision
A. The browser asks for `echoCancellation: true` anyway, so the web path is ready if the echo test passes.

---

## D25. Kokoro TTS deferred

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 4

### Context
The plan lists Kokoro as a higher-quality local voice. Piper (and Sarvam's Bulbul) already work and are verified.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Defer; Piper default** | No unverified code; Piper round-trip verified (0.6 s synth, exact Whisper transcript) | Piper sounds more robotic |
| B. Add an untested Kokoro engine | Better voice, maybe | Model download, unverified dependency chain |

### Decision
A. Add it as another `TTSEngine` when you want to compare voices (open question 2 in the plan).

---

## D26. Web UI on port 8770 (8765 is TradeMare's)

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 4

Your TradeMare web server already listens on 8765. Escanor defaults to 8770; change it with
`[server] port` or `escanor serve --port`.

---

## D27. Music tools are `write`, not `external`

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 5

### Context
The plan's permission table says "controlling apps" is `external` (always confirm). Applied to music,
"skip this" would need an "Allow?" every time.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `write`: run without asking** | A usable DJ and voice remote | A prompt-injected page could change your song (annoying, harmless, reversible) |
| B. `external` | Strictest | Unusable by voice |

### Decision
A ([music/tools.py](../../escanor/music/tools.py)). External stays for anything that leaves your Mac or can't be undone.

---

## D28. Search via ytmusicapi, playback via the desktop app

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 5

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. ytmusicapi for search/radio/history; th-ch app API for play/queue/volume** | Clean search results (videoId, title, artists, album); playback in your real Premium session | Two unofficial dependencies |
| B. th-ch `POST /search` only | One dependency | Returns YouTube's raw internal response; no history/likes |
| C. Stream audio ourselves (yt-dlp) | No app needed | Bypasses the official client; ToS risk; no Premium features |

### Decision
A. Verified live: ytmusicapi search and radio work without signing in.

---

## D29. DJ v1: pause → speak → play; the model picks from radio + likes

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 5

### Context
The plan's v1 DJ never talks over vocals. The next track has to come from somewhere sensible.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. ~20 s before the end, the model picks from YouTube Music's radio for the current song + your likes (no repeats), queues it "up next" and writes an intro; when it starts: pause, speak, play** | Reliable; YouTube's own recommendations as raw material; one cheap call per pick | A 2–4 s pause per introduced track |
| B. Radio-style talk-over with volume ducking | Smoother | Timing is fragile; talks over vocals |
| C. Let YouTube Music's autoplay choose | No model calls | No mood steering, nothing to introduce |

### Decision
A ([music/dj.py](../../escanor/music/dj.py)). Past the daily cap (`[music] daily_model_calls`) it keeps
choosing (first unplayed candidate) but stops talking. B remains the v2 item in the plan.

---

## D30. ytmusicapi browser-cookie login, not OAuth

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 5

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `ytmusicapi browser` (paste request headers once)** | No Google Cloud project | ytmusicapi marks it deprecated; cookies expire after days to weeks |
| B. `ytmusicapi oauth` | Longer-lived | Needs your own Google Cloud OAuth client id/secret |

### Decision
A, guided by `escanor music connect`. History and likes are optional: without them the DJ uses radio only,
and expiry shows up as a note instead of an error.

### Revisit if
Re-pasting headers gets tedious → set up B.

---

## D31. Reminders/Calendar through AppleScript, not EventKit

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 6

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `osascript` with quoted values and dates built from numbers** | No dependency; every script compile-checked with `osacompile`; works with any calendar account the Calendar app has | Slow with many calendars; repeating events not expanded |
| B. EventKit via pyobjc | Fast; recurrences expanded | Heavy dependency; entitlement prompts for a bare Python binary are flaky |
| C. Google Calendar API | Works off-Mac | OAuth setup; only Google calendars |

### Decision
A ([osa.py](../../escanor/assistant/osa.py), [apple.py](../../escanor/assistant/apple.py)). C stays the plan's "optional later".

### Revisit if
Weekly meetings missing from "what's on today?" becomes a real problem → B for reads.

---

## D32. `open_url` asks first; clipboard text is untrusted

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 6

### Context
A URL can carry data out in its query string ("open https://evil.test/?notes=…"), and the clipboard holds
whatever you last copied, including web text.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `open_url` = external (confirm); `open_app`, `notify` = write; clipboard = read + untrusted** | Closes the exfiltration path; clipboard text can't instruct the model | One "Allow?" per link |
| B. All `write` | Smoother | A prompt-injected page could send your notes to a server via a link |

### Decision
A.

---

## D33. Timers live in the running process

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 6

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. asyncio tasks; announce by notification + speech + CLI print** | Simple; spoken through AudioOut at TIMER priority | Lost if Escanor stops |
| B. Persist and reschedule on start | Survives restarts | A store and catch-up logic for something Reminders already does |

### Decision
A ([timers.py](../../escanor/assistant/timers.py)). The tool description steers anything important to a reminder.

---

## D34. The morning briefing is a tool the model summarises

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 6

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `morning_briefing` (core) gathers sources; the model writes the summary** | Works by voice ("good morning"), text, or `escanor brief`; sources pluggable (Phase 7 adds reviews due) | One model turn per briefing |
| B. A fixed template printed by the CLI | No model call | Robotic; not available by voice |

### Decision
A ([briefing.py](../../escanor/assistant/briefing.py)). Each source fails independently.

---

## D35. Ingested sources stay untrusted inside the brain

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 7

### Context
Papers and articles are saved to `research/<slug>/source.md` so they're searchable. Your own notes are
trusted (D18), so a saved web page would otherwise "launder" into trusted text on its next read.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Treat `research/*/source.md` as external copies: fenced by `brain_read`, `brain_search` and `research_read`** | Searchable and safe | A path convention to remember |
| B. Store sources outside the vault | Clear separation | Not searchable with your notes |

### Decision
A ([brain/tools.py `is_external_copy`](../../escanor/brain/tools.py)).

---

## D36. Local files only from allowed folders

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 7

The model picks the path for `research_ingest`, so it may only read PDF/Markdown/text/HTML files in
`[research] allowed_dirs` (default Downloads, Documents, Desktop). `..` tricks are resolved before checking.
Alternatives: any path (would expose `~/.ssh`), or a confirmation per file (tedious for "explain the PDF I
just downloaded").

---

## D37. Explaining is learn-anything's job; Escanor supplies ingest, paging and reviews

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 7

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Tools fetch/save/page/track reviews; the `learn-anything` skill does the teaching** | Reuses your skill as-is; its log lands in the brain | Depends on the model following a long skill |
| B. A built-in "explain" pipeline (summary → sections → quiz) | Predictable | Duplicates the skill; less Socratic |

### Decision
A. arXiv titles come from arXiv's metadata API (the PDF's first line is often a licence notice, B14).

---

## D38. Collapse tool history only when the provider changes

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 8 · Refines D4

### Context
With escalation (D39), Haiku and Sonnet alternate within one conversation. D4 collapsed every other
*model's* tool turns into text, so each "think hard" would have rewritten history for no reason: Claude
reads another Claude model's tool turns natively (it just ignores the other model's thinking).

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Compare providers, not models** | No rewrites within Claude; Gemini/OpenAI/Ollama switches still collapse | Two OpenAI-compatible providers (gpt vs gemini) still collapse, correctly |
| B. Keep D4 as is | Simplest | History churn on every escalation |

### Decision
A ([session.py `collapse_tool_history`](../../escanor/core/session.py)).

---

## D39. Escalate to Sonnet on "think hard" or after 4 tool steps, then hand back

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 8

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Phrase trigger for the whole turn; step trigger mid-turn (same provider only); back to Haiku after** | Cheap by default, strong when it matters; you see `ModelSwitched` | Phrase list is heuristic |
| B. A classifier call per turn to decide | Smarter routing | An extra call on every turn |
| C. No escalation | Simplest | Haiku fumbles long tool chains |

### Decision
A ([router.py](../../escanor/core/router.py)); `[router] escalate_to = ""` turns it off.

---

## D40. Daily spend cap from telemetry, priced conservatively

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 8

The agent checks today's spend (telemetry tokens × list prices) before every model call and stops with
a clear message at `[budget] daily_usd` (default $3). Cache reads are priced as full input, so it
over-estimates, which is the safe direction. Unpriced models count as $0 unless you add
`[budget.prices]`. Alternative: provider-side limits in each console. Those are coarser, and they don't
cover the unattended DJ per day.

---

## D41. Evals grade tool choice with stubbed tools

**Status:** Accepted · **Date:** 2026-10-17 · **Phase:** 8

`escanor eval` runs ten prompts (one per capability) against any model and passes a case when the
expected tool is called. Every tool call gets a stub answer, so evals can't write notes, create
reminders or play music, and grading needs no judge model. Answer quality isn't graded; that needs
a judge or human review, deferred.

---

## D42. MCP tools: permission from the server's read-only hint, otherwise ask

**Status:** Accepted · **Date:** 2026-10-18 · **Feature:** MCP connectors

### Context
An MCP server can expose anything: "search mail" and "send mail" look alike to Escanor. The protocol
lets servers annotate tools (`readOnlyHint`, `destructiveHint`).

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `readOnlyHint` → read; your `auto_approve` globs → write; everything else → external (ask)** | Safe by default; usable once you allow the harmless ones | Trusts the server's own annotation |
| B. Everything external | Strictest | An "Allow?" for every search |
| C. Everything write | Smooth | A prompt-injected email could make Escanor send mail |

### Decision
A ([connectors/mcp_client.py](../../escanor/connectors/mcp_client.py)). All MCP output is untrusted.
Only configure servers you trust, since their annotations decide what runs unasked.

---

## D43. Async start/stop hooks on the app for long-lived connections

**Status:** Accepted · **Date:** 2026-10-18

MCP sessions and the proactive loop live in the event loop, and the MCP SDK requires opening and
closing in the same task. `Escanor.on_start` / `on_stop` hooks are awaited by each interface (chat
REPL, `serve`, `listen`) from its main task. Alternative: connect lazily on first use. Rejected:
tool schemas must be known before the model's first request.

---

## D44. Proactive checks are local and rule-based, no model calls

**Status:** Accepted · **Date:** 2026-10-18 · **Feature:** proactive mode

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Fixed checks (meeting lead time, evening wrap, idle-timer breaks) with local brain search for context** | Free, predictable, works with no key; easy to reason about | Template wording |
| B. Ask the model every few minutes "anything worth saying?" | Clever, flexible | Costs money all day; unpredictable interruptions |

### Decision
A ([assistant/proactive.py](../../escanor/assistant/proactive.py)): quiet hours, an hourly cap,
announce-once keys and `proactive_pause`. Breaks use macOS's HID idle time, not keystroke logging.
Tests switch it off through config so they never touch the real Calendar (B17).

---

## D45. Screenshots ask first and stay in history as files

**Status:** Accepted · **Date:** 2026-10-18 · **Feature:** look_at_screen

### Context
A screenshot can show anything (bank pages, messages) and goes to the cloud model. Old screenshots
in the history are re-sent on later calls (cost). Editing earlier turns to drop them would break
Claude's preserved thinking.

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `external` (ask each time, naming the model); shrink to ~1568 px JPEG; keep the path in history; files pruned to the newest 30** | You see every capture; small DB; history never edited | Later turns in that conversation keep paying for the image (prompt caching softens it) |
| B. Strip images from earlier turns | Cheaper | Edits history (preserved-thinking violation) |
| C. Read-level, no prompt | Fastest by voice | Silent screen capture is too sensitive |

### Decision
A ([assistant/screen.py](../../escanor/assistant/screen.py)). `/new` starts a clean conversation if cost matters.

---

## D46. Images reach non-Claude models as a follow-up user message

**Status:** Accepted · **Date:** 2026-10-18

Claude accepts image blocks inside a tool result; OpenAI-style and Ollama tool messages are text-only.
For those, the images follow the tool results as one user message ("Image(s) returned by the tool call
above"). Verified live on Gemini: it answered "Red" for a generated red image.

---

## D47. DJ v2 talks over the song's opening; v1 pause kept as an option

**Status:** Accepted · **Date:** 2026-10-18 · Supersedes the v1 part of D29

Radio DJs talk over the first seconds of a track. With the intro synthesised while the previous song is
still playing (`AudioOut.prefetch`), the voice starts the moment the track does, and AudioOut's ducker
dips the music under it automatically. The pause → speak → play behaviour stays available as
`[music] dj_style = "pause"` for intros that would clash with vocals. No mood → one from the time of day.

---

## D48. Shortcuts: always ask, except names you allow

**Status:** Accepted · **Date:** 2026-10-18

A Shortcut can do anything (send messages, change settings, unlock doors), so `shortcut_run` is external.
Names you list in `[assistant] shortcuts_allowed` run without asking, via a per-call
`Tool.confirmation_needed(arguments)` hook the permission gate now consults. Names are matched against
your real shortcuts, so a made-up name never reaches `shortcuts run`.

---

## D49. Menubar polls a small local REST API

**Status:** Accepted · **Date:** 2026-10-18

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. `rumps` app polling `/api/status` every second; POSTs for interrupt/quiet/model** | Tiny; works with any running `escanor serve`; can start the server itself | Up to 1 s lag on the icon |
| B. Run the whole assistant inside the menubar process | One process | The menubar would own the event loop, voice and models; much bigger change |
| C. Swift menubar app | Native | A second language and build in the repo |

### Decision
A ([menubar.py](../../escanor/menubar.py)); `escanor service install --menubar` starts it at login.

---

## D50. Same-origin check on the WebSocket and control API

**Status:** Accepted · **Date:** 2026-10-18 · Fixes B22

Binding to 127.0.0.1 keeps other machines out, but not other *websites*: a browser lets any page connect
to localhost. The WebSocket and every non-GET request now require either no `Origin` (the menubar, curl)
or Escanor's own origin ([server/app.py `origin_allowed`](../../escanor/server/app.py)).

---

## D51. Hotkeys live in the menubar; the server records and answers

**Status:** Accepted · **Date:** 2026-10-19

### Options considered

| Option | Pros | Cons |
|---|---|---|
| **A. Carbon `RegisterEventHotKey` in the menubar; `/api/talk/start|stop` and `/api/explain` on the server** | No Accessibility/Input Monitoring permission for the keys; key down *and* up (push-to-talk); the server already has Whisper, the agent and the speaker | Needs the menubar running; ctypes into a deprecated (but still supported) API |
| B. `pynput` keyboard listener inside `escanor listen` | Pure Python | Watches every keystroke: needs Input Monitoring; only works while `listen` runs |
| C. Browser keyboard shortcuts in the HUD | No native code | Only when the HUD tab is focused — not "anywhere" |

### Decision
A ([hotkeys.py](../../escanor/hotkeys.py), [menubar.py](../../escanor/menubar.py), [server/app.py](../../escanor/server/app.py)).
Defaults ⌃⌥Space / ⌃⌥E: macOS 15+ refuses global keys whose only modifiers are ⌥ or ⌥⇧.

### Trade-offs accepted
Hotkeys need `escanor menubar` (which also starts the server). A tap of the talk key just stops Escanor talking.

---

## D52. One shared Session per conversation (the hub)

**Status:** Accepted · **Date:** 2026-10-19

Hotkey turns run on the latest conversation, which the HUD also shows. Two Session objects for one
conversation would each append and overwrite the other's messages, so [hub.py](../../escanor/server/hub.py)
keeps one object per conversation, sends every turn's events to all pages showing it, and allows one turn per
conversation (a new one cancels the old — the same rule as typing over Escanor). A page's own turn stops when the
page closes; a hotkey turn has no owner and finishes.

---

## D53. Explain = press ⌘C for you, restore the clipboard

**Status:** Accepted · **Date:** 2026-10-19

| Option | Pros | Cons |
|---|---|---|
| **A. Post ⌘C (CGEvent), read the pasteboard, put the old contents back** | Works in every app, Finder files too | Needs Accessibility; until granted, falls back to "copy first" |
| B. Accessibility API `AXSelectedText` | No clipboard touch | Many apps (browsers, Electron, PDFs) don't expose it |
| C. Only explain what's already on the clipboard | No permission | An extra ⌘C every time |

**Decision:** A, with C as the fallback ([selection.py](../../escanor/selection.py)). The selection is fenced as
untrusted and taints the turn (D55).

---

## D54. Phone access through Tailscale + a pairing token

**Status:** Accepted · **Date:** 2026-10-19

| Option | Pros | Cons |
|---|---|---|
| **A. Keep binding 127.0.0.1; `tailscale serve` proxies HTTPS; non-local requests need a token (HttpOnly cookie)** | Real HTTPS cert (phones only allow the mic on secure pages); works away from home; nothing on the LAN | Needs Tailscale on Mac and phone |
| B. Bind 0.0.0.0 on the LAN with a token | No extra app | Plain HTTP: no microphone on the phone; home Wi-Fi only; exposed to everyone on the network |
| C. Self-signed HTTPS on the LAN | No extra app | Certificate warnings; iOS needs a profile installed |

**Decision:** A ([remote.py](../../escanor/server/remote.py), `escanor pair`). "Local" = localhost Host *and* no proxy
headers, so proxied and DNS-rebinding requests both need the token (B23). A paired phone gets the page and the
WebSocket, not the Mac's controls (mic, hotkeys, menubar API). The PWA manifest's start URL carries the pairing
because an iOS home-screen app keeps its own cookies apart from Safari. No token file = remote access off.

---

## D55. Fenced text in the user's message taints the turn; tainted turns ask for allowed shortcuts too

**Status:** Accepted · **Date:** 2026-10-19 · Fixes B24

`turn_read_untrusted` also returns True when the latest user message carries `<untrusted …>` (the explain
hotkey's selection). In a tainted turn, external tools ask even if `shortcuts_allowed` would skip the question.

---

## D56. A fuller Pride persona: one constant character, five solar phases

**Status:** Accepted · **Date:** 2026-10-19

The old persona only changed tone (proud by day, humble at night). Now a constant character sheet (loyal and
protective under the arrogance, earned pride so never bluffing, "Who decided that?" when the user doubts
themselves, sun/lion/Rhitta imagery at most once a reply, no flourishes in errors or sad topics) plus five
phases: dawn 06:00, day 09:00, noon 11:30–12:30, afternoon, night 18:00 ([persona.py](../../escanor/core/persona.py)).
Fixed spoken lines (confirm, error, not heard) and the DJ's voice come from the same module. Five prompt
changes a day still leave Claude's cache intact for almost every turn. Checked live on Gemini at 12:00, 15:00 and 22:00.

---

## D57. A heavy voice by effects, not by cloning

**Status:** Accepted · **Date:** 2026-10-19

The user asked for Escanor's actual voice. Cloning a voice actor from clips without consent was declined.
Instead [effects.py](../../escanor/voice/effects.py) makes Piper's voice heavy: resampled pitch (formants drop too,
so it sounds larger), a low-end boost, soft saturation and a short dark hall, ~40 ms a sentence in numpy. Piper
speaks faster by the pitch factor to keep the pace. `effect = "sun"` (default) follows the persona's phases:
heaviest at noon, lightest at night.

---

## D58. Free providers (Groq, OpenRouter) and failover on rate limits

**Status:** Accepted · **Date:** 2026-10-20

Groq (`openai/gpt-oss-120b`, `qwen/qwen3-32b`) and OpenRouter (`openrouter/free`) reuse `OpenAIModel` with their own
base URLs ([config.COMPATIBLE](../../escanor/config.py), [registry.py](../../escanor/models/registry.py)). Cerebras was
skipped: its permanent free tier reportedly ended in July 2026.

[failover.py](../../escanor/core/failover.py): a limit-type error (429, quota, overloaded) **before any reply text
streamed** marks the model resting (15 min; 3 h for a daily quota) and retries the same step on the next keyed model in
`[models] fallbacks`, collapsing tool history for the new provider. Other errors still end the turn (they'd fail
everywhere). The agent stays on the fallback and switches back to the user's model at the start of the first turn after
it has rested. `set_model` (the user's choice) clears the failover; escalation and failover use `_use`.

**Trade-off:** a different model may answer mid-conversation; the switch is announced as ModelSwitched.

