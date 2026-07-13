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
- **1c ✅ (closed 2026-07 — see §6).** All three original steps happened: the CI
  acceptance loop (`tests/test_samantha_acceptance_loop.py`), the §8 lab proof, and
  the prod enable (`ZOE_IDLE_CONSOLIDATION_ENABLED=1` live since 2026-06-24, Jason-blessed)
  — and the §7 positive control has since been shown on organic traffic (real
  authenticated turns land in `chat_messages` with `metadata.user_id`; recall verified
  behaviorally, see `memory-qa-review-2026-07.md`). Nothing in 1c is open.
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
- [x] **1c** — **DONE (closed 2026-07 on organic traffic).** Lab-proof on real saved voice (2026-07-03, end-to-end capture+1b stamping+consolidate+recall); prod flag `ZOE_IDLE_CONSOLIDATION_ENABLED=1` live since 2026-06-24 (Jason-blessed), engine healthy; and the final positive control shown during the 2026-07 memory QA arc — real authenticated turns land in `chat_messages` **with** `metadata.user_id`, and recall/capture verified behaviorally on organic Telegram/panel traffic (`memory-qa-review-2026-07.md` + the #1242–#1266 hardening chain). The two older startup bugs noted in the 2026-06-26 diagnostic (`proactive/engine.py` `_cleanup_expired_pending` rowcount; `memory_digest.py` weekly fallback connection) were separate from the idle engine and are tracked outside this plan.
- [~] **2a** hybrid retrieval — SHIPPED (flag `ZOE_HYBRID_RETRIEVAL_ENABLED`, default **OFF**; real-Chroma lab-smoke passed, flag OFF, value-proof on the realistic corpus still pending). `MemoryService._semantic_search` now composes three cheap, additive, bounded, O(candidates) boosts on top of the existing semantic+hotness re-rank, gated behind the flag; OFF is a byte-for-byte no-op (the boost term is skipped entirely — proved by `test_flag_off_ordering_is_a_true_noop`). Boosts: (1) **keyword/lexical** — normalized alnum token overlap (stopwords dropped, whole-token *or* substring match so "dad"→"dad's"), weight `_HYBRID_KEYWORD_WEIGHT=0.50` — the primary fix for the 0-hit misses (rescues "what is my dad name"→"My dad's name is Neil"); (2) **temporal-proximity** — `exp(-ln2/30d · age)` × `_HYBRID_RECENCY_WEIGHT=0.05` (mild, never dominates relevance); (3) **preference/importance** — `memory_type ∈ {preference,approval,emotional_moment,person,recurring_task}` (or a numeric `importance` field if a producer ever writes one — currently a no-op arm) × `_HYBRID_PREFERENCE_WEIGHT=0.05`. Zero LLM calls, no embedder reload, no new deps. Candidate fetch + status/visibility filtering unchanged (cross-user + approved-only still enforced with the flag ON). Tests: `tests/test_memory_hybrid_retrieval.py` (8, all synthetic, embedding layer mocked). · [~] **2b** compose Postgres+Chroma packet — **SHIPPED** (flag `ZOE_MEMORY_COMPOSE_ENABLED`, default **OFF**, pending lab proof). New `zoe_memory_compose.py` folds the *relational* half (Postgres `people`/`person_relationships`/`person_important_dates` + `user_portraits`) under the existing vector packet in `/api/memories/for-prompt`, each line **cited** (`[people]`/`[relationship]`/`[date]`/`[portrait]`). **Router-gated** by a cheap zero-LLM keyword/pattern classifier (`needs_relational`) so relational facts attach only on person/relationship/date queries — otherwise the packet stays vector-only. OFF (or a non-relational query) is a **byte-for-byte no-op**: `compose_enabled()` short-circuits before any DB read (proved by `test_flag_off_golden_matches_direct_builder`). Reads are three bounded batch queries (no N+1, no LLM), per-user + `visibility='family' OR user_id=?` scoped (no cross-user leakage, soft-deleted excluded), row/char-budgeted. Consumer `memory.ts` unchanged (reads only `packet`). Tests: `tests/test_memory_compose_packet.py` (20, synthetic — fake MemoryService + seeded fake relational store). · [~] **2c** voice mirror — **SHIPPED** (same flag `ZOE_MEMORY_COMPOSE_ENABLED`, default **OFF**; voice + chat now share one composed memory source when enabled). `routers/voice_tts._voice_recall_packet` now folds the SAME cited relational block chat uses into the `[What you remember]` voice block. Both paths call one shared entry point, `zoe_memory_compose.compose_packet(user_id, message)` (new) — it owns the flag + `needs_relational` router gate + `get_db_ctx` + `compose_relational_block`, so the compose/gate logic is NOT duplicated and can't drift; `routers/memories.py` was refactored onto it with **identical** behaviour (its 20 tests unchanged/green). Flag OFF is a **true no-op** for voice: `compose_packet` cheap-gates (pure `compose_enabled()` + `needs_relational()`) before opening any DB connection, so a non-relational or flag-OFF turn is byte-for-byte the pre-2c output (proved by `test_flag_off_identical_to_today`). Hot path preserved (zero-LLM, relational pull only on person/relationship/date turns); per-user + `visibility='family' OR user_id=?` + soft-deleted scoping inherited from 2b; guest fails closed before the read. Tests: `tests/test_voice_recall_compose_2c.py` (11, synthetic — mocked embeddings + seeded fake relational store).
- [x] **2a/2b/3a-portrait ENABLED IN PROD 2026-07-03** — `ZOE_HYBRID_RETRIEVAL_ENABLED=1` + `ZOE_MEMORY_COMPOSE_ENABLED=1` live after real-store proof (jason recall wrong→right, portrait delivered on identity queries); reversible via flag.
- [x] **Emotional thread (criterion #2) — SHIPPED + LIVE**. Capture: Flue `EMOTIONAL_CAPTURE_DOCTRINE` (#1003). Recall: for-prompt emotional gate + intensity **pin** (#1004/#1005, `ZOE_EMOTIONAL_RECALL_ENABLED=1` live) + the live Flue brain's new `EMOTIONAL_RECALL_DOCTRINE` (this PR) — measured **4/4** on the live 4B brain ("how have I been feeling?" → recalls the settlement thread).
- [x] **3a** reflection — `user_portrait.run_portrait_synthesis` runs weekly via the digest cycle (wired); portrait delivered by 2b compose. Understanding-evolves criterion passes on the acceptance harness.
- [x] **3b** importance — BUILT. `memory_importance.score_importance` (new) scores high-stakes content — safety/medical (0.9), dietary restriction (0.7), vital-id (0.6), ordinary → 0.0 — and `MemoryService._build_metadata` writes it onto the row (only when >0, so ordinary facts stay boost-free). Activates the 2a importance arm for plain `fact`-type rows (a penicillin allergy typed "fact" now boosts, where emotional_moment/preference/person already scored 1.0 via the type arm). Lab-proven: allergy fact stored `importance=0.9`, ordinary fact none. Tests: `tests/test_memory_importance.py` (16). Effect is a mild 0.05-weight nudge by design.
- [x] **3c** proactive surfacing — the **proactive engine's morning brief** (`proactive/triggers/morning_checkin.py`, started in main lifespan) deterministically surfaces recent emotional moments + calendar + portrait into a pushed brief (proven: brief renders "I've been thinking about you — Jason has been anxious about the settlement…"). In-turn 4B surfacing was measured (~1/5) and **rejected** — unprompted surfacing is the deterministic morning brief, not model whim.
- [x] **Acceptance test EXTENDED + GREEN (all 4 criteria)** — `services/zoe-core/test/test_samantha_acceptance.py` now covers emotional-thread recall, proactive surfacing, and evolving understanding alongside identity/recall/tool/continuity/latency. **8/8 pass on the box** (live pi + Gemma). NOTE: this harness drives the *core* backend; the LIVE backend is `ZOE_BRAIN_BACKEND=flue`, verified separately against the live Flue brain.
- [x] **Oblique-recall lift + FINAL LIVE EVAL = 100% (2026-07-05)**. A whole-system eval on the LIVE Flue brain first read **88%** single-shot; every miss was the 4B model skipping `recall_memory` on OBLIQUE personal questions ("where do I live", "do I have allergies", "do I prefer tea") — memory itself was ~100%, the gap was brain recall-call behaviour. Fixed with **`PERSONAL_RECALL_DOCTRINE`** (#1026, Flue `zoe.ts`): widen recall to ANY question about the user's own facts, with a hard general-knowledge bound (recipes/maths/world facts answered directly, no recall). Merged + **auto-deployed** (first clean Flue auto-rebuild after the #1016 npm-PATH fix). **Re-run of the full 8-question eval on the deployed :3578 brain: 40/40 = 100%** (dog/dad/mum/home/preference/2×emotional/allergy all 5/5, morning brief PASS; contention 500s retried past). Prior arc PRs: #1004/#1005 (emotional gate+pin), #1014 (emotional-recall doctrine + acceptance suite), #1016 (deploy npm-PATH fix), #1017 (3b importance, incl. a real stale-importance-on-edit bug caught+fixed).
- [x] **Voice regression clean** — 25 `~/.zoe-voice-samples` via `scripts/maintenance/voice_regression_probe.py` → **0 hard failures (fail=0)**; 24/25 scored "OK" and the 25th was a non-failing soft/warn outcome (the probe's third bucket — not an error/timeout), so no regression from any memory change.
- [ ] (carry-over) identity deploy `#768` — FF-deploy when the tooling/classifier outage clears

## 7. NEXT ACTION (always exactly one)
→ **Build phase COMPLETE. All four Samantha criteria are met and measured at 100% on the LIVE Flue
brain** (2026-07-05 full eval: 40/40 usable across fact recall ×5, emotional recall ×2, safety
recall, + morning brief PASS — §6). 3a/3b/3c done; the acceptance suite is 8/8. The remaining work
is **monitoring, not building** — and the one thing that would move the needle is **not a code
task**: jason still has **0 real `emotional_moment` rows**, so the entire emotional path is proven on
seeded/demo data and does nothing for him until a genuine emotional conversation is captured by the
live brain. Watch with `scripts/maintenance/check_emotional_thread.py`; when the first real row
appears, spot-check that recall + the morning brief surface it, then this is truly done for a real
user. Do NOT resurrect the dead idle-consolidation path (§8) as "the" memory path — the live memory
is the immediate voice/chat writers + the for-prompt packet + the Flue `recall_memory` tool
(governed by the live persona doctrines in `labs/flue-zoe-brain/src/agents/zoe.ts`).

## 8. Increment 1c — enable runbooks

> **STATUS 2026-06-27 — DO NOT RE-RUN §8.3 AS A FRESH ENABLE.** The prod flag is
> **already ON** (`ZOE_IDLE_CONSOLIDATION_ENABLED=1` in `services/zoe-data/.env`,
> PID-confirmed; set 2026-06-24) and the engine is healthy, so §8.3 below is kept
> only as reference + the rollback/watch procedure — the enable/restart step is
> **done**, not pending. §8.2's demo path is likewise proven (55 demo watermark
> rows). **The §7 positive control is CLOSED (2026-07):** real authenticated turns
> land in `chat_messages` *with* `metadata.user_id` on organic traffic (memory QA arc,
> `memory-qa-review-2026-07.md`). Treat the enable/restart in §8.3 as a no-op unless
> the flag has been deliberately turned OFF.

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
  process must have the env var. (To re-enable after a deliberate OFF: **first repeat the §7
  positive-control turn**, then set `ZOE_IDLE_CONSOLIDATION_ENABLED=1` in the live service env — the systemd unit / `.env` the
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
- **Live-box proof (was the 8.2 gate on the 8.3 enable): ✅ satisfied.** The enable is done
  (flag live since 2026-06-24) and the live-box checks it gated — clean owner-attributed
  extraction, recall of consolidated facts, hot path untouched — were shown on organic
  traffic during the 2026-07 memory QA arc. Nothing gates here anymore; this list is kept
  only as the rollback-and-re-enable checklist if the flag is ever turned OFF.
