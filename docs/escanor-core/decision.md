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
| D4 | Collapse other models' tool turns to text on a model switch | Accepted | 2026-10-17 |
| D5 | Build streaming-with-tools in Phase 1, not Phase 2 | Accepted | 2026-10-17 |
| D6 | No `eager_input_streaming` on Claude tools | Accepted | 2026-10-17 |
| D7 | Default Claude effort `low` | **Proposed** (your call) | 2026-10-17 |
| D8 | GPT/Gemini default ids `gpt-5` / `gemini-3.6-flash` | **Proposed** (your call) | 2026-10-17 |
| D9 | No clock time in the system prompt | Accepted | 2026-10-17 |
| D10 | Store each message as one JSON body in SQLite | Accepted | 2026-10-17 |
| D11 | Ollama: hold back text that starts like a JSON tool call | Accepted | 2026-10-17 |

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

**Status:** Accepted · **Date:** 2026-10-17 · **Step:** 2

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
