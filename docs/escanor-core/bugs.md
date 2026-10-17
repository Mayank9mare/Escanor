# Escanor core — Bugs

Every bug found while building the core: during coding, tests, review passes, or by you.
Fixed bugs stay here (struck through) as an audit trail. IDs never get reused.

## Summary

| ID | Severity | Bug | Status | Found | Fixed in |
|---|---|---|---|---|---|
| ~~B1~~ | ~~MEDIUM~~ | ~~Parallel tool results sent as separate user messages~~ | ✅ FIXED | step 1 · reading copied code | step 1 |
| ~~B2~~ | ~~HIGH~~ | ~~Claude API errors crashed callers instead of becoming `RuntimeError`~~ | ✅ FIXED | step 1 · reading copied code | step 1 |
| ~~B3~~ | ~~HIGH~~ | ~~Telemetry wrapper would silently turn off streaming~~ | ✅ FIXED | step 1 · design | step 1 |
| ~~B4~~ | ~~HIGH~~ | ~~Claude thinking blocks dropped between tool calls~~ | ✅ FIXED | step 1 · reading copied code | step 1 |
| ~~B5~~ | ~~LOW~~ | ~~OpenAI/Gemini streaming reports no token usage~~ | ✅ FIXED (OpenAI) | step 1 | Phase 8 |
| B6 | LOW | A cancelled turn keeps its provider stream open until the next chunk | ⏸️ DEFERRED | step 2 | Phase 4 (see D2) |
| ~~B7~~ | ~~LOW~~ | ~~CLI test could start a real REPL if a `.env` had a key~~ | ✅ FIXED | step 5 · test review | step 5 |
| ~~B8~~ | ~~MEDIUM~~ | ~~A cancelled confirmation swallowed the next line you typed~~ | ✅ FIXED | review pass 1 (Phase 2) | step 9 |
| ~~B9~~ | ~~LOW~~ | ~~Telemetry stores the whole conversation on every call~~ | ✅ FIXED | review pass 1 | Phase 8 |
| B10 | LOW | Collapsing history on a model switch resets message timestamps | 🔴 OPEN | review pass 1 | — |
| ~~B11~~ | ~~MEDIUM~~ | ~~Speech chunker split sentences inside code blocks~~ | ✅ FIXED | Phase 4 · test | Phase 4 |
| ~~B12~~ | ~~HIGH~~ | ~~`escanor serve` rejected every WebSocket connection~~ | ✅ FIXED | Phase 4 · test | Phase 4 |
| ~~B13~~ | ~~HIGH~~ | ~~No WebSocket support in plain uvicorn~~ | ✅ FIXED | Phase 4 · live browser check | Phase 4 |
| ~~B14~~ | ~~LOW~~ | ~~arXiv papers saved under their licence notice instead of their title~~ | ✅ FIXED | Phase 7 · live ingest | Phase 7 |
| ~~B15~~ | ~~MEDIUM~~ | ~~`listen` heard its own chime as speech and ended the turn early~~ | ✅ FIXED | final review pass | Phase 8 |

Severity: **HIGH** = data loss, wrong result, security, blocks testing/release · **MEDIUM** =
breaks an edge case or error path · **LOW** = minor or cosmetic.

---

## 🔴 Open

### B10. Collapsing history on a model switch resets message timestamps

- **Severity:** LOW · **Found:** 2026-10-17, review pass 1 (Phase 2)
- **Where:** [session.py `_replace_all`](../../escanor/core/session.py#L107)
- **Symptom:** after a model switch that collapses tool turns, every message's `created_at` becomes the switch time.
- **Root cause:** the rewrite deletes and re-inserts rows; message dicts don't carry their own time.
- **Proposed fix:** keep `created_at` in each message dict on append and write it back on replace.
- **Flow impact:** none on behaviour; matters once the brain (Phase 3) indexes conversations by date.

---

## ✅ Fixed

### ~~B1. Parallel tool results sent as separate user messages~~

- **Severity:** MEDIUM · **Found:** 2026-10-17, step 1, reading kmare-code's provider · **Fixed:** step 1
- **Root cause:** kmare-code turned every `tool` message into its own Claude `user` message. When
  Claude calls several tools at once, all results belong in one user message; splitting them
  teaches the model to stop calling tools in parallel.
- **Fix:** consecutive tool results are merged, in [anthropic_provider.py#L139-L150](../../escanor/models/anthropic_provider.py#L139-L150).
- **Regression test:** [test_should_merge_parallel_tool_results_into_one_user_message](../../tests/test_providers.py#L39)

### ~~B2. Claude API errors crashed callers instead of becoming `RuntimeError`~~

- **Severity:** HIGH · **Found:** 2026-10-17, step 1 · **Fixed:** step 1
- **Root cause:** the other providers wrap failures in `RuntimeError` (what the CLI catches); the
  copied Anthropic provider let `anthropic.APIError` escape, so a rate limit or network blip would
  have crashed the REPL.
- **Fix:** [anthropic_provider.py#L47-L67](../../escanor/models/anthropic_provider.py#L47-L67).
- **Regression test:** [test_should_raise_runtime_error_when_claude_api_fails](../../tests/test_providers.py#L115)

### ~~B3. Telemetry wrapper would silently turn off streaming~~

- **Severity:** HIGH · **Found:** 2026-10-17, step 1, while adding `chat_stream_tools` · **Fixed:** step 1
- **Root cause:** every model is wrapped in `InstrumentedModel`. Without its own
  `chat_stream_tools`, the base-class fallback would call `chat()` and stream the whole reply as one
  chunk, so voice would wait for the full answer.
- **Fix:** forward it explicitly in [instrumented.py](../../escanor/models/instrumented.py) (`chat_stream_tools`).
- **Regression test:** [test_should_stream_events_through_instrumented_wrapper](../../tests/test_agent.py#L194)

### ~~B4. Claude thinking blocks dropped between tool calls~~

- **Severity:** HIGH · **Found:** 2026-10-17, step 1 · **Fixed:** step 1
- **Root cause:** kmare-code rebuilt assistant turns from text + tool calls only. Haiku 5.5 thinks
  by default, and its thinking blocks must be sent back unchanged in a tool loop.
- **Fix:** keep `raw` blocks and replay them for the same model ([D3](decision.md#d3-replay-claudes-exact-reply-blocks-including-thinking-for-same-model-turns)).
- **Regression tests:** [test_should_replay_raw_blocks_when_turn_came_from_same_model](../../tests/test_providers.py#L55),
  [test_should_keep_thinking_and_tool_use_in_raw_when_parsing_reply](../../tests/test_providers.py#L76)

### ~~B7. CLI test could start a real REPL if a `.env` had a key~~

- **Severity:** LOW · **Found:** 2026-10-17, step 5, test review · **Fixed:** step 5
- **Root cause:** `main([])` loads `.env` from the working directory; with a real key it would build a
  real model and block on stdin.
- **Fix / regression test:** the test stubs `load_dotenv` ([test_cli.py](../../tests/test_cli.py)).

### ~~B8. A cancelled confirmation swallowed the next line you typed~~

- **Severity:** MEDIUM · **Found:** 2026-10-17, review pass 1 at the Phase 2 boundary · **Fixed:** step 9
- **Root cause:** the CLI read keyboard input with `input()` on a worker thread. Ctrl-C during an
  "Allow? [y/N]" question cancelled the turn but not the thread, which kept waiting and took the next
  line you typed as its answer, so that message never reached Escanor.
- **Fix:** one event-loop stdin reader (`add_reader` + `os.read`) shared by the prompt and confirmations;
  a cancelled wait just stops waiting. [cli.py `StdinLines`](../../escanor/cli.py#L49).
- **Regression test:** [test_should_deliver_next_line_to_prompt_when_confirmation_cancelled](../../tests/test_cli.py#L55)

### ~~B11. Speech chunker split sentences inside code blocks~~

- **Severity:** MEDIUM · **Found:** Phase 4, by test · **Fixed:** Phase 4
- **Root cause:** once a code fence closed, the scan for sentence ends also looked inside the fenced
  code, so `x = 1.` ended a "sentence" and code was read aloud.
- **Fix:** skip positions inside complete fences ([chunker.py `_fenced_spans`](../../escanor/voice/chunker.py)).
- **Regression test:** [test_should_wait_for_code_block_to_close_and_not_read_code](../../tests/test_voice.py)

### ~~B12. `escanor serve` rejected every WebSocket connection~~

- **Severity:** HIGH · **Found:** Phase 4, by test · **Fixed:** Phase 4
- **Root cause:** with `from __future__ import annotations`, FastAPI resolves `ws: WebSocket` by name in
  module globals; `WebSocket` was imported inside `create_server()`, so FastAPI saw an unknown type and
  treated `ws` as a required query parameter (close code 1008 "Field required").
- **Fix:** module-level FastAPI imports ([server/app.py](../../escanor/server/app.py)).
- **Regression tests:** every test in [test_server.py](../../tests/test_server.py) connects a WebSocket.

### ~~B13. No WebSocket support in plain uvicorn~~

- **Severity:** HIGH · **Found:** Phase 4, live check in the browser pane · **Fixed:** Phase 4
- **Root cause:** `uvicorn` without extras has no WebSocket library ("Unsupported upgrade request");
  the FastAPI test client bypasses uvicorn, so tests couldn't see it.
- **Fix:** depend on `uvicorn[standard]` ([pyproject.toml](../../pyproject.toml)). Verified live: message → streamed reply in the page.

### ~~B14. arXiv papers saved under their licence notice instead of their title~~

- **Severity:** LOW · **Found:** Phase 7, live ingest of 1706.03762 · **Fixed:** Phase 7
- **Root cause:** the PDF has no title metadata and its first text line is Google's reproduction notice.
- **Fix:** take the title from arXiv's metadata API ([ingest.py `arxiv_title`](../../escanor/research/ingest.py)); verified live → "Attention Is All You Need".
- **Regression test:** [test_should_fetch_arxiv_pdf_and_title_from_metadata](../../tests/test_research.py)

### ~~B5. OpenAI/Gemini streaming reports no token usage~~

- **Severity:** LOW · **Found:** step 1 · **Fixed:** Phase 8 (OpenAI only)
- **Fix:** `stream_options={"include_usage": true}` on OpenAI's own endpoint and read the final usage chunk
  ([openai_provider.py](../../escanor/models/openai_provider.py)). Gemini's compatible endpoint is left
  without it until verified, so Gemini streams still show no tokens (and count $0 toward the cap).
- **Regression test:** [test_openai_stream_reports_usage_when_official_endpoint](../../tests/test_polish.py)

### ~~B9. Telemetry stores the whole conversation on every call~~

- **Severity:** LOW · **Found:** review pass 1 · **Fixed:** Phase 8
- **Fix:** telemetry keeps the message count and the newest message (≤2000 chars) only
  ([instrumented.py `_request_summary`](../../escanor/models/instrumented.py)).
- **Regression test:** [test_telemetry_keeps_only_newest_message](../../tests/test_polish.py)

### ~~B15. `listen` heard its own chime as speech and ended the turn early~~

- **Severity:** MEDIUM · **Found:** final review pass · **Fixed:** Phase 8
- **Root cause:** the wake chime plays right before recording; the mic picks it up as the first "speech",
  then 0.8 s of silence before you start talking ends the utterance → "Didn't catch anything".
- **Fix:** wait for the chime, drain the mic, then record ([listen.py `ListenLoop.once`](../../escanor/voice/listen.py)).
- **Regression test:** [test_listen_loop_drops_chime_audio_before_recording](../../tests/test_voice.py)

---

## ⏸️ Deferred / 🚫 Won't fix

### B6. A cancelled turn keeps its provider stream open until the next chunk

- **Why not now:** harmless in the CLI (the reply is discarded); matters for barge-in latency in
  Phase 4. Trade-off recorded in [D2](decision.md#d2-async-agent-loop-blocking-providers-and-tools-run-on-worker-threads).

