# Lane C: memory, brain-side intelligence, harness, ops/CI, roadmap coherence

Audit date 2026-10-18, read-only. Repo: worktree `claude/zoe-architecture-review-2efbf6` (= origin/main 62f1e7e83).
Labels: **[verified]** means I read the code, the live process, the unit or the log myself this session. **[inferred]** means a reasoned conclusion I did not prove end to end.
Live reads used: `/proc/<zoe-data pid>/environ` (flag names and values only, no secrets), `systemctl --user list-timers`, unit files, `journalctl --user`, `docker inspect/port/stats`, `~/.cache/zoe/*.json`, `~/.zoe-logs/*`, `gh` (read-only).

---

## 0. Headline findings (the ones that change priorities)

1. **The forget promise has side stores it does not reach.** [verified] Every one of these keeps household words and none is named in `memory_forget_cascade.py`, `memory_residue.py` or `memory_forgotten.py`:
   - `services/zoe-data/data/router_head_shadow.jsonl`: 10,857 rows, each with an `utt_text` field holding the user's words (`semantic_router.py:134-136`).
   - `~/training/data/train.jsonl`: 9 MB, 10,631 "turns".
   - `~/.openclaw/agents/main/sessions`: 1,161 files, 1.2 GB.
   - `~/.hermes`: 6.4 GB.
   - The on-box backups.
   The owner rule "forgotten means forever" therefore holds for the palace and Postgres, not for the box.
2. **An unversioned 2026-04 "nightly training cycle" still runs every night at 02:00.** [verified] Unit `zoe-training.timer` runs `~/bin/nightly-training-cycle.sh`, 34 KB, not in the repo.
   - It re-reads the dead OpenClaw session folder (newest file 2026-06-16) and appends **exactly 55 "new turns" every night**: 10,026 on 10-07 rising to 10,631 on 10-18 (`~/training/logs/nightly-summary.log`). It is a duplicating bug that grows a copy of private conversation.
   - LoRA training itself is off (`ZOE_ENABLE_LORA_TRAINING=false`).
   - It starts inside the 01:50-04:55 quiet hours. A sibling weekly "self-review" (cron plus timer, `~/bin/zoe-self-review.py`) writes proposals nobody reads to `~/training/improvement-proposals/`.
   This is a whole retired harness generation still running.
3. **The 12B night window is live and green, but it does almost nothing.** [verified]
   - `zoe-night-window.timer` runs at 02:50 every night. Since 2026-10-12 it has reported `outcome='ok'` every night (journal `NIGHT_WINDOW_SUMMARY`).
   - Each run stops zoe-data, the router, Kokoro and the 4B for 3-13 minutes.
   - The jobs consumed 0 prompt / 0 generated tokens for `night_mind`, and 0 for `digest` on recent nights (`~/.zoe-logs/night-window.log`).
   - Decode fell to 1.86 tok/s on 10-18, against 5.3-7.0 on earlier nights.
   - Behind that, the night mind runs in **enforce** mode, not off: `zoe-night-mind.py:387` defaults to `enforce` without `--dry-run`, and `night_window.py:591` passes no `--mode`. The gap register (§1 gap 1) and PLANS both say it "exists but is off", which is wrong.
   - `omi_day_review` is in the unit's `--jobs` but never appears in the run's job list.
   Green with nothing done is a vacuous success signal.
4. **The docs claim more is "dark" than really is, and the reverse.** [verified from live env] Live ON while PLANS says flag-dark or ship-shadow:
   - `ZOE_COMPOSE_UI=1`
   - `ZOE_FACE_ID_ENABLED=true`
   - `ZOE_ENROLL_OFFER=on`
   - `ZOE_OMI_SYNC_ENABLED=1`
   - `ZOE_SPEAKER_MODEL=dual`
   - `ZOE_HOLD_THE_FACT`, `ZOE_ASK_WHEN_AMBIGUOUS`, `ZOE_CLEAN_GOODBYE`, `ZOE_QUOTE_RETIRE` and `ZOE_SELF_MODEL` at `enforce`

   Built and "done" but still shadow or off live: `ZOE_RESTRAINT`, `ZOE_STRUCTURAL_CLAIMS`, `ZOE_RECALL_GATE`, `ZOE_STRUGGLE_DETECT`, `ZOE_DELIVERY_LEDGER`, `ZOE_COMMITMENTS`, `ZOE_DISTRESS_GENTLE`, `ZOE_ROUTER_SELFTRAIN`, `ZOE_CONTACTS_API`, `ZOE_OMI_DAY_REVIEW=shadow`.
   The flag inventory itself misses live flags read through a constant: `ZOE_SEAM_RECALL_INJECT` (`zoe_flue_client.py:677`) is **not in `flag-inventory.json`** but is ON live.
5. **There is no off-box backup of anything.** [verified] `zoe-backup.timer` (02:30) writes Postgres dumps and a MemPalace tarball to `~/.zoe-backups` on the same NVMe (`postgres-nightly-backup.sh:11`). The restore check passes weekly. No restic, borg or rclone, no copy of `.env`, models, `~/.zoe`, or the voice corpus.
   - The MemPalace backup script lives outside the repo (`~/scripts/maintenance/mempalace-nightly-backup.sh`). The units `zoe-backup`, `zoe-training`, `zoe-health`, `zoe-merge-queue` and `zoe-self-review` are untracked.
   - "Off-box backups" appears once as an operator item in `beat-the-bar-2026-program.md:47` and **is not on the open-problems ledger**.
6. **The roadmap is about fifteen roadmaps.** PLANS calls `beat-the-bar-2026-program.md` §0 "the authoritative sequence" and "NEXT ACTION (keep this current)", but §0 was last updated **2026-10-04**. Since then roughly 360 PRs merged in 30 days against at least 12 other programme documents (section 6).
7. **The pace is incompatible with the repo's own rules.** [verified, gh]
   - **360 PRs merged in 30 days, 12 a day** (105 in the last 7 days), with median 722 changed lines and 131 PRs at or above 1,000 lines.
   - AGENTS.md says to serialise, keep one or two PRs in flight, target 200 lines with a 400-line ceiling, and fail at or above 1,000 lines.
   - PR #2102 counts 86 cross-reviews this week. Codex hit its usage quota, and polly's OAuth breaks after idle gaps.
   - Validate has a median of 13.9 min, p90 16.2 min. 11 of 38 runs were cancelled (superseded pushes).

---

## 1. Memory stack: as it is

### 1.1 Map [verified unless marked]

| Layer | What | Where |
|---|---|---|
| Vector store | Chroma 1.5.9 "MemPalace" palace at `~/.mempalace` (95 MB; the export reports `mempalace_drawers` **371 rows, 323 live**); the zoe-data venv is py3.12 with chromadb 1.5.9, the **host python3.10 still has chromadb 0.6.3** | `memory_service.py` (4,289 LOC) is the declared single surface |
| Relational | Postgres 17 + pgvector (`zoe-database`, now bound to `127.0.0.1:5432`); people graph (0043), user-model card (0034), open loops, proactive candidates and ledger, restraint (0042), night store (0040), struggle_events (0050), calendars (0052), contacts (0053), Omi (0054/0055): **54 Alembic migrations** | `alembic/versions/` |
| Per-turn writers | regex extractor, turn digest, person extractor (regex + LLM), latent-intent offers, ask-to-remember, hold-the-fact update, quote-retire, named relations | `memory_extractor.py`, `memory_digest.py` (3,501), `person_extractor*.py`, `ask_to_remember.py`, `hold_the_fact.py`, `memory_retire.py`, `named_relations.py` |
| Batch writers | nightly digest + emotional pass + open loops, idle consolidation, weekly merge/contradiction/decay/synthesis/deep-sleep, implicit supersede nightly, REM, music digest, night mind (moments/threads) | `memory_digest.py`, `memory_idle_consolidation.py`, `memory_supersede.py`, `night_mind.py` (2,009) |
| Guards | authority classes (`memory_authority.py` 1,511, default **enforce** `:64,137`), tombstones (300 s in-process, `memory_tombstones.py`), own-words wall, PII scrub, forget ledger + alias + cascade + residue scrub | `memory_authority.py`, `memory_forget_*.py`, `memory_residue.py`, `forget_match.py` |
| Read side | recall packet + recall gate (shadow), evidence recall, exact-words index, structural reader (shadow), user-model card, personalisation hop, provenance answers, restraint filter (shadow) | `recall_gate.py`, `recall_evidence.py`, `exact_words.py`, `structural_reader.py`, `user_model_card.py`, `personalisation_hop.py`, `provenance_answers.py` (1,554), `restraint.py` (1,170) |

**Proportion.** About **37,000 LOC** of memory and companion modules (that figure, plus 5,600 in `proactive/`) serve one real member, 371 drawer rows and 32 people rows. zoe-data as a whole is 296 top-level modules and 147k LOC, with **210k LOC of tests in 690 files** [verified wc]. This is no argument against quality. It is an argument that every new memory "floor" now costs more to keep coherent than it buys.

### 1.2 Write paths: is "31" still right?

- The audit (`docs/research/memory-fidelity-audit-2026-10-05.md` §1, W1-W31) counted 31 writers plus a raw-upsert bypass (`memory_digest.py:1569,1573,1668-1673,1799`; `zoe_agent.py:4560`).
- **Today the count is at least 39, so the audit is stale.** [verified grep]
  - Non-test files calling `.ingest(`: 19. New since the audit: `named_relations.py`, `omi_review_apply.py`, `mempalace_baseline.py`.
  - New writer modules outside `ingest`: `ask_to_remember.py`, `hold_the_fact.py` (edits via `review(edit)`), `memory_retire.py` (quote retire), `structural_claims` / `memory_supersede.retire_by_claims`, `night_mind.py` (night store), `contacts_store.py`, `people_graph.py` (0043 atomic supersede), `provenance_answers` (the fix and forget verbs).
  - Raw collection `upsert` call sites outside tests: **12** (memory_service 6, memory_digest 5, zoe_agent 1).
- Recommendation: re-run the audit's writer table **as a generated artefact**, for example a CI test that greps writer sites and fails on an unregistered one. The authority matrix test exists (`tests/test_memory_authority_matrix.py`). It should enumerate writers from code, not from a doc.

### 1.3 Shadow vs enforce: code defaults vs live

| Flag | Code default | Live (zoe-data env) | Doc expectation |
|---|---|---|---|
| `ZOE_MEMORY_AUTHORITY` | enforce (`memory_authority.py:64`) | unset → enforce | enforce ✓ |
| `ZOE_AFFECT_CONSENT_GATE` | household (`memory_authority.py:160-176`) | unset → household | owner decision ✓ |
| `ZOE_RESTRAINT` | shadow (`restraint.py:1,70`) | unset → **shadow** | shipped 10-09 in PLANS "Done"; enforce never flipped |
| `ZOE_STRUCTURAL_CLAIMS` | shadow (`structural_claims.py:46-55`) | unset → shadow | ledger: enforce needs a bar run first |
| `ZOE_STRUCTURAL_READER` | shadow | unset → shadow | — |
| `ZOE_RECALL_GATE` | shadow | unset → shadow | — |
| `ZOE_QUOTE_RETIRE` | shadow | **enforce** | ledger 10-07 says "SHADOW-only" (stale) |
| `ZOE_SELF_MODEL` | shadow | **enforce** | ledger 10-09 "built but NOT injected" (possibly stale) |
| `ZOE_HOLD_THE_FACT` / `ASK_WHEN_AMBIGUOUS` / `CLEAN_GOODBYE` | shadow (`hold_the_fact.py:34`, `ask_when_ambiguous.py:31`, `clean_goodbye.py:26`) | **enforce** | ledger 10-09 "flips to enforce owed" (stale) |
| `ZOE_STRUGGLE_DETECT` | shadow (`struggle_detector.py:5`) | unset → shadow | PLANS: a shadow week, then re-score |
| `ZOE_NIGHT_MIND` | off (`night_mind.py:41`) | unset in zoe-data; **enforce via the window CLI** | gap register: "off" (wrong) |
| `ZOE_DELIVERY_LEDGER` | shadow (`proactive/lines.py:2`) | unset → shadow | pull-not-push relies on it |
| `ZOE_PROACTIVE_LEDGER` | '' | 1 | on ✓ |
| `ZOE_CORRECTION_APPLY` | False | 1 | — |
| `ZOE_SEAM_CONTINUITY_INJECT` | **true** (`zoe_flue_client.py:1396`) | unset → ON | the ground truth reads "unset" as dark; it is ON |
| `ZOE_SEAM_RECALL_INJECT` | off (`zoe_flue_client.py:658-677`) | true | **missing from flag-inventory.json** |
| `ZOE_OMI_DAY_REVIEW` | off | shadow | — |

Systemic issue [verified]:
- `flag-inventory.json` holds **673 prod ZOE_\* flags**, and zoe-data's live process carries 171 `ZOE_*` variables.
- The inventory records `defaults: ['-']` (could not parse) for most mode flags that go through `env_str` or constants, and silently misses constant-named flags.
- Fix: make each mode flag declare itself in one registry module (name, modes, default, owner doc). Generate the inventory from that registry, not by regex.

### 1.4 The Samantha bar: wired or hand-run?

**It is hand-run.** [verified]
- `validate.yml:211` ignores `tests/samantha_live`.
- `self-hosted-tests.yml:30,197-209` runs it only on manual `workflow_dispatch`.
- No unit or timer runs `samantha_bar.py`, `samantha_day_sim.py` or `samantha_person.py`. Only their offline instrument proofs (for example `test_samantha_person_checkout.py`) are in `ci_safe`.

Last results in `~/.cache/zoe/` [verified]:

| Harness | Last full run | Result |
|---|---|---|
| Bar (`samantha_bar_trend.jsonl`, 67 rows) | 2026-10-09 06:20 UTC on `4bb903d2e`, **dirty tree** | **18 PASS / 3 FAIL / 2 SKIP** of 23. Fails: S10 (one-word change), S21 (Biscuit role), S24 (provenance) |
| Bar, later partial | 10-09 23:12 / 23:35 | S4 FAIL then PASS |
| Day-sim | 2026-10-12 | **FAIL**: 9 of 10 scored pass, 6 asks skipped (1b, 1r, 7b, 7r, 7s, S9c), complete=false |
| Person bench | 2026-10-10 | partial, 3 PASS (arm `none` only) |

**Every full bar run in the trend is `dirty: true`**, so no bar score is attributable to a commit. That is the same evidence-binding defect the voice gate fixed with `--expect-revision`. Nothing has been measured in 6-9 days, during which about 100 PRs merged, many touching memory.

---

## 2. Brain side

### 2.1 Flue 2.x sidecar `labs/flue-zoe-brain-2x` [verified]

- **Size:** 18.1k tracked lines without the lockfile: `src` 5.7k, `test` 7.7k, `parity` 3.5k. `zoe-tools.ts` is 1,287 lines with 24 tools. `streaming.ts` 803, `capped-completions.ts` 642, `context-window.ts` 354.
- **What it reimplements:** a verbatim copy of SOUL in `soul.ts` (the comment says "keep in sync if SOUL.md changes", `agents/zoe.ts:68`). On top of it, the activator, voice-delivery, in-session-context, recall-precedence and emotional-thread doctrines (`agents/zoe.ts:73-149`), plus the persona seam (`persona.ts`), manner (`manner.ts`), the user-model block, early-text, a speculative-turn hold, untrusted-content fencing, a turn guard, and its own SQLite session store (`db.ts`).
- **Callback:** tools call back into zoe-data `POST /api/system/intent-dispatch`. So persona and doctrine text lives in two places (zoe-data `persona_layer.py` / `zoe_core_client.py` for the dormant core lane, and this sidecar). The ledger already records parity drift: a mute is recorded only on the Flue lane, and `self_model` is not injected because `zoe_flue_client.py` "is owned by the latency work".
- **Lab or production:** it is **production in all but name.** `deploy.yml` rebuilds and restarts it on any diff under the path. CANONICAL lists it as the live brain. `labs/AGENTS.md` carries a long carve-out. Yet the same file opens with "Nothing here is imported, built, or executed by any production service" (`labs/AGENTS.md` Purpose), a contradiction inside a binding contract.
  - **Recommendation:** move it to `services/zoe-brain` (and the Telegram bot to `services/zoe-telegram`) in one rename PR with the deploy path retargeted. "Lab" then means lab again, and the voice-gate patterns cover a real service path.
- **Brain lanes still in tree:**
  - `services/zoe-core`: 4.5k lines, "dormant, extend it, don't archive it" (CANONICAL).
  - `services/zoe-data/zoe_agent.py`: 4,569 LOC, the legacy lane.
  - `zoe_core_client.py`: 1,391.
  - `zoe_flue_client.py`: 2,648.
  - `brain_dispatch.py`: 986.
  
  Three lanes are maintained. Failover is off (`ZOE_BRAIN_FAILOVER` default 0), so the two non-live lanes provide no runtime resilience. They only cost parity work on every replay-gated change.

### 2.2 Prompt size

- `docs/architecture/brain-speed-tuning.md:25,286` measured a 3,773-token system prompt plus 27 schemas of about 3,100 tokens, roughly 7.3k tokens.
- This has been mitigated twice: progressive tool disclosure (11→3 schemas, #952) and the prefix cache (`docs/knowledge/little-gemma-engine-bench-2026-10-10.md:64`: the live median is 16 new prompt tokens per turn).
- The remaining risk [inferred]: llama-server runs `--ctx-size 8192` (CANONICAL), and doctrines, card, `[Today]`, recall packet, history and hop all compete inside 8k. Every new "block" (persona, manner, self-model, restraint lines) eats history.
- There is no per-turn budget report in the bar. Add a `first_prompt_n` / packet-size column to the bar and day-sim output.

### 2.3 The 12B night window [verified]

- **Orchestration:** `scripts/night/install_night_window.sh` installs `zoe-night-window.{service,timer}` from `scripts/night/systemd`. The service runs `scripts/night/night_window.sh --jobs digest,dreaming,night_mind,omi_day_review --ngl 34 --ctx 8192 --kv q8_0 --end-by 03:55` at 02:50, takes `/tmp/zoe-brain-window.lock`, stops zoe-data, the router, Kokoro and llama-server, then loads the 12B (QAT, ctx 8192, KV q8_0). It uses `sudo` to drop caches and compact memory six times to defragment for NvMap. The deploy workflow waits out an open window.
- **Is it registered live?** Yes. It is a systemd timer, not an APScheduler job, which is why the ground-truth job list shows nothing named night_mind.
  - The other "missing" nightly jobs are also timers: `zoe-backup` 02:30, `zoe-memory-export` 02:40, `zoe-voice-regression` 04:30, `zoe-backup-verify` Sunday 03:30, `zoe-training` 02:00.
  - The old `zoe-dreaming.timer` is not active (superseded), so the ledger lines from 10-09 saying "not installed / dreaming still enabled" are **stale**.
- **Value delivered:** near zero lately. 0-token night-mind runs, 0-token digests on recent nights, and chat_messages newest on 10-15 (so this is partly no input). The voice stack still goes down nightly for it.
- **Fixes:**
  1. Skip the 12B load when no member has turns since the last run (a cheap SQL precheck).
  2. Report `outcome=noop` instead of `ok`.
  3. Pass `--mode` explicitly.
  4. Alert when decode tps drops below 50% of its trailing median (it hit 1.86).

### 2.4 Router self-train [verified]

- The flag is unset live, so it is dark (`main.py` default `'off'`).
- The shadow corpus `services/zoe-data/data/router_head_shadow.jsonl` holds **10,857 decisions** (3.9 MB, live through today).
- The last dry-run journal is `data/router_selftrain/runs/20260726T060749Z.json`, **12 weeks ago**. The 07-14 run was rejected with a mined candidate of only 6 examples.
- PLANS says "accumulate more traffic and re-mine". The traffic is there and nobody re-mined. This is an S-sized agent task: a dry-run in a 1.6 GB window. It needs a RAM window, which is the actual blocker.
- The same file is an unforgotten store of user utterances (finding 0.1).

---

## 3. Self-evolution and harness: the honest picture

| Generation | What | LOC (tracked unless noted) | State today |
|---|---|---|---|
| G0 (Apr-May 2026) | `~/bin/nightly-training-cycle.sh`, `zoe-self-review.py`, `zoe-apply-improvements.py`, `~/training/*` (scrub/train/eval/merge) | **untracked**, about 200 KB of scripts | **RUNNING nightly and weekly**; duplicates OpenClaw turns, proposals unread → **dead weight with privacy cost** |
| G1 OpenClaw | `routers/openclaw.py` (155, mounted at `main.py:52,2217`), `openclaw_manager/ws/maintenance` (1,114), `proactive/triggers/openclaw_trigger.py` (111, **registered** `main.py:1282-1288`, calls Hermes `:8642`), docs/skills archive; 219 files mention it; about 7.1k LOC of named files | gateway INACTIVE/disabled; "delete" decision 2026-07-22 (`multica-executor-migration.md` §5) | **dead weight**; the trigger probes a dead port each slow loop; the runtime health probe logs `hermes=False openclaw=False` every 5 min |
| G2 Hermes | `chat_hermes_stream.py`, `hermes_http.py`, `hermes_model_profiles.py` (807); 257 files mention it; `~/.hermes` 6.4 GB; zoe-data, zoe-health and voice-regression units still load `~/.hermes/.env` | INACTIVE; "retires" (§5) | **dead weight**, but **AGENTS.md still says "Hermes is Zoe's default engineering and browser agent"** (Hermes-First Delegation section): the root contract contradicts the decision |
| G3 Multica | about 55 files / 15.3k LOC + 16.8k test LOC (`multica_*.py`, `executors/kanban_adapter.py`, `pipeline_*`, `worktree_bootstrap`, `multica_triage_judge.py` untouched since 08-03) + 2 containers + heartbeat timer every minute | **dispatch paused since 2026-08-04** (kill-switch mtime); 105 rows; 8 autopilots registered | **half-alive:** the autopilots schedule *household* jobs (`multica_autopilot_sync.py:141` morning check-in, `:163` evening wind-down, `:217` reminder scan, `:230` platform health), so the product's proactive schedule depends on the engineering board being up |
| G4 Omnigent | `modules/omnigent` (1.3k) + `cross_review.sh` (338) + polly | container up 4 days, **1.1 GiB RSS** (largest container), used for cross-review (86 reviews this week) | **operating** (the review tier), but auth-fragile (#2102) |
| G5 Flue executor | `labs/flue-executor` (2.7k), unit template only | no unit installed live, `ZOE_EXECUTOR_DISPATCH` dry; last touched 07-31 | **designed, never ran in prod** |
| G6 Greploop / local merge queue | `services/zoe-data/greploop_guard.py` (**2,390 LOC, 98 KB, inside the product package**) + `scripts/maintenance/greploop_guard.py` CLI; **`zoe-merge-queue.timer` every 10 min** (untracked unit) merges auto-merge-labelled PRs; admin routes `/engineering/guard/*` in the product API (`routers/system.py:1429-1453`); `mcp_server.py:2116` | **running** | works, but an engineering bot lives in the household API process and package |
| G7 Autoresearch | `autoresearch_bridge.py` (438) + `routers/autoresearch.py` (84, mounted `main.py:2256`) | dry-run CLI | small; candidate to move out of zoe-data |
| G8 In-product self-improvement | `struggle_detector/signals/digest` (1,689, shadow), `evolution_notice.py` (750), `evolution_autonomy.py` (64), `night_mind` proposals (designed), `zoe-evolution-proposal-contract.md`, `GOVERNED_SELF_EVOLUTION.md`, strategy plan/status (June) | Phase 1 shadow | the VISION's real self-evolution path; keep |
| Dev agents on the box | Claude CLI sessions (about 1.2 GB RSS across processes), codex app-servers (leaking, #2102), serena 248 MB | live now | eat the same 785-930 MB headroom the voice stack needs |

**Count:** 8 harness generations coexist. Operating usefully: Omnigent cross-review, the greploop merge queue, and the struggle detector (shadow). Dead weight still executing: G0, the G1 trigger and router, and the G2 env loads and health probes. Half-alive: Multica, which carries household scheduling.

**Recommended consolidation:**
1. Delete G0 (stop the timer and cron, purge `~/training/data` duplicates under the forget rule).
2. Finish the OpenClaw delete: router, trigger, manager, health probe, `~/.openclaw` sessions after an export decision.
3. Drop the `~/.hermes/.env` loads and fix the AGENTS.md Hermes-first section.
4. Move household autopilot jobs (check-in, wind-down, reminder scan) onto zoe-data's own scheduler so Multica can stay paused or retire without breaking the product.
5. Move greploop, the merge queue and autoresearch out of `services/zoe-data` into `tools/engineering/` or `scripts/`, and remove the engineering routes from the product API.

---

## 4. Ops

| Area | State [verified unless marked] | Issue / recommendation |
|---|---|---|
| Deploy | `deploy.yml` (668 lines) on the single self-hosted Jetson runner; post-merge; voice artifact bound with `--expect-tree-of`; waits out the night window; 30 of 30 recent runs green, median 30 s (most are docs-only skips) | sound design. The runner is not ephemeral, has no resource cap and serves a public repo (ledger, owner) |
| Systemd | 16 user timers. Tracked templates: 46 files in `scripts/setup/systemd`. **Untracked live units:** `zoe-training`, `zoe-backup`, `zoe-health`, `zoe-merge-queue`, `zoe-self-review`. **Template drift:** live `zoe-data.service` carries `HERMES_FAST_PATH`, `JETSON_AGENT_MODE`, `GEMMA_SERVER_URL`, `MEMPALACE_DATA_DIR` absent from the template; the kokoro drop-ins differ (tracked `70-start-timeout.conf`, live `backend.conf` and `memory.conf`). Cruft: about 22 retired unit files and about 13 `.bak` copies in `~/.config/systemd/user` | one "unit drift" check (template vs live, report-only) in `zoe_ground_truth.sh`; track or delete every live unit |
| Memory protection | drop-ins exist for zoe-data (8), llama-server (4), kokoro (4); swap 7.9 GB used, MemAvailable 785-931 MB; the largest non-voice consumers are zoe-omnigent 1.1 GiB and dev-agent CLIs | the RAM floor is still the binding constraint (the B0.1 gate was 700/800 MB idle). Dev agents and Omnigent on the same box are the cheapest RAM to reclaim |
| Quiet hours | AGENTS.md says 01:50-04:55; inside it run `zoe-training` 02:00, `zoe-backup` 02:30, `zoe-memory-export` 02:40, night window 02:50, `zoe-voice-regression` 04:30, serena-pregate 04:15 | the training job should not exist; the rest is acceptable but undocumented as one schedule |
| Backups | on-box only (§0.5); no secrets or `.env` backup; the credential vault design names the411 droplet as a future target | **top owner-agent item**: encrypted restic to the411 or an external disk, covering Postgres dumps, the palace tar, `.env*`, `~/.zoe`, the voice corpus and the router corpus |
| Secrets | **115 `.env*` copies** across `~/assistant`, 401 `.claude/worktrees` and 159 `~/.worktrees`; 11 `.env.bak*` beside the live zoe-data `.env` (one at 664); repo-root `.env` 664; `ssl/zoe.crt` 777; `.env.openclaw-secrets`; retired `labs/flue-zoe-brain/` and `labs/flue-zoe-telegram/` dirs (with `.env`) still on the live disk | worktree reaping should delete `.env` copies; delete the `.bak` env files after a backup; chmod 600 (ledger has it, owner) |
| Logs | `~/.zoe-logs` 130 MB with an hourly rotation timer ✓; docker: 6 of 11 containers have `max-size 10m`, **5 have none** (cloudflared, ytmusic-potoken, multica-backend/web, smb-drop); **0 of 11 have a memory cap** | ledger item half-done; finish it in compose |
| Postgres / HA bridge bind | now `127.0.0.1:5432` / `127.0.0.1:8007` ✓ (ledger line 10-04 stale on this half) | zoe-data still `--host 0.0.0.0 --port 8000` (`systemctl show zoe-data ExecStart`), the LAN-exposure ledger item stays open |
| Watchdogs | 5 overlapping (cron `zoe-watchdog.sh` every 5 min, `zoe-health.timer` every 5 min, crash-loop-watch every minute, telegram watchdog every minute, multica heartbeat every minute). `zoe-watchdog.sh` and `zoe-health-check.sh` (both untracked in `~/bin`) probe dead Hermes `:8642` / OpenClaw `:18789` and **do not probe the live brain `:3579` or Kokoro `:10201`** | collapse to one tracked health unit that probes the actual canonical spine |
| Dependencies | zoe-data on the py3.12 venv ✓ (B0.7 done); Kokoro on a py3.10 venv; host python 3.10 with chromadb 0.6.3 (trap for scripts); Node v22.23.3; **Dependabot open: 3** (all `scripts/setup/requirements-kokoro.txt`: 1 medium, 2 low); `mempalace` still pinned in both requirements files although production does not import it (ledger) | fine overall; drop the mempalace pins and the host chromadb |
| Containers | `livekit` Exited (137) 5 days ago (ledger: a week, owner decision); `zoe-music-assistant` Exited (0) 13 days ago (idle-reaped, by design) | decide livekit |

---

## 5. CI and the review pipeline

- **Lanes** [verified `validate.yml:210-260`]:
  - Marker-based: `services/zoe-data/tests -m ci_safe` (`-x`); `tests/unit -m ci_safe`.
  - Full directory: `services/zoe-auth/tests`.
  - Enumerated: 2 HA-bridge files, `tests/integration/test_people_mempalace_sync.py`, `test_people_graph_postgres.py` on real PG.
  - Plus `deps-resolvable` and `secret-scan`.
  - 820 files carry the `ci_safe` marker. The self-hosted catch-all runs nightly.
- **Duration:** median 13.9 min, p90 16.2 (38 runs); 11 of 38 cancelled by newer pushes.
- **Flakiness signs:**
  - 61 commits since 09-18 match flaky/race/pollution/nondeterministic.
  - `~/.zoe-logs/nondeterministic-test-failures.jsonl` has 36 records.
  - The ledger has 12 "Tests" lines (cross-lane `sys.modules` pollution; `test_memory_physical_erase.py` red about 1 run in 4; brain tool-choice at temp 0.7 failing about 14%).
  - The box hung on 2026-10-13 under 5 parallel agent test runs, which led to the `flock /tmp/zoe-test-lane.lock` rule.
- **Is the review pipeline proportionate?** Not at this pace. Per PR there is:
  - the deterministic gate (about 14 min);
  - cross-review (polly → codex → pi fallback; 86 this week);
  - the Codex bot on every push (owner wants it kept);
  - optional Copilot;
  - Greptile for big or load-bearing PRs, which now means most of them (median 722 lines, so the warn ceiling of 400 is hit by more than half);
  - the voice-gate replay (serial, single artifact slot, so voice merges are strictly serial).
  
  Median open→merge is 1.9 h, p90 16 h. The **cost is not per-PR friction, it is volume.** 12 PRs a day × (CI + 1-3 reviews + Codex every push) on a solo-owner repo saturates the Codex quota, Omnigent auth and the Jetson runner, and produces 30 ledger lines a day of "found in review".
  - Proportionate shape: batch work into 2-4 PRs a day, each closing a coherent slice. Keep the deterministic gate plus one cross-vendor pass. Reserve Greptile and voice-gate for load-bearing paths.

---

## 6. Roadmap coherence

### 6.1 How many programmes? I count 15 live "plans of record"

| # | Document | Last change | Status claim |
|---|---|---|---|
| 1 | `docs/PLANS.md` (232 lines, 25 sections) | 10-18 | human index |
| 2 | `docs/architecture/beat-the-bar-2026-program.md` (1,688) B0-B10 | 10-12 (§0 state **10-04**) | "authoritative sequence" |
| 3 | `samantha-evolution-plan.md` (1,118) W0-W16 + `samantha-evolution-packets.md` | 10-11 / 10-04 | detail plan |
| 4 | `zoe-memory-samantha-buildplan.md` | **08-10** | "has the NEXT ACTION pointer" |
| 5 | `samantha-brain-blueprint-2026-10-09.md` (945) waves | 10-08 | DRAFT, "nothing is a decision until the owner says so" |
| 6 | `docs/research/samantha-gap-register-2026-10-09.md` (10 gaps) | 10-09 | ranking |
| 7 | `docs/research/best-ideas-register-2026-10-09.md` (683; BM/BP/BV/BS/BH, D1-D10) | 10-09 | ranking |
| 8 | `docs/research/what-gets-us-closer-2026-10-07.md` | 10-07 | ranking |
| 9 | `docs/research/tools-best-practice-program-2026-10-05.md` ("program item N") | 10-05 | programme |
| 10 | `memory-fidelity-audit-2026-10-05.md` P1-P3 | 10-05 | plan |
| 11 | `state-of-zoe-review-2026-09-25.md` §9 sequence + 90-item register | 09-30 | historical, "not a contract" |
| 12 | `tech-debt-remediation-plan.md` | 09-25 | Wave 4 not started |
| 13 | `desktop-ui-overhaul-plan.md` waves 0-6 + F | **07-22** | "waves not started" |
| 14 | `zoe-flue-integration.md` + `multica-retirement-gates.md` + `multica-executor-migration.md` | 08-10 / 09-27 | phases |
| 15 | `docs/knowledge/open-problems.md` (385 items) + agent-memory "Samantha 100%" and "10/10" programmes (not in repo) | 10-17 | ledger |

**One roadmap or eight?** Fifteen. The gap register, best-ideas register and blueprint (5-8) each re-rank the same Samantha space, and #2 and #3 each carry their own "NEXT ACTION".

### 6.2 Contradictions [verified]

1. PLANS:32 says "the authoritative sequence is the program tracker ... its §0 is the NEXT ACTION", but §0 is dated 2026-10-04 and since then the night window, Omi, contacts, calendar, identity, relay and vault all landed outside it.
2. PLANS:32 says "Do not start new feature work until B0.1 has cleared the 700/800 MB gates". MemAvailable today is 785-931 MB, and feature work never stopped.
3. AGENTS.md "Hermes-First Delegation: Hermes is Zoe's default engineering and browser agent" contradicts `multica-executor-migration.md` §5 ("Hermes retires") and the live state (inactive).
4. `labs/AGENTS.md` Purpose ("Nothing here is ... executed by any production service") contradicts its own Forbidden carve-out and `deploy.yml`.
5. VISION Direction says "Pi (zoe-core) is the brain" and "MemPalace memory". Live, the brain is the Flue sidecar, zoe-core is dormant, and the mempalace package is not imported by production (ledger 10-05).
6. The gap register and PLANS say the night mind is "off". Live, it runs in enforce nightly via the window (§2.3).
7. PLANS presents Ask-card generative tiles (`ZOE_COMPOSE_UI`), voice enrolment, face ID and Omi sync as flag-dark. Live, all are ON (§0.4).
8. Ledger lines say person-half flags await enforce and quote-retire is shadow. Live, they are enforce. In the other direction, PLANS "Done" lists restraint and struggle, which are still shadow live.
9. AGENTS.md size and serialisation rules vs the measured 12 PRs a day and median 722 lines (§5).
10. Ledger rule "delete the line when it is fixed" vs at least 21 lines kept with RESOLVED / FIXED / CODE LANDED / DECIDED / (done) in them.
11. CANONICAL says the zoe-core lane is "Wired + tested — not retired; extend it". The ledger shows core-lane parity gaps accumulating (mute, voice lane). Three lanes maintained, one served.

### 6.3 Active plans with no activity in 30 days [verified git log since 09-18]

- **Desktop UI overhaul:** plan doc untouched since 07-22; "waves not started". `dist/*.html` has 16 commits since, so some touch work happens off-plan.
- **Ask-card conversation:** plan untouched since 07-17; `ui_compose.py` untouched since 07-07, yet `ZOE_COMPOSE_UI=1` is live.
- **TRIAGE-JUDGE TT1/TT2:** `multica_triage_judge.py` untouched since 08-03 (and Multica is paused).
- **Flue convergence / executor Phase 2:** `labs/flue-executor` untouched since 07-31; `multica_poll_dispatch.py` since 06-17.
- **Tech-debt Wave 4** (chat split / typed config): plan untouched since 07-07.
- **Router self-train re-mine:** last dry-run 07-26.
- **Agent + tooling backlog items #4, #6, #8:** since June.
- **Samantha-grade memory buildplan:** pointer doc untouched since 08-10.

### 6.4 Ledger triage: 385 lines, about 12 themes (keyword clustering on the "where" field; counts approximate)

| Theme | Lines | Owner-blocked | Agent-doable | Notes |
|---|---|---|---|---|
| Memory write, authority, extraction, digest, recall | ~98 | ~15 | ~80 | the bulk; most are follow-ups from review rounds on 10-05 and 10-09 |
| Restraint / person-half / distress / proactive delivery | ~47 | ~11 | ~29 | many are concurrency edges (per-member vs per-session state) |
| Voice / STT / TTS / wake / barge | ~35 | ~9 | ~25 | wake FA/FR unmeasured; keyterms off; Silero chunking |
| Benches and harness instruments | ~33 | ~4 | ~28 | instruments with no negative control; dirty-tree runs |
| Night window / 12B / night mind | ~30 | ~8 | ~17 | several now stale (installed, merged) |
| UI / panel / calendar / contacts / Bluetooth | ~29 | ~7 | ~17 | |
| Omi pendant | ~27 | ~10 | ~16 | 10-14 to 10-17 |
| Infra / Docker / deps / deploy / landing | ~24 | ~10 | ~8 | |
| Speaker ID / identity / enrolment | ~21 | ~9 | ~12 | owner re-enrolment blocks the shadow week |
| Security / exposure / secrets | ~19 | **~14** | ~5 | Telegram token rotation, Cloudflare Access on SSH, HA tokens, runner, chmod |
| Forgetting / privacy / retention | ~16 | ~5 | ~8 | **does not list** the router log, the training copy or the OpenClaw/Hermes stores |
| Tests / CI hygiene | ~12 | 0 | ~12 | pollution class, time bombs |

- **Owner-blocked: about 100 lines (26%). Agent-doable: about 260 (68%).**
- Arrival rate: 107 lines on 10-09 alone, about 30 a day on build days. The ledger grows faster than it drains, and it is a review byproduct, not a backlog.
- **Missing from the ledger:** off-box backups; G0 training duplication; forget not reaching side stores; the night window's vacuous green.

### 6.5 Spot-check: 13 ledger lines (10 asked for, 3 extra)

| Ledger line | Live / code now | Verdict |
|---|---|---|
| 10-04 flag inventory has no pre-commit hook | `.pre-commit-config.yaml:38` `zoe-flag-inventory-fresh` | **fixed, line stale** |
| 10-05 `samantha_bar.py` has no `--only` | `samantha_bar.py:77,475` | **fixed, line stale** |
| 10-09 12B window not installed; dreaming timer enabled | timer runs nightly since at least 10-12; dreaming timer inactive | **fixed, stale** |
| 10-09 night-mind script only on an unmerged branch | `scripts/maintenance/zoe-night-mind.py` on main | **fixed, stale** |
| 10-04 Postgres / HA bridge on all interfaces | `127.0.0.1:5432`, `127.0.0.1:8007` | **half-fixed** (log/mem caps still open) |
| 10-04 voice-gate not a required check | now deliberate by design (AGENTS.md "INFORMATIONAL, fail-closed, not branch-protected") | **decided; close or reword** |
| 10-05 zoe-data loads `~/.hermes/.env` | `systemctl show -p EnvironmentFiles` still lists it | **open** |
| 10-05 `.env` 664 / `ssl` 777 | stat shows 664 / 777 | **open** |
| 10-05 landing scripts unversioned in `~/.zoe/agent-tools` | 86 files there, none tracked | **open** |
| 10-04 livekit Exited (137) | still Exited (137) | **open** |
| 10-05 mempalace pinned, unused | `requirements.txt:105`, `requirements-py312.txt:64` | **open** |
| 10-07 `ZOE_QUOTE_RETIRE` shadow-only | live `enforce` | **stale** |
| 10-09 person-half flags ship shadow, flips owed | live `enforce` ×3 | **stale** |

### 6.6 Proposal: collapse into one living roadmap, weekly cadence

- **One file, `docs/ROADMAP.md`, replacing PLANS' role and every "NEXT ACTION" pointer.**
  - Max about 150 lines. Exactly three horizons: **Now** (this week, ≤5 items), **Next** (≤10), **Later** (a link list).
  - Each item: outcome in plain words · owner or agent · flag and its *live* value · acceptance (the bar or gate id) · PR(s).
  - Programme docs (beat-the-bar, evolution plan, blueprint, registers) become **evidence and design records** linked from items. Their own "NEXT ACTION" / §0 sections are deleted.
- **A generated live column.** A script (extend `zoe_ground_truth.sh`) emits `docs/knowledge/live-state.json` (flags as set in the zoe-data, sidecar and Pi envs; timers; last bar / day-sim / replay results with commit and dirty bit). A CI check flags any roadmap item whose stated flag state disagrees with the last snapshot. This kills contradictions 6-8 at the source.
- **Weekly cadence (Monday, 30 min, owner + one agent):**
  1. Regenerate live-state.
  2. Run the bar and day-sim on a clean checkout of main (bound to the commit).
  3. Move ≤5 items into Now.
  4. Triage this week's new ledger lines into roadmap items, `wontfix` or delete.
  5. Delete every RESOLVED line.
  6. Publish one paragraph of "what changed for the household".
- **Ledger rules:**
  - A line older than 21 days with no roadmap item is either promoted or deleted with a one-word reason in the commit.
  - "Owner" lines are mirrored into one owner pack, with a single list in the roadmap's "Owner" section.

---

## 7. Docs mass and retention

- **Size** [verified]: 422 docs files, about 116k lines. Breakdown:
  - `knowledge/`: 187 files, 43.9k lines.
  - `research/`: 63 files, 29.2k lines; 58 files added since 09-25.
  - `architecture/`: 69 files, 21.4k lines.
- **Is anyone reading them?** Every research file has at least one inbound reference, but almost all of those come from other agent-written records (the blueprint cites five open-PR records), so agents read them. The owner-facing surface is PLANS, VISION and the operator packs.
  - `docs/research/` has **no `index.md` and no `AGENTS.md`**, although `docs/AGENTS.md` and the root OKF rule require an index per bundle directory.
- **Retention rule, DOX/OKF-consistent, nothing lost (git keeps bytes):**
  1. **Records are immutable evidence; summaries are living.** A research record keeps its frontmatter plus a `status: open | absorbed | superseded-by:<path>` field.
  2. **Absorb-then-retire at 30 days.** When a record's conclusions are absorbed into a living doc (ROADMAP item, `knowledge/<topic>.md`, ADR), the PR that absorbs them sets `status: absorbed` and **deletes** the record from the tree, leaving one line in `docs/research/index.md` with the title, date, final commit sha and `git show <sha>:path` recovery. Evidence stays recoverable; the working tree stays readable.
  3. **One topic, one living knowledge page.** For example, memory has 15 `knowledge/memory-*` / restraint / recall / structural pages. Consolidate them into `knowledge/memory/index.md` + ≤5 pages (write path & authority; read path & recall; forgetting; night mind; benches) and register the bundle in `docs/AGENTS.md`.
  4. **Dated "addendum" sections are banned in living docs** (state-of-zoe has 2). A new state is a new snapshot record; the living doc is overwritten.
  5. **Cap:** `docs/research/` at or below 25 open records. A new record over the cap must absorb or retire one. Enforce it with a validator in `tools/audit/validate_structure.py` (count + `status` field + index entry).

---

## 8. Top 10 recommendations (this lane)

| # | What | Size | Who | Risk if not done / risk of doing |
|---|---|---|---|---|
| 1 | **Make forget reach every store**: add `router_head_shadow.jsonl` (redact `utt_text` on forget or stop logging raw text), app logs, `~/training/*`, `~/.openclaw`/`~/.hermes` session stores and backups to the cascade or delete them; add a "where do household words live" inventory test | M | agent (deletion of old stores: owner OK) | breaks the owner's "forgotten means forever" today; low risk to do (append-only logs) |
| 2 | **Delete harness generation G0** (`zoe-training.timer`, cron self-review, `~/bin` training scripts), purge the duplicated 10.6k-turn copy | S | agent + owner OK | it runs nightly in the quiet hours and grows private data; near-zero risk |
| 3 | **Off-box encrypted backups** (restic to the411 or an external disk: pg dumps, palace tar, `.env*`, `~/.zoe`, voice corpus, router corpus) + a restore drill in the weekly verify; put it on the ledger | M | owner (target + key) + agent (script, unit tracked) | one NVMe failure loses Zoe's memory and every secret |
| 4 | **Night window honesty**: skip when no new turns, `outcome=noop`, explicit `--mode`, decode-drop alert, `omi_day_review` actually scheduled or removed; fix the gap register/PLANS claim that night mind is off | S | agent | nightly voice outage for no work; false confidence |
| 5 | **One ROADMAP.md + generated live-state + weekly cadence** (§6.6); retire every other "NEXT ACTION" | M | owner decision, agent build | without it, 15 plans keep diverging and live flags contradict docs |
| 6 | **Bind Samantha evidence to commits and schedule it**: refuse dirty-tree runs for the trend, run bar + day-sim weekly on a clean main checkout (post night window), store revision; add prompt-size column | S-M | agent | memory changes ship unmeasured (last full bar 10-09) |
| 7 | **Finish the OpenClaw/Hermes removal** (router + trigger + manager + health probes + `~/.hermes/.env` loads + AGENTS.md Hermes-first text) and **move household autopilots off Multica** onto zoe-data's scheduler | M | agent (gated PRs) | dead calls each loop; product scheduling depends on a paused engineering board |
| 8 | **Get engineering bots out of the product process**: move `greploop_guard.py`, autoresearch, the engineering routes and the merge-queue unit to `tools/engineering/`, track the unit | M | agent | a review bug can 500 or slow the household API; untracked merge bot |
| 9 | **Flag registry**: one module declares every mode flag (name/modes/default/doc); inventory generated from it; CI fails on unregistered reads; collapse the 673 flags by retiring flags that have been `enforce`/on for >30 days (promote to code) | L | agent | two inventory misses already; flag debt makes "what is live" unknowable |
| 10 | **Throttle the PR firehose**: 2-4 coherent PRs/day, honour the size rule (or change it honestly), one cross-vendor pass, Greptile only for load-bearing; rename `labs/flue-zoe-brain-2x` → `services/zoe-brain` in the same quiet week | S (policy) + M (rename) | owner policy, agent rename | review quota and auth failures, ledger growth of about 30/day, review rounds introducing bugs (#1560 pattern) |

Also worth doing, smaller:
- chmod / `.env.bak` cleanup, plus worktree `.env` reaping (115 copies);
- docker log + memory caps on the 5 + 11 containers;
- the livekit decision;
- drop the mempalace pins and the host chromadb 0.6.3;
- re-mine router self-train with 10.9k decisions in a RAM window;
- `docs/research/index.md` + status fields;
- regenerate the memory writer map from code (at least 39 writers, not 31).

---

### Appendix: commands and paths used (for re-verification)

- Live flags: `tr '\0' '\n' </proc/$(systemctl --user show zoe-data -p MainPID --value)/environ | grep ^ZOE_`
- Timers: `systemctl --user list-timers --all`; units under `~/.config/systemd/user/`; cron via `crontab -l`
- Night window: `journalctl --user -u zoe-night-window | grep NIGHT_WINDOW_SUMMARY`; `~/.zoe-logs/night-window.log`
- Training duplication: `~/training/logs/nightly-summary.log`; `~/training/scrub_sessions.py:42` (`SESSIONS_DIR = ~/.openclaw/agents/main/sessions`)
- Bar: `~/.cache/zoe/samantha_bar_trend.jsonl`, `samantha_day_sim_last.json`, `samantha_person_last.json`
- PR stats: `gh pr list --state merged --limit 700 --json mergedAt,additions,deletions,changedFiles,createdAt`
- Ledger clustering script: scratchpad `cluster.py`
