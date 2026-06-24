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
- **Capture:** every chat/voice turn → `chat_messages` verbatim, instantly. ✅ DONE.
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
- [~] **1c** — lab-prove + Samantha same-day test + prod-enable prep  ← **NEXT** (CI acceptance test added; lab-enable + prod-enable runbooks documented in §8; prod flag NOT flipped — awaits Jason + a run on the live box)
- [ ] **2a** hybrid retrieval · [ ] **2b** compose Postgres+Chroma packet · [ ] **2c** voice mirror
- [ ] **3a** reflection · [ ] **3b** importance · [ ] **3c** proactive surfacing
- [ ] (carry-over) identity deploy `#768` — FF-deploy when the tooling/classifier outage clears

## 7. NEXT ACTION (always exactly one)
→ **Run the §8 lab-enable runbook on the live box, then (with Jason's blessing) the prod-enable
runbook.** The merged 1a+1b code is proven in CI by `services/zoe-data/tests/test_samantha_acceptance_loop.py`
(live→idle→store→recall, Gemma + DB mocked, real gate + ingest/recall) — that test does NOT need the
live box and is the regression bar. What remains can only be done ON the Orin (it needs real
Postgres/Chroma/Gemma): set `ZOE_IDLE_CONSOLIDATION_ENABLED=1` + a short `ZOE_IDLE_CONSOLIDATION_IDLE_S`
in a worktree, replay a morning→afternoon exchange, confirm afternoon cross-session recall via
`/api/memories/for-prompt`, and run the zoe-core integration Samantha tests. Only after that passes,
flip the prod flag per §8 and watch the `MEMORY_IDLE_CONSOLIDATE` log line + memory-store growth.
**Do not flip the prod flag without Jason.** When 1c lands as proven-in-prod, move to **2a (hybrid
retrieval)**. Update §6/§7 after each step.

## 8. Increment 1c — enable runbooks

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

### 8.3 Prod-enable (only after 8.2 passes AND Jason blesses it)
- The flag is read **per-sweep** (`start_idle_consolidation_loop` re-checks `_enabled()` every
  iteration), so enabling needs no code change and no restart of the loop task itself — but the
  process must have the env var. Set `ZOE_IDLE_CONSOLIDATION_ENABLED=1` in the live service env
  (the systemd unit / `.env` the zoe-data process reads) and restart via
  `scripts/maintenance/deploy_live.sh` (rolls back on a failed health check). Leave
  `IDLE_S`/`LOOKBACK_S` at defaults (180s / 3600s) for prod.
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
