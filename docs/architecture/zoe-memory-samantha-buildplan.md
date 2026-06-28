# Zoe Memory → Samantha-grade: Autonomous Build Plan

> **Single source of truth for the memory rebuild.** A fresh/autonomous session
> (no human present) reads this top-to-bottom, does the **§7 NEXT ACTION**, updates
> the **§6 checklist**, and repeats. Do not improvise around it. Companion analysis
> lives in memory `project-mempalace-deep-dive`; this is the *executable* version.

## 0. Operating rules (non-negotiable — these are why it failed before)
- **Work only in a dev worktree** (`/home/zoe/.worktrees/...`), never the live tree
  `/home/zoe/assistant` (it serves `main` + the live service). Symlink `.env` in.
- **One increment = one PR.** Drive each to merge: address Greptile findings, reply,
  resolve threads, `gh pr merge <branch> --squash --auto`. Server-side only — **no
  checkout-based merge drivers in the live tree** (they wiped work twice).
- **Every new behaviour ships behind a flag, default OFF.** **Lab-prove** (enable the
  flag in the worktree, run against real services, run the zoe-core Samantha tests)
  **before enabling in prod.** No prod migration before lab proof.
- **Deploy** only via `scripts/maintenance/deploy_live.sh` (FF + restart, rolls back
  on failed health check). Enable a prod flag only after Samantha-tests pass.
- After every step, **update §6 + §7** in this doc and commit it.

## 1. Goal & acceptance bar
Samantha-grade companion memory that is **local, fast, no-nightly**, with **same-day
cross-session recall**: *"tell it in the morning → reference it in the afternoon."*
Acceptance = `services/zoe-core/test/test_samantha_acceptance.py` (extend it): recalls
a fact same-day across sessions; recalls the emotional thread; surfaces relevant
memory unprompted; its understanding of the user evolves.

## 2. DO-NOT-REPEAT (the traps that sank prior attempts — evidenced)
- **No heavy graph DB** (Graphiti/Neo4j). Orin NX 16GB has **~1.8 GB free** (Gemma
  =4.9 GB); a graph DB doesn't fit → it got parked → the composite never wired.
  Get temporal/relationship value from **Postgres relational + Chroma**, both already running.
- **No nightly batch.** Cadence is immediate (verbatim) + event-driven (idle).
- **No extraction-first pollution** of the raw store. MemPalace is **raw-first**: store
  verbatim, retrieve smart; keep distilled facts as a thin separate layer.
- **Rocks unchanged**: Gemma 4 E4B+MTP brain, Moonshine v2 Medium STT. Optimise around them.
- **Hot path = one warm sub-400 ms retrieval, zero-LLM.** All LLM/heavy work runs
  off the hot path (idle / router-gated). Warm the embedder on wake (kills the 2.1 s cold).

## 3. Target architecture (compose what we already run)
- **Stores (already running):** Chroma/MemPalace (vector recall, raw-first) +
  PostgreSQL (entities/relationships/temporal/user-model: `people`,
  `person_relationships`, `person_important_dates`, `user_portraits`, `events`).
- **Capture:** every chat/voice turn → `chat_messages` verbatim. ⚠️ MECHANISM REAL, LIVE-UNCONFIRMED POST-1b. Code-traced + demo-proven; 68 organic `voice-panel-*` rows + 728 `web_*` rows exist historically. BUT the newest `chat_messages` row of ANY kind is 2026-06-22 — *before* the 1b deploy (2026-06-24) — so **zero rows carry `metadata.user_id`** and the table is dominated by test/replay harness sessions. No persisted turn has occurred since 1b shipped, so owner-stamping is unconfirmed on live organic traffic. Open either/or: no real voice *commands* since (wake-bleed empty transcripts + panel polling only) vs a persistence regression ~2026-06-24. Note: organic panel voice ALSO has its own per-turn memory path (`voice_tts._run_voice_memory_passes` → `latent_intent_detector.detect_and_store`) independent of idle consolidation.
- **Consolidate (Increment 1):** idle-triggered whole-conversation pass → clean facts
  via the write-quality gate. `memory_idle_consolidation.py`.
- **Retrieve (Increment 2):** `zoe_memory_router` composes Chroma + Postgres into the
  **`/api/memories/for-prompt`** cited packet → Pi **`zoe-core/extensions/memory.ts`**
  already injects it (the one seam). Voice `_voice_recall_packet` mirrors it.
- **Reflect (Increment 3):** idle-time insight synthesis → `user_portraits` + memories,
  importance-scored; proactive surfacing in the Pi soul.

## 4. Increments (sequenced; each = PR + flag + Samantha gate)
### Increment 1 — idle "live → idle → store" consolidation
- **1a ✅ DONE (PR #771)** — engine `memory_idle_consolidation.py` (+ tests, `main.py`
  loop), flag `ZOE_IDLE_CONSOLIDATION_ENABLED` (off). Branch `feat/memory-idle-consolidation`.
- **1b ✅ DONE (PR #775)** — *persist the effective user on each turn.* Conversations
  stored `chat_sessions.user_id='guest'` even when authenticated, so consolidation
  couldn't resolve the owner. Fixed: `routers/chat.py:_save_chat_message` stamps the
  resolved user into `chat_messages.metadata` (JSON `{"user_id": ...}`) at write time;
  chat + voice callers pass the resolved `effective_user`; guest/empty leaves metadata
  NULL. `memory_idle_consolidation._resolve_owner` resolves the owner from per-turn
  metadata (most-recent non-guest, freq tie-break), falling back to a real
  `chat_sessions.user_id` only. Sessions with no resolvable real user are skipped.
- **1c ← NEXT** — *lab-prove + gate + prod-enable.* The merged 1a+1b code is gated OFF
  behind `ZOE_IDLE_CONSOLIDATION_ENABLED`. This increment proves the full loop and
  preps (does NOT execute) the prod enable. Steps: (1) self-contained CI acceptance
  test `tests/test_samantha_acceptance_loop.py` exercising live→idle→store→recall
  against the merged engine (Gemma extractor + DB mocked, real gate + real ingest/recall
  path); (2) the lab-enable runbook in §8 (flip the flag in a worktree against real
  Postgres/Chroma/Gemma, replay a morning→afternoon exchange, confirm afternoon
  cross-session recall, run the zoe-core integration Samantha tests); (3) the prod-enable
  runbook in §8 — what to flip, what to watch, what still needs the live box. **The prod
  flag stays OFF; Jason blesses the prod enable.**
- **DoD:** a fact told now is consolidated within minutes of idle and recalled in a
  later session, with junk gated out.

### Increment 2 — composite query-relevant retrieval into the packet
- **2a** — *hybrid retrieval* in `MemoryService.search`: add keyword + temporal-proximity
  + preference-pattern boosting on top of semantic (MemPalace best practice; fixes the
  measured 0-hit misses like "what is my dad name"). Verify on the saved-audio corpus + a recall set.
- **2b** — *compose Postgres + Chroma* into `/api/memories/for-prompt` via
  `zoe_memory_router`: relational facts (people/relationships/important_dates/portrait)
  + vector recall, **cited**, router-gated (relational only when the query needs it).
- **2c** — point voice `_voice_recall_packet` at the same composed packet (one memory
  across chat + voice). Pi `memory.ts` unchanged — it just receives a richer packet.
- **DoD:** packet is compact, cited, importance-aware, draws on both stores; recall set passes.

### Increment 3 — reflection / user-model (the Samantha "understanding")
- **3a** — idle *reflection pass*: synthesize higher-level insights from recent memories
  → write to `user_portraits` + as importance-scored memories. Reuse the idle loop.
- **3b** — *importance* scored at consolidation and used in retrieval ranking
  (recency × importance × relevance).
- **3c** — *proactive surfacing*: packet flags "worth mentioning"; the Pi soul brings it
  up unprompted (Pi-side).
- **DoD:** Samantha tests pass — recalls the emotional thread, evolves understanding,
  surfaces unprompted.

## 5. Per-increment procedure (the loop body)
1. `git worktree add --detach /home/zoe/.worktrees/<name> origin/main`; branch;
   symlink `.env`.
2. Implement behind a flag (default off) + focused tests; `py_compile` + `pytest`.
3. Lab-prove: enable the flag in the worktree, run against real Postgres/Chroma/Gemma,
   run the zoe-core Samantha tests.
4. Commit (`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`), push, open PR,
   `gh pr merge <branch> --squash --auto`.
5. Drive Greptile to merge (fix → reply → `resolveReviewThread`); keep it server-side.
6. After merge: `deploy_live.sh`. Enable the prod flag **only** after Samantha-tests pass.
7. Update §6 + §7 here; commit the doc.

## 6. Status / where am I
- [x] Deep dive + architecture decided (memory `project-mempalace-deep-dive`)
- [x] Memory P1 read-packet, P2 write-gate, P3 junk cleanup (shipped earlier)
- [x] **1a** — idle consolidation engine (**PR #771** merged; branch `feat/memory-idle-consolidation`, flag off, tests green, SQL lab-validated)
- [x] **1b** — persist per-turn user (**PR #775** merged; chat+voice `_save_chat_message` stamps `metadata.user_id`; consolidation resolves owner from per-turn metadata over a guest session; SQL lab-proven a guest-owned session resolves to `jason`)
- [x] **dedup gate** — `memory_quality.classify_against_existing` ADD/UPDATE/**SKIP** + prefer-richer + same-attribute near-dup merge + "never drop a correction" (**PR #794** squash-merged 2026-06-24; branch tip `f0e30bff` == #794 head, so the "2 unmerged commits" are the squash-ancestry illusion, NOT pending work). This was the stated prod-enable blocker; it is **cleared**. Plus **PR #795/#796** (live-integration suites) and **PR #798** (normalize `GEMMA_SERVER_URL` — the prod `/v1/v1` 404 was killing consolidation).
- [~] **1c** — lab-prove + prod-enable + **verify prod health**  ← **NEXT** (CI bar green: 67 tests pass, demo/synthetic only. **Prod flag is already ON** since 2026-06-24: `ZOE_IDLE_CONSOLIDATION_ENABLED=1` in `services/zoe-data/.env`, confirmed in the live process env (PID-checked 2026-06-26). Engine is **healthy** (NRestarts=0, active). **BUT it has never consolidated a real conversation.** Live Postgres diagnostic 2026-06-26 (read-only): `memory_consolidation_state` has 55 watermark rows and **every one is a `demo_*`/`demo_dedup_*` test session** (last `2026-06-24 21:55`) — all from the dedup lab run, zero real users. Why: (a) **no `chat_messages` traffic in 3.7 days** (newest row ~2026-06-22, total 3150) so nothing is ever inside the 1h lookback; (b) **zero `chat_messages` carry `metadata.user_id`**, so 1b owner-resolution would skip every session — though all 3150 rows **predate the 1b deploy (2026-06-24)**, so 1b stamping is **unverified in prod, not proven broken**. The 3.7-day capture gap is itself suspicious (panel is in active use → is capture even alive?). Also two **separate, older** startup bugs (NOT the idle engine): `proactive/engine.py:208` `_cleanup_expired_pending` does `cur.rowcount` on the compat `_Cursor` (no such attr); `memory_digest.py:706` weekly `run_weekly_consolidation_for_all` fallback hit "connection was closed in the middle of operation". 1c is not done until a *real* authenticated turn is shown to (1) land in `chat_messages` and (2) carry `metadata.user_id`, and then a sweep consolidates it cleanly.)
- [ ] **2a** hybrid retrieval · [ ] **2b** compose Postgres+Chroma packet · [ ] **2c** voice mirror
- [ ] **3a** reflection · [ ] **3b** importance · [ ] **3c** proactive surfacing
- [ ] (carry-over) identity deploy `#768` — FF-deploy when the tooling/classifier outage clears

## 7. NEXT ACTION (always exactly one)
→ **Get ONE real post-1b persisted turn and watch the whole chain (the positive control).**
DIAGNOSIS DONE 2026-06-26/27 (Jason chose "diagnose first"): capture is NOT structurally broken —
`_save_chat_message` works (demo round-trip PASS; `get_db_ctx` translates `?`→`$N`), `voice_command`
persists the user turn (~line 3014) *before* any fast-path, `zoe-touch-pi` resolves to a real owner
(jason via `panel_user_bindings`/`ui_panel_sessions`), and 68 organic `voice-panel-*` rows exist
historically. The gap: **no turn of any kind has persisted since 2026-06-22 (pre-1b)**, so the
metadata-stamp + idle-consolidation chain has never run on live post-1b data. **Positive control
(needs a real turn — static trace can't go further):** have Jason speak ONE command to the panel
("Zoe, remember my dad's name is Neil"), then read-only confirm a fresh
`voice-panel-zoe-touch-pi-*` row appears in `chat_messages` *with* `metadata.user_id` populated.
Branches: row WITH metadata → chain healthy; wait past IDLE, confirm `MEMORY_IDLE_CONSOLIDATE …
stored>=1` + recall via `/api/memories/for-prompt`. Row WITHOUT metadata → 1b stamping regressed on
the live voice path (fix `effective_user`→`_save_chat_message` plumbing). NO row → persistence
regressed ~2026-06-24 (the fire-and-forget `_spawn_bg` save) — the real blocker, fix first. **Parallel,
independent:** the two adjacent startup bugs as one small hardening PR (no new flag):
`proactive/engine.py` `_cleanup_expired_pending` `rowcount` on the compat `_Cursor`, and
`memory_digest.py` weekly `list_users` pooled-connection-closed. After the control passes, revisit the
§3 strategy fork (chat_messages-as-spine vs voice-keeps-own-path), then **2a**. Update §6/§7 after each step.

## 8. Increment 1c — enable runbooks

> **STATUS 2026-06-27 — DO NOT RE-RUN §8.3 AS A FRESH ENABLE.** The prod flag is
> **already ON** (`ZOE_IDLE_CONSOLIDATION_ENABLED=1` in `services/zoe-data/.env`,
> PID-confirmed; set 2026-06-24) and the engine is healthy, so §8.3 below is kept
> only as reference + the rollback/watch procedure — the enable/restart step is
> **done**, not pending. §8.2's demo path is likewise proven (55 demo watermark
> rows). **The actual open 1c item is the §7 positive control:** prove a fresh
> authenticated turn lands in `chat_messages` *with* `metadata.user_id`, then that a
> sweep consolidates it. Treat the enable/restart in §8.3 as a no-op unless the flag
> has been deliberately turned OFF.

### 8.1 What is already proven (no live box needed)
- `test_samantha_acceptance_loop.py` drives the merged engine end to end: a short morning
  conversation → `run_idle_consolidation_sweep()` (flag ON) finds the idle session, resolves the
  owner from per-turn metadata, extracts facts (Gemma stubbed), passes them through the **real**
  `memory_quality.is_storable_fact` gate, ingests via a fake MemoryService, and a later "afternoon"
  recall surfaces the durable fact while the gated junk (a question) is absent. Run:
  `python -m pytest services/zoe-data/tests/test_samantha_acceptance_loop.py services/zoe-data/tests/test_memory_idle_consolidation.py -v`.

### 8.2 Lab-enable (on the Orin, in a worktree — NOT prod)
1. `git worktree add --detach /home/zoe/.worktrees/mem-1c origin/main`; symlink the live `.env`.
2. Export, scoped to that worktree's process only (do not edit the live service env):
   `ZOE_IDLE_CONSOLIDATION_ENABLED=1`, `ZOE_IDLE_CONSOLIDATION_IDLE_S=30`,
   `ZOE_IDLE_CONSOLIDATION_CHECK_S=15`, `ZOE_IDLE_CONSOLIDATION_LOOKBACK_S=3600`.
3. Have an authenticated (jason) chat/voice exchange that states a durable fact ("my dad's name is
   Neil"). Wait > `IDLE_S`. Call `run_idle_consolidation_sweep()` (or let the loop run) against the
   real Postgres/Chroma/Gemma.
4. **Confirm:** the sweep returns `sessions>=1, stored>=1`; the `MEMORY_IDLE_CONSOLIDATE` log line
   names `user=jason`; `memory_consolidation_state` has a row; the fact appears in a later session's
   `/api/memories/for-prompt` packet for jason and NOT junk/questions.
5. Run the zoe-core integration Samantha tests with the live model up:
   `python -m pytest services/zoe-core/test/test_samantha_acceptance.py -v` (these skip without
   `pi` + the model server — that is expected off the box).

### 8.3 Prod-enable — ALREADY DONE 2026-06-24 (reference + rollback/watch only)
- ⚠️ **The enable below already happened — do NOT perform it again as if fresh.** The flag is on
  in `services/zoe-data/.env` and confirmed in the live process env. Re-running the enable/restart
  does NOT prove the live capture chain; the open item is the §7 positive-control turn. This bullet
  is retained only to document how the flag is wired and how to re-enable *if it is ever turned off*.
- The flag is read **per-sweep** (`start_idle_consolidation_loop` re-checks `_enabled()` every
  iteration), so enabling needs no code change and no restart of the loop task itself — but the
  process must have the env var. (To re-enable after a deliberate OFF: set
  `ZOE_IDLE_CONSOLIDATION_ENABLED=1` in the live service env — the systemd unit / `.env` the
  zoe-data process reads — and restart via `scripts/maintenance/deploy_live.sh`, which rolls back on
  a failed health check. Leave `IDLE_S`/`LOOKBACK_S` at defaults 180s / 3600s for prod.)
- **Watch (first hour):** `journalctl -u <zoe-data unit> | grep MEMORY_IDLE_CONSOLIDATE` for
  per-session stored counts and the resolved user; the `idle consolidation sweep:` summary line;
  no growth in error/warn lines from the sweep; memory-store row count for jason rising only with
  *clean* facts (spot-check `/api/memories/for-prompt`). The engine is best-effort and never
  blocks a turn, so the hot path is unaffected; a Gemma OOM/timeout leaves the watermark
  un-advanced and retries next sweep.
- **Rollback:** set the flag back to `0` and redeploy; the loop idles harmlessly. No data
  migration to undo — only thin distilled facts were added, which the existing memory tooling
  can review/forget.
- **What still needs the live box to fully prove (cannot be done in CI):** real Gemma extraction
  quality (do the facts come out clean and owner-attributed?), real Chroma recall ranking of the
  consolidated fact, and end-to-end latency that the sweep never touches the hot path under real
  load. These are the 8.2 checks; they gate the 8.3 enable.
