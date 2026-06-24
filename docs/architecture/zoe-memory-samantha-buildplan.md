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
- **1a ✅ DONE** — engine `memory_idle_consolidation.py` (+ tests, `main.py` loop),
  flag `ZOE_IDLE_CONSOLIDATION_ENABLED` (off). Branch `feat/memory-idle-consolidation`.
- **1b ← NEXT** — *persist the effective user on each turn.* Conversations currently
  store `chat_sessions.user_id='guest'` even when authenticated, so consolidation
  can't resolve the owner. Fix: in `routers/chat.py:_save_chat_message` store the
  user_id into `chat_messages.metadata` (JSON) at write time; callers
  (`chat.py` ~660 and voice `_schedule_voice_chat_save` in `voice_tts.py`) pass the
  resolved `effective_user`. Update `memory_idle_consolidation.find_idle_sessions` /
  `consolidate_session` to resolve the user from message metadata (fallback to
  `chat_sessions.user_id` when present and non-guest). **Verify:** lab sweep finds a
  real (jason) idle session and consolidates clean facts.
- **1c** — *lab-prove + gate + prod-enable.* Enable the flag in the worktree; replay
  a morning→afternoon exchange; confirm afternoon cross-session recall. Add a
  same-day-cross-session case to the zoe-core Samantha tests. Then enable the prod flag.
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
- [x] **1a** — idle consolidation engine (branch `feat/memory-idle-consolidation`, flag off, 5 tests green, SQL lab-validated)
- [ ] **1b** — persist per-turn user  ← **NEXT**
- [ ] **1c** — lab-prove + Samantha same-day test + prod enable
- [ ] **2a** hybrid retrieval · [ ] **2b** compose Postgres+Chroma packet · [ ] **2c** voice mirror
- [ ] **3a** reflection · [ ] **3b** importance · [ ] **3c** proactive surfacing
- [ ] (carry-over) identity deploy `#768` — FF-deploy when the tooling/classifier outage clears

## 7. NEXT ACTION (always exactly one)
→ **Increment 1b.** In a fresh worktree off `origin/main` (or continue `feat/memory-idle-consolidation`):
persist `effective_user` into `chat_messages.metadata` at `_save_chat_message` (chat + voice callers);
update `memory_idle_consolidation` to resolve the user from message metadata; lab-verify a sweep
consolidates a real jason idle session into clean facts. Then advance to 1c. Update §6/§7 when done.
