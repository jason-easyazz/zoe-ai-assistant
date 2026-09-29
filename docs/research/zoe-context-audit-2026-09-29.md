# Context-engineering audit: Zoe's current setup against the 2026-09-29 research gap list

Scope: code-level audit of `/home/zoe/assistant` at `29b7a226` (= `origin/main`), read-only, against
`docs/research/samantha-context-engineering-2026-09-29.md` §4. Live facts come from `services/zoe-data/.env`
(ZOE_* names and values only), the zoe-data app log (`~/.zoe-logs/zoe-data.app.log*`, 2026-09-02 → today),
the dreaming log (`~/training/logs/dreaming-systemd.log`), the Flue session store (row counts only) and
Postgres (schemas, row counts, lengths only). No household content was read or reproduced.

## 1. Summary

- **Live lane.** Voice uses **Flue** (`ZOE_BRAIN_BACKEND=flue`, `:3579`, wire 2, streaming). Telegram and web chat reach the same `brain_dispatch` → Flue through `/api/chat`, with one exception: on chat, memory-domain turns that the router scores ≥ threshold go to `expert_dispatch.memory_expert`, which calls the **core** lane (`run_zoe_core_streaming`). A Flue-only context change therefore misses those turns.
- **Already mostly there:**
  - #2 ordering. The Flue prefix is byte-stable, and every volatile block rides in the *last* user message.
  - #4 supersede/validity for explicit corrections.
  - #6 recall as a tool. `recall_memory` is always visible.
- **Small tweak:**
  - #1 user-model block. The content exists as a weekly portrait of about 370 tokens that stays byte-stable for 7 days, but it never reaches the live brain.
  - #3 evidence capture. The `source_excerpt` field exists but the main writers drop it.
  - #6 date-scoped recall.
- **Build:**
  - #5 salience/cooldown selector.
  - #7 ask-to-remember.
  - #8 side models, which have no usage signal logged yet.
  - #9 LoRA.
- **Two defects the research did not anticipate:**
  1. Open-loop extraction has been **broken**. `memory_digest._extract_open_loops` imports a non-existent `zoe_agent._llm_chat`, which produced 133 logged failures. The `open_loops` table has **0 rows**, so the morning brief, the open-loop items in PR #1781's `[Today]` block, and Samantha bar S5 have nothing to carry.
  2. On the Flue lane, injected seam blocks are **persisted into session history** and replayed on later turns. There are 193 stored `[MEMORY CONTEXT` blocks and 116 `[PENDING CONTACT OFFER` directives. The core lane elides these; Flue does not.

## 2. Prompt order per lane

Token figures are calibrated against llama-server's own accounting: about 12.4k static characters reuse about 2.65k cached tokens, i.e. ≈4.5 chars/token. **S** = byte-stable across turns. **A** = append-only within a session. **V** = varies per turn.

### Flue lane (LIVE)

Assembly points:
- System and tools: `labs/flue-zoe-brain-2x/src/agents/zoe.ts` (`Zoe()`).
- Wire policy: `src/providers/capped-completions.ts` (`applyPolicies`).
- Tail: `services/zoe-data/zoe_flue_client.py` (`_run_flue_brain_streaming_turn`).

```
[S]  system  ZOE_INSTRUCTIONS = SOUL + 9 doctrines ........ 9,326 chars ≈ 2.1k tok (all users identical)
[S*] tools   core: get_time, recall_memory, activate_abilities   3,049 chars ≈ 0.7k tok
             + session-sticky groups (append-only; all 21 = 21,317 chars)
[A]  history durable Flue session, windowed (8192 − 1536 reserve) in whole user-turn blocks
             (context-window.ts windowContextToBudget; head quantised for cache stability)
             !! stored user messages still CONTAIN earlier seam blocks (see §3 #2)
---------------------------------------- cache boundary in practice ----------------------------
[V]  last user message:
       zoe-uid line (stripped in applyPolicies)
       [MEMORY CONTEXT] recall floor   ≤1,600 chars / 12 bullets (ZOE_SEAM_RECALL_INJECT=true; my/I + event questions)
       [PENDING CONTACT OFFER]         1–2 lines (ZOE_SEAM_OFFER_INJECT=1; not on continuity turns)
       <user words>
       [MEMORY CONTEXT] continuity     ≤1,600 incl. portrait ≤240 (ZOE_SEAM_CONTINUITY_INJECT default ON; mood statements)
       [Today]                         PR #1781 — OPEN, not on main; ZOE_BRIEF_ON_FIRST_TURN default OFF
```

Measured over 510 live turns from `FLUE_PROMPT_CACHE` logs:
- First-round prompt: p50 2,851, p90 3,308, max 4,025 tokens.
- Tokens reused from cache: p50 2,653.
- Tokens re-prefilled: p50 112, p90 606.
- 22% of turns re-prefill ≥500 tokens.
- 1.19 model rounds per turn.

The 8k window is mostly empty: p90 uses about half of the 6.6k prompt budget. **Context rot is not today's problem. Missing identity context is.**

Seam block sizes from logs:
- Continuity: n=56, mean 1,037 chars (≈230 tok), max 1,582.
- Recall: n=6, mean 264 chars.

Every matched block in the window came from synthetic (demo/test) users. Real users logged 20 continuity *misses* and 0 hits.

### Core lane (dormant as brain; still serves chat `memory_expert`)

Assembly: `zoe_core_client._compose_message` plus `services/zoe-core/extensions/memory.ts`.
- System: SOUL plus the static `MEMORY_USAGE_DIRECTIVE`. It is byte-stable, pinned by `test/prefix_stability.test.ts`.
- Tools: disclosure in `abilities.ts`.
- History: Pi retains it, and `memory.ts` elides all but the newest copy of each `CONTEXT_BLOCKS` pair.
- Last message, in order: `[voice brevity]` → `[About you]` portrait (≤600) → `[What you remember]` → `[MEMORY CONTEXT]` → `[Recent conversation]` (history[-12:]) → utterance marker → message.

The core lane injects the portrait on every turn, but in the volatile tail.

### llama-server

`scripts/setup/systemd/llama-server.service`:
- `--ctx-size 8192 --parallel 1`: one slot, because draft-MTP is unsafe with >1 slot.
- `--cache-ram 2048`: the host prompt cache keeps prefixes warm across requests.
- The Flue request shape is cache-friendly by design: no per-round system-prompt mutation, and a wrap-up note moved into a trailing user message (`applyCap`).

## 3. Gap table

| # | What exists (file:function, evidence) | Distance from target | Smallest tweak | Effort | Risk | Verification |
|---|---|---|---|---|---|---|
| 1 User-model block | `user_portrait.run_portrait_synthesis` runs weekly (Sunday, `memory_digest.run_dreaming_cycle` phase 4) and writes to `user_portraits`. Jason's portrait: **1,657 chars ≈370 tok**, v21, last generated 2026-09-27, so byte-stable for 7 days. `load_portrait` caps it at 600. On Flue it only enters via `_continuity_context_block` → `_portrait_line` (240 chars), and 0 real-user continuity hits were logged, so **in practice the live brain never sees the portrait**. The Flue system prompt contains no per-user content. | Content ≈ at budget. The placement is missing entirely. The format is prose rather than a sectioned "peer card". | Serve a bounded (≤1,400 chars) per-user block from zoe-data. In the sidecar, cache it per (user, portrait_version) and append it as a **suffix to `systemPrompt`** in `applyPolicies` before windowing. It stays byte-stable until the next Sunday. Add one doctrine line reconciling it with `PERSONAL_RECALL_DOCTRINE` ("never answer about this person from your own head"). | M | Voice path (`labs/flue-zoe-brain-2x/src/*` is replay-gated). Needs a flag. Cost is ~0.6 s extra prefill once per user per week or cache eviction. The prefill is larger if tools render after system text (see §5). | Samantha bar with the flag off vs on (ablation); `parity/recall_reliability.py` (recall_memory call rate must not drop); `FLUE_PROMPT_CACHE first_prompt_n`; voice replay probe |
| 2 Stable prefix → volatile tail | The order already matches the research: static system and tools, then history, then all volatile blocks in the last user message next to the query. Per-block caps: `_RECALL_MAX_CHARS=1600`, 12 bullets, portrait 240. Logs: `SEAM_RECALL … chars=`, `SEAM_CONTINUITY … chars=`, `FLUE_PROMPT_CACHE`. | (a) Old seam blocks persist in Flue history. `applyPolicies` strips only the identity, replay and speculative envelopes; there is no port of `memory.ts` elision. (b) No single per-turn budget line; history size is never logged. | Elide `[MEMORY CONTEXT]…`, `[PENDING CONTACT OFFER]…` and `[Today]…` from every user message **except the last** in `applyPolicies`. Emit one `CONTEXT_BUDGET system/tools/history/tail` estimate on the `done` terminal. | S–M | Voice path. Eliding blocks from turn N−1 re-prefills the last exchange once (~100–300 tok ≈0.2–0.5 s), and only after turns that carried a block. | New sidecar unit test (history bytes, last message untouched); `first_prompt_n` distribution; Samantha S4 (no repeated check-ins within a session) |
| 3 Verbatim evidence | `MemoryService.ingest(source_excerpt=…)` writes metadata `source_excerpt` (`_build_metadata`). Only `skybridge_service.py:3434` and `hindsight_retain_candidates.py:286` pass it. `memory_extractor` computes `source_excerpt` (220 chars) on each candidate, but its ingest at `memory_extractor.py:946` **drops it**. `run_turn_digest` (`memory_digest.py:591`) and the nightly digest pass none. The turn-digest `user_turn_id` is sha1(message), not a `chat_messages.id`. Verbatim transcripts do exist in `chat_messages`. Packet lines are `- text[:200] [mem:id8]`, with no date and no evidence. | Field exists; capture is ~absent; rendering is absent. | **Capture now**, flag-free: pass `source_excerpt=scrub_pii(user_message)[:220]` in the turn digest and extractor. The excerpt currently **bypasses** the PII scrubber, which runs on `text` only. **Render later**, and only on explicit `recall_memory` results: `(said 2026-09-12: "…")`. | S capture / M render | Capture: off the voice path (background digest), metadata only. Render: `routers/memories.py`, which is *not* in `VOICE_PATH_PATTERNS` even though it shapes voice packets. | Unit test that the excerpt is stored and scrubbed. Later, S2/S7 plus a LongMemEval-S subset |
| 4 Validity + supersedes | `MemoryService.review(edit)` writes the new row, then sets old `status=superseded`, `superseded_by_id`, and the new row's `supersedes_id`. Write-time reconcile ADD/UPDATE/SKIP runs via `memory_quality.reconcile_for_ingest` in the turn digest and nightly digest. `memory_extractor` does conversational-correction supersede. `expires_at` is honoured. `_BLOCKED_READ_STATUSES` hides superseded/archived/pending on every read. The packet shows conflicts newest-first (`_present_conflicts_newest_first`). `person_relationships` has `valid_from/valid_to/superseded_by`. Latest snapshot: 13 superseded / 455 approved. S2 passes. | Explicit corrections are handled. Missing: event time (`added_at` is capture time) and an **implicit-conflict** sweep. `memory_lint` is report-only and `ZOE_MEMORY_LINT_IN_DREAMING` is unset. | Derive the interval with no schema change (`valid_to` = successor's `added_at`). Add a flag-gated dreaming step: lint contradiction candidates → one LLM adjudication → `review(edit)`. | M | Offline (dreaming). A wrong adjudication hides a true fact. It is recoverable because rows are retained, not deleted. | STALE-style scenario added to `samantha_bar.py`; lint report counts |
| 5 Proactivity selector | `proactive/engine.py` has quiet hours. Triggers: `morning_checkin`, `emotional_followup` (enabled: neg/mixed, intensity ≥0.6, 20 h–7 d, once per moment, ≤1 per user per day), `evening_windown`, birthdays, `people_health`, reminders. `arrival.py` has a once-per-day claim (`proactive_responses`, 0 rows). Contact offers use `pending_suggestions` (≤2, aging, deferred on continuity). The continuity **focus item** is already a cue-matched single surfacing. `ZOE_PROACTIVE_SPOKEN=0`. **`open_loops`: 0 rows** — `_extract_open_loops` → `from zoe_agent import _llm_chat` raises ImportError (133 occurrences in the dreaming log). `tests/test_memory_digest_postgres_sql.py` stubs only the DB, so it passes regardless. | No cross-trigger salience score, no global cooldown, no "≤1 per conversation" budget. The main candidate source (open loops) is dead. S5 is hook-gated to SKIP. | (1) Repair open loops (S). (2) Later, a nightly `surface_candidates` pass: rank loops, emotional moments and events by importance × recency × relevance, with trigger and cooldown columns. Inject ≤1 per conversation through the existing tail seam, reusing the `[Today]`/offer injection pattern. | S then M–L | (1) Offline, restores designed behaviour. (2) Voice path plus a flag. | `select count(*) from open_loops`; dreaming log `open_loops.extracted`; S5 un-SKIPs once a hook fires |
| 6 Recall as a tool | `recall_memory` is in `CORE_TOOL_NAMES` (always visible) → `GET /api/memories/for-prompt?limit=24`. Its only arg is `query`, also in router `TOOL_ARGS`. The router `memory` domain → `expert_dispatch._plan` → `memory_expert`, which **on chat runs the core lane**; voice defers to Flue (`fast_tiers defer domain=memory`). "Are you sure?" is a web-verification path (`zoe_agent.py:2668`, `web_search` tool behind `ZOE_WEB_SEARCH_TOOL`, off), not memory re-checking. | The tool exists. No time scoping; results are undated, so "when did I…" cannot be answered. There is a lane split on chat. | Add optional `since/until` to the tool and endpoint, plus a date prefix on explicit-recall lines. Route `memory_expert` through `brain_dispatch` so chat and voice share one context manager. | S–M | Voice path (tool schema sits in the cached prefix, so there is one cache miss at deploy). The router corpus drops unknown args (safe). | Samantha S1/S8; a new "when did I…" scenario |
| 7 Ask-to-remember | Nothing. The closest reusable mechanism is the `_pending_offer_block` "ask exactly" directive with aging. | Full build. | A "pending preference question" as a second offer type through the same seam, with caps and mood deferral (continuity turns already defer offers). | M | Voice path plus a flag; nagging risk | New Samantha scenario; count of `SEAM_OFFER` asks per day |
| 8 Side models | Router-head pattern (numpy heads, self-train loop). Hand-tuned hybrid rank in `memory_service` (semantic + hotness + recency 0.05 + preference 0.05 + `memory_importance`). | No training signal: injected `[mem:id]` refs are never logged per turn against the reply. | First log `refs` ids per injected block (a cheap data-capture PR), then train. | L | — | Offline eval |
| 9 Style LoRA | Nothing. | Full build, off-box. | — | L | Voice path, MTP interaction untested | Replay gate |

## 4. Recommended first three PRs

Ordered by impact per unit of work.

### PR 1 — Repair open-loop extraction and start capturing evidence

Off the voice path, about 200 lines, no flag; it restores a pass that is already enabled.

Files:
- `services/zoe-data/memory_digest.py`:
  - Replace the `_llm_chat` import with the module's own `httpx` → `{_GEMMA_URL}/v1/chat/completions` call (the pattern at lines ~478–490).
  - Parse JSON that arrives wrapped in code fences.
  - Skip inserting a loop that duplicates an unresolved one.
  - Pass scrubbed `source_excerpt` in `run_turn_digest`'s two ingest/edit calls.
- `services/zoe-data/memory_extractor.py`: forward `c.source_excerpt` (scrubbed) at the line-946 ingest.
- `services/zoe-data/memory_service.py`: run `scrub_pii` on `source_excerpt` inside `ingest`, so no caller can bypass it.
- New `services/zoe-data/tests/test_memory_open_loops_extract.py` (`ci_safe`). Stub the HTTP call and assert rows are inserted. **Negative control:** with the old import, the test must go red. The existing test passes either way.

Verify on the next 02:33 dreaming run: `open_loops.extracted>0`. Review the first night's loops by hand, because 4B extraction quality has never been measured.

### PR 2 — Flue user-model block

Flag `ZOE_USER_MODEL_BLOCK`, default off. Voice path, about 350 lines.

Files:
- `services/zoe-data/user_portrait.py`: `load_user_model_block(user_id)` returns `{version, text}`: a name line from `users`, like `voice_tts._voice_user_identity`, plus the portrait capped at 1,400 chars. It returns `""` when the flag is off, the user is a guest, or the user is synthetic.
- `services/zoe-data/routers/memories.py`: `GET /api/memories/user-model`, internal-token gated.
- New `labs/flue-zoe-brain-2x/src/user-model.ts`: fetch and cache the block per user, keyed by version with a TTL; fail-open to `""`.
- `src/providers/capped-completions.ts`: append the block to `systemPrompt` before `windowContextToBudget`.
- `src/agents/zoe.ts`: one doctrine line.
- Tests:
  - `test/user_model_block.test.ts`: byte-identical across turns and rounds; flag off gives byte-identical output to today.
  - `services/zoe-data/tests/test_user_model_block.py`.

Driving the flag from zoe-data means the operator flips one env var and the sidecar needs no flag of its own. Gate: replay probe, Samantha bar with the flag off vs on, and `recall_reliability.py`.

### PR 3 — Flue history hygiene and budget accounting

Voice path, about 250 lines. Flag `ZOE_BRAIN_ELIDE_STALE_BLOCKS`, default off.

Files:
- `labs/flue-zoe-brain-2x/src/providers/capped-completions.ts`, plus a new `src/context-blocks.ts` that mirrors `memory.ts` `CONTEXT_BLOCKS` for the Flue markers. Elide from all user messages except the last.
- `src/streaming.ts`: add estimated section sizes to the `done` terminal.
- `services/zoe-data/zoe_flue_client.py`: `_log_prompt_cache` logs them.
- A pin test that keeps the Python and TypeScript marker tables equal. This is the core lane's existing drift guard, copied.

This PR also stops persisted "ask the user exactly…" offer directives and continuity "ask how that is going" lines from replaying on later turns of the same session.

**Also recommended, as a one-line follow-up in the gate classifier:** add `services/zoe-data/routers/memories.py`, `user_portrait.py` and `memory_gate.py` to `VOICE_PATH_PATTERNS` in `scripts/maintenance/voice_gate_check.py`. They shape every voice recall and continuity block but are not replay-gated today.

## 5. Things I could not determine

- **Gemma 4 chat-template ordering of system text vs tool schemas in llama-server.** This decides whether a per-user system suffix also re-prefills the ~0.7k-token tool block on a user switch. Measure `first_prompt_n` on an A/B user alternation before trusting the cost estimate in PR 2.
- **Why 22% of turns miss the cache.** Candidates: new sessions, sticky tool-group growth, or other single-slot users (digest, portrait and router calls hit the same llama-server). The log does not record session age or group changes.
- **Whether real users ever trigger continuity or recall-floor turns over a longer window.** Logs only go back to 2026-09-02 (6 rotated files), and all hits in the window were demo users.
- **Flue session lifetime per channel,** i.e. how long history and the persisted blocks survive. Not traced.
- **Open-loop output quality once repaired.** It has never run successfully in the retained log.
- **`scripts/maintenance/memory_prompt_packet_measure.py` was not run.** It measures a disabled preview compiler (`zoe_memory_router_runtime`), not the Flue seam packet. Its default mode opens `MemoryService` against the live Chroma store, which has a history of HNSW corruption, and its `--seed-synthetic` mode writes. Packet sizes above come from the `SEAM_*` logs instead.
- **Process note.** Early on I ran `git fetch -q origin` in the live checkout to check PR #1781's status. That updated remote-tracking refs only; no working-tree or branch change. It should not have been run under the read-only brief. Nothing else wrote to the live checkout.
