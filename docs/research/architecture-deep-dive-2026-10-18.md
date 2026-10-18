---
type: research
title: Architecture deep dive 2026-10-18 - the whole of Zoe, the roadmap, the field, and the household communications layer
date: 2026-10-18
status: research-only; nothing built, nothing changed on any live system. Handed to the "Zoe comprehensive review and modernization" session as its task list.
description: Owner ask (2026-10-18) - "a comprehensive architectural deep dive on Zoe and the roadmap, look at everything, find any issues, things that could be better, systems that don't work together, look at other projects online, look at the new Apple device, and calls, messages, PA across panels, video calls and calls to other panels outside the house, with the411.life as the account, relay and backup server." Six evidence lanes (three read-only code audits with live reads, three web lanes) folded into one ranked list. The call and video server the owner remembered is LiveKit (in compose since the first commit, on demand today) beside an early Matrix router that never had a homeserver.
evidence: docs/research/architecture-deep-dive-2026-10-18/ (the six lane reports, every claim path:line and tagged verified / inferred / secondary / unverified). Live reads this session - scripts/maintenance/zoe_ground_truth.sh, /proc/<zoe-data pid>/environ (names only), docker inspect, systemd timers, ~/.zoe-logs/night-window.log, ~/.zoe/night-reports, gh (read-only).
builds_on: docs/knowledge/state-of-zoe-review-2026-09-25.md (the last full review), docs/architecture/beat-the-bar-2026-program.md (the tracker), docs/architecture/panel-anywhere.md (the relay design, F0 lab passed 81/81), docs/architecture/communication-preferences.md, docs/research/telegram-calls-voice-photos-2026-10-04.md, docs/research/samantha-gap-register-2026-10-09.md
---

# Architecture deep dive (2026-10-18)

> Read with [VISION.md](../VISION.md) and [CANONICAL.md](../CANONICAL.md). The rocks (Gemma 4 E4B + MTP, Moonshine v2 Medium, Kokoro) are fixed; nothing here proposes a swap. Every finding was read in code or live state by a lane agent and the load-bearing ones were re-checked by the author with a negative control (section 1). Household names do not appear.

## 0. Answer first

1. **Zoe is a strong single-household companion with a one-process, one-lane spine that is now carrying far more than it was built for.** One uvicorn worker on `:8000` runs the voice path, chat, memory, the proactive engine, Omi transcription, nightly LLM loops, a PR-review harness, a Multica poll loop and `git push`. Four of the ten structural findings in section 3 are direct consequences of that shape.
2. **The highest-value small fix is a blocking call on the event loop.** With `ZOE_ROUTER_HEAD=active`, every routed voice turn makes a synchronous `urllib` call to the router sidecar (about 400 ms by its own comment) from inside async handlers at four sites. Panel sockets, `/health` and the brain's intent-dispatch callbacks all stall meanwhile. One `asyncio.to_thread` per site, replay-gated. [verified]
3. **Three correctness and safety defects need fixing before any new feature:** list and calendar fallbacks that write as `family-admin` with unescaped user text, 17 music mutation endpoints with no auth dependency plus a settings dump to guests, and a second voice lane (`/ws/voice/`) that skips the distress, provenance and identity tiers. [verified]
4. **"Forgotten means forever" is true for the palace and Postgres and false for the box.** The router shadow log (10,858 rows with the user's words), a 9 MB training copy, 1.2 GB of OpenClaw sessions, 6.4 GB of Hermes state and the on-box backups are outside the forget cascade. A retired training job still appends 55 duplicate turns every night at 02:00, inside the quiet hours. [verified]
5. **There is no off-box backup of anything.** Nightly dumps land on the same NVMe. One disk failure loses Zoe's memory and every secret. This is the top owner-plus-agent item and it is not on the ledger. [verified]
6. **The night window is green and does almost nothing.** It stops the voice stack for 3 to 13 minutes, reports `outcome=ok`, and the night mind and digest used 0 tokens on recent nights; decode fell to 1.86 tok/s on 10-18. The night mind runs in enforce (the CLI default) while PLANS and the gap register say it is off. [verified]
7. **The docs and the live flags disagree in both directions, and there are fifteen plans of record.** Flags documented dark are live (face ID, enrol offer, Omi sync, dual speaker model, five person-half guards at enforce); items marked done are still shadow (restraint, structural claims, recall gate, struggle detector, delivery ledger). The tracker's "NEXT ACTION" is dated 10-04; 361 PRs merged since 09-18 at a median of 723 changed lines against a 400-line ceiling. [verified]
8. **Household communications: Zoe has none between people.** Every existing primitive goes one way, from Zoe to one person. The brain's 22 tools cannot announce, message or call. The announcement lane assumes one speaker. Panel targeting is a client-side filter that would leak between two panels. zoe-data cannot send a Telegram message because the token sits under the wrong environment name. [verified]
9. **The call and video server you remembered is LiveKit, and it is not dead.** It has been in compose since the first commit (2025-11-17), the container starts on demand for the Ask-card "stay talking" mode and reaps itself after 300 s idle (the `Exited (137)` is a `docker stop`, `OOMKilled=false`). Two ops records misread it as dead. Beside it, a 548-line Matrix router with household users, rooms, a Zoe bot and QR onboarding lived in zoe-core, pointed at matrix.org, and never had a homeserver. [verified]
10. **Recommended comms architecture: a hybrid.** Native Opus-over-WebSocket on the LAN for PA and push-to-talk intercom (about 700 lines, zero Orin RAM). Matrix on the411 for everything between people: a homeserver, Element X on phones, Element Call with a LiveKit SFU on the411 whose media is end-to-end encrypted, one bot user per household as the Zoe-to-member and Zoe-to-Zoe primitive. restic to a rest-server on the411 for backups. The frp relay stays the panel path. Two decision gates before committing: the411's free RAM, and a one-week ringing test on the family's real phones. Section 8.

The Apple device is **HomeView** ($299, announced 2026-10-13, ships 2026-11-13, not sold in the EU). What it has that Zoe lacks, ranked, is in section 7; the top three are phone presence as a second factor for private results, whole-home intercom that reaches phones and watches, and FaceTime-class video. Zoe's edge is the whole private, uncapped, Android-friendly household memory that Apple gates behind a nearby unlocked iPhone.

## 1. Method and evidence

- Six lanes ran read-only against this worktree (`62f1e7e83` = `origin/main`) and the live box. Lane A: the zoe-data request path and structure. Lane B: the voice, panel, identity and comms substrate (with live SQL reads and `docker inspect`). Lane C: memory, brain side, harness generations, ops, CI and roadmap coherence. Lane D: the Apple device. Lane E: the open-source and commercial field since 2026-09-25. Lane F: the comms stack options with Matrix and Element as the incumbent. The full reports, with every path:line, are in [architecture-deep-dive-2026-10-18/](architecture-deep-dive-2026-10-18/index.md).
- The author re-ran the following with a negative control: the synchronous router call and its four async call sites; the `family-admin` fallback command builder; the 17 unauthenticated music routes (0 `Depends` in the file); the `core` default in `brain_dispatch.py:164`; the guest settings dump; the Telegram env-name mismatch against the live process environment (names only); the 48:1 broadcast ratio and `panelMatches`; the 22 brain tools; `docker inspect livekit`; the night-mind mode default and the 10-18 night report; the shadow-log fields; the PR statistics via `gh`.
- Codebase-memory was empty and its index attempt crashed the server (the box had about 785 MiB free), so the lanes used grep and file reads; Serena was left alone as the shared single-lane server. No test was run, nothing was restarted, the Pi was not touched.
- Labels used throughout the lane reports: `[verified]` read in code or live state; `[inferred]` reasoned from verified facts, not executed; web material `[P]`/`[verified]` primary page read, `[S]`/`[secondary]` snippet or aggregator, `[K]`/`[unverified]` from memory.

## 2. Live ground truth at the time of the review

From `zoe_ground_truth.sh` and the live reads (2026-10-18, box up 4 days 13 h):

- Active: zoe-data `:8000`, llama-server `:11434`, kokoro-tts `:10201`, functiongemma-router `:11436`, flue-zoe-brain-2x `:3579`, flue-zoe-telegram `:3582`, serena-mcp, github-runner. Inactive: hermes-agent, openclaw-gateway.
- Brain lane: `ZOE_BRAIN_BACKEND=flue`, `ZOE_ROUTER_HEAD=active`, `ZOE_EXPERT_MODE=active`, `ZOE_FLUE_STREAM_ENABLED=1`, `ZOE_SEAM_RECALL_INJECT=true`, `ZOE_ROUTER_SELFTRAIN` unset, `ZOE_PROACTIVE_SPOKEN=0`, `ZOE_SPEAKER_MODEL=dual`, `ZOE_FACE_ID_ENABLED=true`.
- Scheduled in APScheduler: 8 Multica autopilots, music discovery, music history, token sweep. Everything nightly runs as systemd user timers instead: `zoe-training` 02:00 (untracked), `zoe-backup` 02:30, `zoe-memory-export` 02:40, `zoe-night-window` 02:50, `zoe-voice-regression` 04:30, `zoe-backup-verify` Sunday 03:30.
- Containers: zoe-database (pgvector pg17, now loopback), homeassistant, homeassistant-mcp-bridge, zoe-omnigent (1.1 GiB RSS), zoe-auth, zoe-ui, zoe-cloudflared, zoe-ytmusic-potoken, zoe-multica-backend/web (dispatch paused), zoe-smb-drop. `livekit` exited by its idle reap; `zoe-music-assistant` exited by its idle reap.
- Data: chat_messages 1,741 rows (newest 10-15), people 32, MemPalace drawers 371 (323 live), one real panel (`zoe-touch-pi`, room "Bedroom") plus three test rows, one room, zero web-push subscriptions, 9 device tokens.
- MemAvailable 785 to 931 MiB; swap 7.9 GB used. The RAM floor is still the binding constraint on every line of every plan.

## 3. The core: what the request path looks like and where it breaks

### 3.1 The event loop (lane A headline 1)

`semantic_router.route()` is synchronous (`semantic_router.py:696`). With the head active it calls `router_two_stage.decide`, which does `urllib.request.urlopen(..., timeout=1.5)` to `:11436` (`router_two_stage.py:298-302`). It is called directly inside async handlers at `routers/voice_tts.py:3446` (every `/command`, `/turn`, `/turn_stream`), `fast_tiers.py:618` (chat and LiveKit), `stt_snap.py:477` and `main.py:2391`; only `skybridge_service.py:2050` wraps it in `to_thread`. zoe-data is one uvicorn worker. This is the same failure class as the documented accept-queue hangs. **Fix: S, replay-gated, plus an event-loop-lag gauge on `/metrics`.**

### 3.2 Writes that land in the wrong account (lane A headline 2)

When a direct intent handler returns `None`, `execute_intent` falls back to an `mcporter` subprocess (`intent_router.py:4755-4761`). `_build_command` omits `user_id` for `list_show`, `list_remove` and `calendar_create` (`intent_router.py:5609-5626`) and `mcp_server.py:42-45` then falls back to `family-admin` because `ZOE_MCP_STRICT_USER_ID` defaults false. User text is spliced into the command unescaped before `shlex.split`. **Fix: M, give every dispatchable intent a direct handler and delete the fallback (about 400 lines).**

### 3.3 Five voice lanes, one safety stack (lane A headline 3)

`/api/voice/command|turn|turn_stream` run `fast_tiers` plus guest policy plus the scope gate. `/ws/voice/` (`main.py:2991-3334`, used by both `voice.html` files) runs its own sequence and never calls `fast_tiers`, so it skips distress, provenance, feedback, correction, self-model and identity. LiveKit, Telegram and the HA conversation component are further lanes. The parity rig compares only `voice_command` against `chat_stream_generator`. The non-stream `chat()` re-implements the streaming generator block by block (about 390 duplicated lines). **Fix: M, retire or route `/ws/voice/` through `voice_command`; extend the parity rig; collapse `chat()` onto the generator.**

### 3.4 Three brain lanes, one served, drift between them (lane A headlines 4-5, lane C 2.1)

- `brain_dispatch.use_flue_brain()` is true only when `ZOE_BRAIN_BACKEND == 'flue'`; the code default is `'core'` (`brain_dispatch.py:164`). A lost `.env` line serves turns on the dormant core lane, which has none of `clean_goodbye`, `role_guess_guard`, `brain_slot_guard`, the recall floor, seam recall, speculation, replay isolation or manner (grep counts flue/core all N/0).
- The flue `soul.ts` claims to be a "verbatim copy" of `SOUL.md` and carries a RECALL paragraph that `SOUL.md` does not. No test pins them together.
- The legacy `zoe_agent.py` (4,569 lines) is still warmed into the KV cache on every boot (`JETSON_AGENT_MODE=true` in the unit, `main.py:1253-1258`) with prompts flue never sends, is imported as a library by nine live modules, and is reachable through failover and `pi_intent_lab.py`, which mutates `os.environ` during an await.
- The live brain is `labs/flue-zoe-brain-2x` (18.1k tracked lines, 24 tools in `zoe-tools.ts`, its own SQLite store, auto-deployed by `deploy.yml`). `labs/AGENTS.md` opens with "nothing here is executed by any production service". VISION still says `zoe-core` is the brain.
- **Decision for the owner: make `flue` the in-code default, pin it in `test_canonical_invariants`, promote the sidecar to `services/zoe-brain` (and the bot to `services/zoe-telegram`), retire legacy as a lane (move its library functions out), and either delete the core lane or give it a guard-and-persona parity test. L, every step replay-gated.**

### 3.5 Duplicated mechanics (lane A 1c)

Five tool-schema lists (mcp_server 75, zoe_agent 26+17, flue 22, core 11, the intent-dispatch allowlist 29) with no generator between them. At least six persona definitions. Four memory-recall injection paths. At least ten inconsistent guest-sentinel frozensets (one already caused a bug; `user_filters.is_unidentified` exists and 35 modules use it). Three "voice session" concepts; the split plan's reserved file name `voice_session.py` is already taken by a different one. Four env-bool parsing rules.

### 3.6 God files grew after their split plans (lane A headline 8)

`voice_tts.py` 4,812 to 7,370 lines since its plan (1 of 6 PRs done); `voice_command` alone is 2,216 lines with 8 intent-detection call sites, 23 reply returns and the speak-save-memory tail open-coded about 15 times. `intent_router.py` 4,414 to 6,193 (no plan). `main.py` 2,650 to 3,334. `chat.py` is the one success (4,035 to 3,437). `services/zoe-data/AGENTS.md` is 305 KB in 200 lines, with one 11,090-character line; a contract nobody can read defeats DOX.

### 3.7 Config and flags (lane A headline 9, lane C 1.3)

673 production `ZOE_*` flags; 624 read in exactly one file; 95 read only by scripts. 647 raw `os.getenv` reads against 164 `typed_env` calls. 18 flags with conflicting literal defaults between readers (`ZOE_BASE_URL` has three). 68 non-`ZOE_` names the inventory cannot see gate live paths (`HERMES_FAST_PATH`, `JETSON_AGENT_MODE`, `OPENCLAW_ALL_TOOLS_ENABLED` on the chat tool fast path). `ZOE_SEAM_RECALL_INJECT` is on live and absent from the inventory (read through a constant). The inventory's `.env.example` coverage metric reads the wrong file (`flag_inventory.py:201`: 2 of 673 instead of 48). **Fix: M for the inventory; L for a registry each mode flag declares itself in, with the inventory generated from it and flags that have been on or enforce for 30 days promoted to code.**

### 3.8 Data layer and auth (lane A 6-7)

30 of 52 routers run raw SQL; lists, calendar and reminders each have three or more writers (router, `_execute_*_direct`, `mcp_server`). 50 files still use `?` placeholders through the compat shim. Chroma runs in-process, so the documented HNSW segfault takes STT, chat and the brain seam down together. `vapid_keys.json` (a private key) lives inside the checkout. Auth: 26 mutating routes declare no `Depends` (17 in `routers/music.py:441-735`), `GET /api/settings` dumps every `system_preferences` row to a guest (`stubs.py:74-93`, including the household weather location), the Multica webhook updates proposal status before it checks the secret (`system.py:~1925` vs `~1944`), two endpoints fake success, and 114 mutating routes rely on `get_current_user`, which admits the guest, 35 of them with no role check in the body. **Fix: S for the auth rules plus a CI test that every mutating route declares an auth dependency from an allowlist; L for one domain service per table with generated tool schemas.**

### 3.9 Observability (lane A 8)

Every log line is written twice (JSON to stdout at `main.py:59`, plain text to a rotating file at `main.py:1119`). No tracing; the request id is not propagated to the sidecar or back through intent-dispatch; identity, replay, persona and speculation travel as text lines prepended to the user message. 2,037 broad `except Exception` and 397 `except: pass`. Prometheus metrics exist but nothing scrapes them. No event-loop-lag metric, which is the one signal that would have caught 3.1 and the accept-queue hangs.

### 3.10 The engineering harness inside the product process (lane A 7, lane C 3)

58 modules and 19,668 lines of PR-pipeline and Multica code live in `services/zoe-data`, including `greploop_guard.py` (2,390 lines) with admin routes in the product API, `pi_executor.py` doing `git push` and `gh pr create`, about 1,000 lines of Multica orchestration in `main.py`, and an untracked `zoe-merge-queue.timer` every 10 minutes. Seven modules (2,190 lines) are imported only by tests. Eight harness generations coexist (lane C 3): the 2026-04 training scripts (untracked, still running), OpenClaw (router mounted, trigger registered, probing a dead port each loop), Hermes (env loaded by three units; root `AGENTS.md` still calls it the default engineering agent), Multica (paused, but its autopilots schedule the household's morning check-in, evening wind-down and reminder scan), Omnigent (operating, 1.1 GiB), the Flue executor (designed, never installed), greploop (operating, inside the product), and the in-product self-improvement path (struggle detector, shadow), which is the one VISION actually wants. **Fix: M to move the harness out and the household autopilots onto zoe-data's own scheduler; S to delete generation 0; M to finish the OpenClaw and Hermes removal and fix the three contracts that contradict it.**

## 4. Memory, the brain side and the night

- The memory and companion code is about 37k lines (plus 5.6k in `proactive/`) serving one real member, 371 drawer rows and 32 people. Quality is high (the 10-08 bake-off: 237 of 237 authority checks, name from the account 39 of 39, Zoe led every shared axis). The cost is coherence: every new floor costs more to keep consistent than it buys.
- Write paths: the 10-05 audit counted 31; today there are at least 39 (19 non-test `ingest` callers plus eight new writer modules plus 12 raw upserts). The writer map should be generated from code by a test, not kept in a document.
- The Samantha bar, day-sim and person bench are hand-run only (CI ignores `tests/samantha_live`, no timer). Last full bar 2026-10-09: 18 PASS, 3 FAIL (S10, S21, S24), 2 SKIP. Every full run in the trend is `dirty: true`, so no score is attributable to a commit, the same evidence-binding defect the voice gate fixed with `--expect-revision`. About 100 PRs touching memory have merged since.
- Night window (`zoe-night-window.timer` 02:50): on 2026-10-18 it ran 3.2 minutes, stopped llama-server, Kokoro, the router and zoe-data, loaded the 12B, ran `night_mind` for 5.0 s with 0 prompt and 0 generated tokens, decode 1.86 tok/s, and reported `outcome='ok'`. `zoe-night-mind.py:387` sets `mode = enforce` unless `--dry-run`, and the window passes no `--mode`. `omi_day_review` is in the unit's `--jobs` and never appears in the run. **Fix: S, skip the 12B load when no member has new turns, report `noop`, pass `--mode` explicitly, alert when decode drops below half its trailing median, and correct PLANS and the gap register.**
- The router shadow corpus holds 10,858 decisions; the last self-train dry-run was 2026-07-26. Re-mining is S-sized and blocked only by a RAM window.
- Prompt budget: the 8k context carries doctrines, the user-model card, `[Today]`, the recall packet, history and the hop; every new block eats history and nothing reports the per-turn prompt size. Add a prompt-size column to the bar and day-sim.

## 5. Ops: the things that lose data or run by accident

| Item | State [verified] | Fix | Who |
|---|---|---|---|
| Off-box backup | none. `zoe-backup.timer` writes Postgres dumps and a MemPalace tarball to `~/.zoe-backups` on the same NVMe; no copy of `.env*`, models manifest, `~/.zoe`, the voice corpus or the router corpus. The weekly restore check passes against the same disk | restic to a rest-server on the411 (section 8.5), restore drill in the weekly verify, on the ledger | owner (target, key) + agent |
| Forget reach | the forget cascade covers the palace and Postgres; outside it: `router_head_shadow.jsonl` (`utt` field), `~/training/data/train.jsonl`, `~/.openclaw` sessions (1.2 GB), `~/.hermes` (6.4 GB), app logs, backups | a "where do household words live" inventory test; redact or stop logging raw text in the shadow log; delete the dead stores after an export decision | agent + owner OK |
| Generation-0 training job | `zoe-training.timer` 02:00 runs untracked `~/bin/nightly-training-cycle.sh`, which re-reads the dead OpenClaw sessions (newest 06-16) and appends exactly 55 "new turns" nightly (10,026 on 10-07 to 10,631 on 10-18); LoRA off; weekly self-review writes unread proposals | stop and delete; purge the copy | agent + owner OK |
| Untracked live units | `zoe-training`, `zoe-backup`, `zoe-health`, `zoe-merge-queue`, `zoe-self-review`; `zoe-data.service` carries env lines absent from the template; about 22 retired unit files and 13 `.bak` copies in `~/.config/systemd/user` | track or delete every live unit; a template-vs-live drift line in `zoe_ground_truth.sh` | agent |
| Watchdogs | five overlapping; `zoe-watchdog.sh` and `zoe-health-check.sh` probe dead Hermes `:8642` and OpenClaw `:18789` and do not probe the live brain `:3579` or Kokoro `:10201` | one tracked health unit on the canonical spine | agent |
| Secrets | 115 `.env*` copies across worktrees, 11 `.env.bak*` beside the live one, repo `.env` 664, `ssl/zoe.crt` 777, `vapid_keys.json` in the checkout, Telegram token leaked once to the journal and not rotated (ledger) | worktree reaping deletes `.env` copies; chmod; move the VAPID key; rotate the token | owner + agent |
| Containers | 0 of 11 memory caps; 5 without log caps; zoe-data bound on `0.0.0.0:8000` (bypasses nginx on the LAN); `livekit` image `:latest`, unpinned, no memory cap | compose caps; loopback bind; pin the image | owner Docker window + agent |
| Quiet hours | AGENTS.md says 01:50 to 04:55; inside it run training 02:00, backup 02:30, memory export 02:40, the window 02:50, voice regression 04:30, serena pregate 04:15 | one documented nightly schedule; the training job should not exist | agent |

## 6. Roadmap coherence, docs mass and pace

- **Fifteen plans of record** (lane C 6.1): PLANS, the beat-the-bar tracker (§0 "NEXT ACTION" dated 10-04), the evolution plan and packets, the memory build plan (08-10), the brain blueprint, the gap register, the best-ideas register, what-gets-us-closer, the tools programme, the fidelity audit, the September review, tech-debt, desktop overhaul (07-22, "waves not started"), the Flue/Multica trio, and the ledger plus two agent-memory programmes. Three of them re-rank the same Samantha space; two carry their own NEXT ACTION.
- **Eleven contradictions** between docs and live (lane C 6.2), the sharpest: PLANS says no feature work until the 700/800 MB gates clear, and feature work never stopped; AGENTS.md says Hermes is the default engineering agent; `labs/AGENTS.md` says nothing in labs runs in production; VISION says zoe-core is the brain; the night mind is "off"; ten flags are live while documented dark, five are shadow while documented done; the ledger rule says delete fixed lines and at least 21 resolved lines remain.
- **Pace:** 361 PRs merged between 2026-09-18 and 10-18 (12 a day; 105 in the last 7 days), median 723 changed lines, p75 1,434, 132 at or above 1,000, against the repo's "one or two in flight, 200 target, 400 ceiling, fail at 1,000". `validate` median 13.9 min; 11 of 38 runs cancelled by newer pushes; 86 cross-reviews in one week; Codex quota exhausted; polly auth broken after idle (#2102). The ledger gains about 30 lines on a build day (107 on 10-09) and drains slower than it grows: 385 lines, roughly 26 % owner-blocked, 68 % agent-doable, at least 21 already fixed. **Either the rule changes or the pace does; the measured 3.6 reviews per PR in July came from exactly this.**
- **Docs:** 422 files, about 116k lines; `docs/research/` has 63 records (58 since 09-25), no `index.md` and no `AGENTS.md`, against the OKF rule. Agents read them; the owner reads PLANS, VISION and the packs.
- **Proposal (lane C 6.6):** one `docs/ROADMAP.md` of about 150 lines with Now (at most 5), Next (at most 10) and Later; each item carries its flag and the flag's *live* value, its acceptance gate and its PRs; a generated `docs/knowledge/live-state.json` (flags, timers, last bar and replay with commit and dirty bit) that CI compares against roadmap claims; a 30-minute Monday cadence; programme docs become evidence records and lose their NEXT ACTION sections; research records get a `status` field, absorb-then-retire at 30 days, a cap of 25 open records enforced by `validate_structure.py`, and an `index.md`.

## 7. The field: Apple HomeView and what changed since 2026-09-25

### 7.1 Apple HomeView (lane D, all `[announced]`, ships 2026-11-13)

$299 tabletop or wall (mounts $60 to $130 extra), 7-inch square display, A18 with "Secure Exclaves" for mic and camera data, 12 MP Center Stage camera with no physical shutter, 4 beamforming mics, Wi-Fi 6E, Thread, Matter, no battery, no App Store, English only, not sold in the EU. Siri AI runs on device or on Private Cloud Compute with daily caps and a paid tier planned; its models are "custom-built in collaboration with Google and its Gemini models". Up to six people recognised by face; personal content shows only when the face is recognised, the member's iPhone is on the same Wi-Fi and was recently unlocked; it asks before switching profiles and locks when you walk away; guests get timers, music, weather and accessories. FaceTime video, Messages read and reply by voice, Intercom to a room or the whole house reaching HomePod, iPhone, iPad and Watch (audio only, no drop-in, no video), Find My, up to nine camera feeds, smoke and CO alarm sound detection, a cooking mode, AirPlay 2 multiroom, distance-aware UI. Nothing announced for kids, Live Translation, Personal Voice or accessibility.

**What Apple has that Zoe does not, ranked by value to a household that already has a voice-and-face panel:**

| # | Apple has | Buildable on Zoe | The piece |
|---|---|---|---|
| 1 | Phone presence as a second factor for private results | yes, cheap, pure policy on the existing face and voice ID | HA companion presence, Wi-Fi association or BLE (ESPresense); it is the identity-presence engine's "phone in range" signal |
| 2 | Whole-home and per-room Intercom reaching phones and watches, with spoken reply | yes | section 8: native PA and PTT on the LAN, Matrix for the phone leg |
| 3 | FaceTime-class video calls | yes | LiveKit is already in the stack; section 8 P2 |
| 4 | Distance-aware adaptive UI and presence-reactive screens | yes, the cheapest big UX win | face-box size (`FACE_MIN_PX` exists) or a $5 mmWave radar on ESPHome |
| 5 | Recognise-and-switch personal screen, auto-lock on walk-away, guest scope | yes, UI and session policy on existing ID | the identity-presence engine design already specifies it |
| 6 | Camera intelligence (person, face, pet, package, descriptions, clip search) | partly; GPU and RAM contention with the brain | Frigate on separate hardware, captions batched in the night window |
| 7 | Sound recognition (smoke, CO, glass, dog) alerting phones | yes, near zero cost, privacy-safe | YAMNet or PANNs on the panel mic; a strong family safety feature |
| 8 | Handoff by holding the phone to the display | partly; no NFC or UWB on the Pi | the QR and Telegram "send to panel / phone" flows that exist |
| 9 | Cooking mode (recipe to steps, hands-free timers) | yes | recipe-scrapers or Mealie plus structured extraction and the existing timers |
| 10 | Onscreen-aware assistant | yes | feed the panel state into the brain context |
| 11 | Find My-class people and item location | partly | OwnTracks or Traccar; BLE tags with ESPresense |
| 12 | Conversation continuity across devices | yes; memory is already household-central | expose thread continuity in Telegram and on the panel |
| 13 | Matter and Thread hub with Aliro locks | mostly via HA | HA Matter server plus an OpenThread border router |
| 14 | Secure-Exclave isolation as a trust claim | not in silicon; a stronger local claim is possible | a physical mic and camera cutoff, which Apple does not have |
| 15 | One-tap NFC accessory onboarding | no | Matter QR plus the phone-QR flow |

Zoe's edge: no usage caps or subscription, no region lockout, works for Android and Telegram users and guests without an account, memory lives in the box, and a hardware kill switch is possible.

### 7.2 What changed in the field since the September review (lane E, new or reinforced only)

1. **Short silence gate plus semantic end-of-turn.** Pipecat 1.0 (2026-04-14) cut its default VAD stop from 0.8 s to 0.2 s behind turn analysers; Smart Turn is still v3.2 (8 MB int8, BSD-2, 10 to 65 ms CPU). Stronger evidence for the speculative turn-start already ranked first in September. There is no Smart Turn v4, Moonshine v3 or Kokoro v2.
2. **False-interruption classifier with resume.** LiveKit Agents 1.5.0 classifies in 30 ms and resumes playback instead of regenerating, with dynamic endpointing; copy the policy, use Smart Turn or Silero as the classifier.
3. **Speaker verification at the wake gate.** OpenVoiceOS (2026-09-26) verifies the speaker from about 1 s around the wake word, before recording, with per-profile thresholds; it fails open until someone is enrolled, which Zoe must invert to fail-closed-to-guest. HA's ha-agenthub merged speaker ID (2026-10-12), prompt-context only, off by default.
4. **WakeForge custom wake words.** OVOS (2026-10-02): a 1.46 MB int8 model trained only on synthetic voices, 99 % recall at one false activation per hour on single words; "hey zoe" is multi-word and must be lab-proven against the voice corpus.
5. **Intercom in the HA world.** HA still has one-way `announce` only; Gemini for Home improved broadcast (09-16); the open piece is VoIP Stack (MIT, stable 2026.10.0: SIP/RTP, browser softphone, companion app, any SIP client). Kiosk Satellite's intercom is CC BY-NC-ND.
6. **Side-talk detection, 15-minute conversational memory, no re-verification inside a session** (Gemini for Home notes, Jul to Oct 2026), including a shipped bug where "stop" ended the conversation instead of halting media. This maps onto Zoe's short voice session design.
7. **A CPU-real-time TTS fallback** (Kyutai Pocket TTS, 100 M params, licence unverified) would remove the Edge-TTS cloud hop from the canonical waterfall. Not a Kokoro swap.
8. **Moonshine Voice toolkit** (2026-02-13): streaming partials and built-in speaker ID in the same family as the rock; check whether zoe-data uses the streaming variant (the IDEAS board says the live model is `MEDIUM_STREAMING`).
9. **Telegram Bot API 10.1 to 10.3**: streamed message drafts, voice-note blocks, ephemeral per-user messages. Streaming the brain's reply into a draft makes Telegram feel as fast as voice.
10. **Music Assistant 2.9**: per-player minimum and maximum volume (a night or kids cap that survives any Zoe bug); HA 2026.10 lets MA announcements speak through a TTS entity with chime and volume kept; MA still pauses rather than ducks on players without native announce.
11. **mem0's decay re-ranking and fusion** are worth the algorithm shape; do not copy its habit of weighting agent-confirmed facts like user-stated ones.
12. **Phones without an App Store**: Home Screen PWA with Web Push (Declarative Web Push on iOS 18.4+), ntfy and UnifiedPush on Android, Telegram as the fallback; iOS push cannot be self-hosted end to end.
13. **HA Cloud became Home Assistant Link** (2026-10-02) with nearest-relay-by-ping at boot, a trick for the the411 relay. `wyoming-satellite` is archived, replaced by `linux-voice-assistant`.
14. Commercial, confirmed: Amazon Alexa tablets with Google Play (10-08); OpenAI's device not before March 2027; Meta Muse Charm (December target); Sonos 27 with its own assistant and MCP; Samsung Ballie shelved. The whole field converges on per-person recognition with a guest default, restrained follow-ups, MCP as the openness layer, and a screenless home computer. Nobody ships local-only.

Looked for and not found, so nobody re-searches: an official HA speaker-ID or multi-wake-word release; an open-source kids-mode household assistant; any Neon or Letta news in October; Willow's status.

## 8. Household communications: calls, messages, PA, intercom, the411

### 8.1 What exists today (lane B, all verified)

| Primitive | State |
|---|---|
| Zoe to one panel, text spoken | `voice_announcements` queue, 5 s daemon poll, TTL 120 s, played-ACK; claim-any by default (`voice_announce.py:27-37`), so "all panels" is played once by whichever daemon polls first; text only through `/api/voice/speak`; no arbitrary clip, no chime, no music ducking; delivery OFF (`ZOE_PROACTIVE_SPOKEN=0`, owner decision) |
| Zoe to panel UI | push WebSocket (`PushBroadcaster`, channels `all` and `panel_<id>`), `ui_actions` with client ACK; `voice_tts.py` broadcasts to `all` 48 times and to one panel once; the kiosk filters with `panelMatches`, which acts when the target is empty or its own identity is unknown (`touch-ui-executor.js:280-286`). Correct with one panel; by reading, transcripts and cards would render on a second |
| The brain's reach | 22 tools in `zoe-tools.ts`; none is panel, announce, message or call. The rich `panel_*` MCP tools belong to the legacy agent; `panel_announce` only shows a toast (`mcp_server.py:2799-2811`) |
| Zoe to a phone | web push (VAPID, `pywebpush`): **0 subscriptions**; Telegram: chat with Zoe, text and voice notes live, photos dropped silently, `/talk` points at a LAN IP; zoe-data cannot send a Telegram message because `auth_handoff.py:382` reads `ZOE_TELEGRAM_BOT_TOKEN` and the process holds `TELEGRAM_BOT_TOKEN` |
| Member to member | nothing; the nearest is an admin-only scheduled nudge (`routers/proactive.py:48`) |
| Media server | LiveKit, on demand (`ZOE_LIVEKIT_ONDEMAND=true`, 300 s idle reap), one hard-coded room `zoe-voice`, a server agent that subscribes and sends base64 WAV over the data channel and never publishes audio, pure-Python aiortc transport (livekit-ffi cannot open a PeerConnection on Tegra), no TURN (`LIVEKIT_TURN_URL` unset), so LAN only; image `:latest`, no memory cap, about 560 MB while up against 0.8 GB free; the real live consumer is the estate Ask card "Stay talking to Zoe" (`home.html:4460-4662`). Latent party line: every token grants the same room with publish and subscribe, the agent broadcasts replies to all, desktop `voice.html:951` attaches any remote audio track |
| Panel audio and mic | PulseAudio to the Jabra Speak 750 (hardware echo canceller); the daemon holds one PyAudio stream and Chromium can open a second; the kiosk auto-grants mic and camera and autoplays; nothing suspends the wake word during a browser LiveKit session (PLANS lists it open); MA can play to the panel's AirPlay "Zoe Panel" and to Sonos, Chromecast and groups, but MA is idle-reaped and Zoe never calls its announcement API |
| Identity for a caller or callee | device token = the panel; PIN sessions; dual Resemblyzer + ResNet34 voice ID live at 0.70; voice sessions (#2025) off; face ID flag on, Pi side enrolment-only; contacts with phones and IM fields (0053); no reachability record, no phone presence, no call permissions by role |
| Remote access | Cloudflare tunnel (TLS ends at Cloudflare, no UDP; hosts `zoe.`, `ssh.`, `buildzoe.`), Headscale (PR #2083 **closed unmerged**, bundle on a branch; `panel-anywhere.md:73` is stale), frp relay (F0 lab merged #2094, R1 box side draft #2095, runbook never run on the411). None carries media |
| the411 today | a DigitalOcean droplet with host nginx and other sites; the relay target and the Headscale host; VAPID claims already name `zoe.the411.life`; `agent_sync.py:296-300` tells every agent Zoe is "component #16 of the411.life, the Living Map of Social Life" |

### 8.2 What was (the thing you remembered)

- **LiveKit** entered compose with the first commit (2025-11-17) beside `zoe-voice-agent`, `zoe-tts` and `zoe-whisper`, as the "Her-style remote voice" lane. It survived every cleanup because the Ask card uses it.
- **Matrix and Element**: `services/zoe-core/routers/matrix_integration.py` (548 lines, deleted in the 2026-03 cleanups) implemented `/api/matrix` with SQLite-backed household users (`@username:zoe-pi.local`), rooms, messages, a Zoe bot command handler, a WebSocket per user, QR onboarding and household setup; `matrix-mcp-server` in compose pointed at `matrix.org`. No homeserver of our own was ever stood up. Retire the shape, keep the idea.

### 8.3 Recommendation: a hybrid (lane F)

**Adopt Matrix for everything between people; build a small native path for the LAN; keep a bespoke plan as Plan B.**

- **LAN PA and intercom, native (P0).** Opus over the existing panel WebSocket: one-to-all and one-to-room PA (a pre-rendered TTS clip or a recorded message), push-to-talk intercom, chimes, ducking through the daemon's existing `pactl` duck, sender logged, guests denied. About 700 lines (plus about 150 for open duplex), zero Orin RAM, 5 to 10 MB on the Pi. HA `assist_satellite.announce` does not fit (the panel would have to become an HA satellite that owns the mic); MA announcements pause music rather than duck; Mumble is the best adoptable alternative and still needs a mapping layer for rooms, speakers and guests. The daemon must mute wake-word and STT while a stream is live. PA is member-initiated speech, a different class from Zoe speaking unprompted; the owner should rule on it explicitly.
- **Messages and the account layer, Matrix on the411 (P1).** Each household is a Matrix user plus a bot user on the411's homeserver; the box runs an outbound `/sync` E2EE bot (about 60 to 120 MB, the one new box cost, measure it); Zoe-to-member and Zoe-to-Zoe messages are ordinary DMs and rooms; "leave a message for Sam" is a delivery policy on top. Homeserver: **Tuwunel** (v1.9.4, 2026-10-10, the official conduwuit successor, 0.5 to 1 GiB) for messaging, **Synapse** (1 to 2 GB) if calls are wanted now, because Element Call needs MSC4140 delayed events and Tuwunel's PR #555 is unmerged. conduwuit is archived; Dendrite is maintenance-only. Do not run a homeserver per box (CGNAT, RAM). Element X is the phone client (Element Classic sunsets 2026-12-31); its push rides Element's gateway and APNs on iOS, UnifiedPush or ntfy on Android. Bridges (mautrix v26.09 for WhatsApp and Signal) are opt-in per member, later; Telegram is already native. Retire the old SQLite `/api/matrix` shape rather than extend it.
- **Calls panel to phone (P2).** Element Call (v0.28.0, the default calling in Element Web) with its own LiveKit SFU and `lk-jwt-service` on the411's second public IP; LiveKit's end-to-end encryption keeps the SFU blind to media (keys exchanged over Matrix); the homeserver still sees who called whom and when. The panel kiosk gets a Matrix identity and an Element Call widget; the phone is Element X. Ringing is client-defined (MSC4075 is still in review): Element X iOS uses PushKit and CallKit with 2026 regressions, Android a full-screen intent. **Usable, not yet boring: acceptance-test ringing for a week on the family's real phones with the app killed and the screen locked before promising it.** In-house panel-to-panel calls stay native P2P WebRTC on the LAN, because Element Call would hairpin through the411.
- **Household to household (P3).** Two bot users and a shared room, cross-signing confirmed by voice on both panels, federation to a friend's homeserver optional, group calls on the411's SFU.
- **Backups to the411 (P1).** restic 0.19.1 to a `rest-server` with `--private-repos` (one repo per household id) behind HTTPS on the411; repo password minted on the box, shown once as a QR "emergency kit" and escrowed in the household vault (#2100 design); `pg_dump -Fc | restic backup --stdin`, SQLite `.backup`, the palace SQLite only (HNSW rebuilds), `.env*`, the voice corpus optional, **never `models/`** (34 GB, re-downloadable). Append-only plus a per-household quota; the key never on the411. Reference UX is Nabu Casa's one-toggle encrypted backup; keep more than their single generation. Run in the quiet window, not beside the 12B.
- **Where things run.** TURN and any SFU on the411 (a TURN server behind CGNAT relays nothing; the box has no RAM for an SFU). Media never rides the frp relay (the session-master plugin refuses non-https proxies by design); TURN/TLS can share 443 through the existing `ssl_preread` stream front by SNI. Cross-household signalling is an outbound WebSocket from each box to a small hub on the411 under the same device registry, so zoe-data never faces the internet and the R5 public name stays optional.
- **Plan B (all native):** signed and sealed envelopes with a directory and mailbox on the411 (about 800 lines), Web Push, P2P WebRTC with coturn. Cheaper in RAM and end-to-end by construction; no native phone ring; every feature is Zoe's to maintain. Use it if the droplet is small or Element Call fails the ringing test.

**Budget:** Orin +60 to 120 MB (the bot only); the411 +0.7 to 1.3 GB with Tuwunel or +1.2 to 2.3 GB with Synapse, +0.1 to 0.3 GB LiveKit, +20 to 30 MB rest-server, the relay stack 40 MB measured. Zoe code about 2,100 to 2,400 lines for P0 to P3 without the app (Plan B about 3,800 to 4,500), with about twelve new operational surfaces on the411.

**Decision gates:** (1) the411's free RAM, which is not in the repo (4 GB or more: Synapse from day one; under 2 GB: Tuwunel for messages, Plan B for calls until MSC4140 lands); (2) the one-week ringing cell; (3) an E2EE bot lifecycle cell (verification, restoring the box from backup without forking the bot's Olm device).

### 8.4 The one Zoe app (owner idea, 2026-10-18: Omi's app plus Matrix and Element)

- The Omi app is MIT, Flutter, with a FastAPI-on-Firebase backend. It is forkable but not clean: the backend host was hard-coded until a debug-only override merged 2026-10-09 (release builds ignore it), a fork needs its own Firebase project, and Flutter ignores an iOS-installed household CA so a `.zoe.internal` endpoint needs a pinned certificate. The pieces worth taking are the BLE and Opus device SDKs and the pairing and recording UI, not the backend. The panel Pi already syncs the pendant (`routers/omi_recordings.py`); a phone BLE path is a second ingest that should reuse `POST /api/omi/recordings`, and background BLE on iOS is unreliable for bulk sync.
- Flutter and Matrix in 2026: the famedly `matrix` Dart SDK (AGPL-3.0, vodozemac E2EE) is what FluffyChat 2.10.0 (2026-10-04) uses to ship E2EE MatrixRTC calls with CallKit wiring, days old; `matrix-rust-sdk` is Apache-2.0 but has no Flutter binding and Element's native RTC library is AGPL or commercial; a webview Element Call gives calls without a native ring.
- **Verdict:** the cheapest credible "one Zoe app" is a Flutter or Capacitor app that is **not** a Matrix client: Omi-style pendant companion, day review, people tagging, household lists, QR pairing, deep-linking to Element X for chat and calls. If one binary is required, Flutter plus the famedly SDK on FluffyChat's call path is the only 2026 stack with E2EE, MatrixRTC and CallKit in one codebase (AGPL, days old). Either way an iOS ring needs a Zoe-owned PushKit and APNs setup, so a Zoe iOS binary means running Sygnal with Apple credentials. Size: 3,000 to 6,000 lines for a credible app. Sequence it after P2, not before.

### 8.5 Owner decisions this section needs

1. the411 droplet size and monthly transfer, and whether a second public IP and about 1 to 2 GB for a homeserver are acceptable there.
2. Matrix hybrid versus Plan B (after gates 1 and 2).
3. Intercom and PA as member-initiated speech: confirm it is outside the no-unprompted-speech rule.
4. Who may ring whom: kids, guests, drop-in versus ring, quiet hours (the identity-presence engine's `guests.intercom` and `others_present` settings).
5. Backups: restic to the411 versus an external disk first; who holds the emergency kit.
6. Whether a Zoe-owned iOS app (Apple Developer account, Sygnal) is wanted, or Element X is the phone app.

## 9. Systems that do not work together (consolidated)

1. Three box-to-panel delivery lanes (push WebSocket, announcement queue, Bluetooth long-poll) with different panel-identity semantics; `panel_announce` writes to the one that cannot speak.
2. Panel targeting by client-side filter (48 broadcasts to `all`), fine for one panel, a leak with two.
3. Four voice ingest transports each with their own endpointing; barge-in implemented twice (daemon `BARGE_DUCK`, LiveKit `ZOE_VOICE_BARGE_IN`); only the daemon lane is replay-gated.
4. Two `voice.html` files plus the Ask-card client; the touch one is orphaned, the desktop one is the `/talk` target.
5. Three remote-access paths, none merged end to end, none carrying media; the docs disagree about which is next.
6. Three notification designs (proactive engine, notifications bell, communication preferences) and one shared bug: `fire_notification` counts web-push subscriptions and calls them WebSocket subscribers.
7. The LiveKit lane's status misread in two ops records; removing it would break the Ask card.
8. The daemon's wake word and a browser LiveKit session on one mic, with no suspend handshake.
9. Three brain lanes with drifted guards and six persona definitions; the live brain under `labs/`.
10. Five tool-schema lists with no generator; three writers per table for lists, calendar and reminders.
11. The engineering harness, the household autopilots and the product sharing one process and one scheduler; the product's morning check-in depends on a paused engineering board.
12. Fifteen plans, one ledger, two agent-memory programmes, and a flag inventory that cannot see the flags that drive the live chat path.

## 10. The task list handed to the modernization session

Grouped and ranked. Size S = days, M = one to two weeks, L = a programme. "Replay" means the voice replay gate applies.

**A. Fix now (correctness, safety, data loss)**

| # | Task | Size | Owner | Risk |
|---|---|---|---|---|
| A1 | `asyncio.to_thread` around `semantic_router.route` at `voice_tts.py:3446`, `fast_tiers.py:618`, `stt_snap.py:477`, `main.py:2391`; add an event-loop-lag gauge to `/metrics` and a test that fails on a blocking call in the turn path | S | agent | replay |
| A2 | Router-level auth on `routers/music.py` (panel or member); `GET /api/settings` allowlist; Multica webhook secret check before the UPDATE; delete the two fake-success endpoints; CI test "every mutating route declares an auth dependency from an allowlist" | S | agent | low; verify the panel music bar still works with its device token |
| A3 | Give every `_DISPATCHABLE_INTENTS` intent a direct handler, delete the `mcporter` fallback, set `ZOE_MCP_STRICT_USER_ID=true` | M | agent | replay |
| A4 | Retire `/ws/voice/` and the orphaned `touch/voice.html`, or route the lane through `voice_command`; extend the parity rig to LiveKit and chat non-stream; collapse `chat()` onto the generator | M | agent | low for the panel |
| A5 | Off-box encrypted backups: restic to a rest-server on the411 (or an external disk first), covering Postgres dumps, the palace SQLite, `.env*`, `~/.zoe`, the voice and router corpora, with a restore drill in `zoe-backup-verify`; track the unit; put it on the ledger | M | owner (target, key) + agent | none |
| A6 | Forget reaches every store: inventory test of where household words live; redact or stop raw text in `router_head_shadow.jsonl`; delete `~/training/data`, `~/.openclaw` sessions and `~/.hermes` after an export decision; include backups in the cascade policy | M | agent + owner OK | low |
| A7 | Stop and delete `zoe-training.timer`, the weekly self-review and the `~/bin` training scripts; purge the duplicated copy | S | agent + owner OK | none |
| A8 | Night window honesty: SQL precheck and skip when no member has new turns, `outcome=noop`, explicit `--mode`, decode-drop alert, `omi_day_review` scheduled or removed; fix PLANS and the gap register | S | agent | none |
| A9 | Telegram: accept `TELEGRAM_BOT_TOKEN` as well as `ZOE_TELEGRAM_BOT_TOKEN` (or rename in the unit template); a loopback notify route on the bot; fix the `/talk` LAN URL | S | agent | none |
| A10 | LiveKit hygiene: pin the image, memory cap, per-session rooms (close the party line), rewrite the two ops records that call it dead | S | agent | low |
| A11 | Server-side panel targeting: every voice UI event carries `panel_id`; `panelMatches` stops acting on unknown; a two-panel test | S | agent | replay (voice_tts) |

**B. Structure (the monolith)**

| # | Task | Size | Owner |
|---|---|---|---|
| B1 | Brain lanes: `flue` as the in-code default pinned by `test_canonical_invariants`; promote `labs/flue-zoe-brain-2x` to `services/zoe-brain` and the bot to `services/zoe-telegram` with `deploy.yml` retargeted; a `soul.ts` versus `SOUL.md` parity test; retire legacy as a lane (library functions moved, boot warmup dropped); decide core (delete or parity-test); fix VISION, CANONICAL and `labs/AGENTS.md` | L | owner decision, agent build (replay each step) |
| B2 | Move the engineering harness out of zoe-data (`greploop_guard`, `pipeline_*`, `pi_executor`, the Multica poll loop and chain helpers, autoresearch, the engineering routes); track the merge-queue unit; delete the seven orphan modules; move the household autopilots (morning check-in, evening wind-down, reminder scan) onto zoe-data's own scheduler so Multica can stay paused or retire | M to L | agent |
| B3 | Finish the OpenClaw and Hermes removal: router, trigger, manager, health probes, the `~/.hermes/.env` loads in three units, the `OPENCLAW_*` and `HERMES_FAST_PATH` gates on the chat path renamed or deleted, the root `AGENTS.md` "Hermes-first" section rewritten | M | agent (DOX pass) |
| B4 | Resume the `voice_tts.py` split (plan PRs 2 to 5, leaves first), then extract `_finish_turn` and a stage list; rename plan PR 6; add a size ratchet test. Write and start an `intent_router.py` split (detect, per-domain executors behind a registry, one domain service per table) | L | agent, replay |
| B5 | Tool schemas generated from one registry (mcp, flue `zoe-tools.ts`, `_DISPATCHABLE_INTENTS`); unify the ten guest-sentinel sets on `user_filters.is_unidentified`; one env-bool rule | M | agent |
| B6 | Flags: fix the inventory (`.env.example` path, non-`ZOE_` and TypeScript reads, conflicting-default check); then a registry each mode flag declares itself in, inventory generated from it, CI fails on unregistered reads, flags on or enforce for 30 days promoted to code | M then L | agent |
| B7 | Observability: one logging stream; `turn_id` as a header to the sidecar and back through intent-dispatch; structured fields instead of the in-band `zoe-*` envelope in wire 3; per-tier counters in `fast_tiers.resolve`; Chroma behind a subprocess so a segfault cannot take the voice API down; zoe-data bound to loopback | M | agent |
| B8 | Memory evidence bound to commits: refuse dirty-tree runs for the trend, run the bar and day-sim weekly on a clean checkout after the night window, store the revision, add a prompt-size column; generate the writer map (at least 39) from code | S to M | agent |
| B9 | Ops hygiene: track every live unit, a template-versus-live drift line in `zoe_ground_truth.sh`, one health unit on the canonical spine, container memory and log caps, `.env` reaping and chmod, VAPID key out of the checkout, drop the mempalace pins and the host chromadb 0.6.3, re-mine the router corpus in a RAM window | S each | agent + owner Docker window |
| B10 | `services/zoe-data/AGENTS.md` from 305 KB to a readable contract, history into OKF records | S | agent |

**C. Roadmap and process**

| # | Task | Size | Owner |
|---|---|---|---|
| C1 | One `docs/ROADMAP.md` (Now 5, Next 10, Later), generated `live-state.json` checked by CI, a Monday 30-minute cadence; every other NEXT ACTION section deleted; `docs/research/index.md` and `status` fields, absorb-then-retire at 30 days, cap 25 open records in `validate_structure.py` | M | owner decision, agent build |
| C2 | Pace: either enforce the size and serialisation rule (2 to 4 coherent PRs a day, one cross-vendor pass, Greptile only for load-bearing paths) or rewrite the rule honestly | S | owner policy |
| C3 | Ledger triage: delete the 21-plus resolved lines, promote or delete anything older than 21 days without a roadmap item, mirror owner lines into one owner pack | S | agent |
| C4 | Fix the eleven doc-versus-live contradictions in one DOX pass (Hermes-first, labs isolation, zoe-core as the brain, night mind off, the ten flags, Headscale #2083 status) | S | agent |

**D. The communications programme (section 8), after A and the two decision gates**

| # | Task | Size | Owner |
|---|---|---|---|
| D0 | Owner decisions 8.5 (1) to (6); the411 recon (RAM, transfer, nginx) per `labs/relay/RUNBOOK-R1.md` section 1 | - | owner |
| D1 | P0 LAN PA and PTT intercom: Opus over the panel WebSocket, room and all targeting on the server, daemon play with ducking and wake-word mute, sender log, guest denial, a brain tool `announce` and `intercom` behind a flag; lab cells with negative controls (a guest cannot broadcast; a second panel hears only its room; the wake word stays muted during a stream) | M | agent, replay for the daemon |
| D2 | P1 messages and backups: homeserver on the411 (Tuwunel or Synapse per gate 1), one account per member plus a bot per household, the E2EE bot on the box (measure RAM), delivery policy and quiet hours, registration by QR, recovery key escrowed in the vault, restic to rest-server | M to L | agent + owner (the411) |
| D3 | P2 calls panel to phone: LiveKit SFU plus `lk-jwt-service` plus `.well-known` on the411, Element Call widget on the kiosk with a panel identity, "call Sam" intent with presence gating, daemon pause during a call, the ringing acceptance cell | M | agent + owner phones |
| D4 | P3 household to household: paired bot users, voice-confirmed cross-signing on both panels, group calls, bridges opt-in | M | agent |
| D5 | P2b the one Zoe app (section 8.4), after D3 | L | owner decision |
| D6 | Apple-parity pieces that need no new infrastructure: phone presence as a second factor, distance-aware UI from face-box size, auto-lock and guest scope on existing ID, sound recognition on the panel mic, cooking mode, onscreen-aware context, MA per-player volume caps | S to M each | agent |

## 11. Corrections to existing records (so the wrong claims stop propagating)

- `docs/knowledge/open-problems.md` 10-04 and `log-review-docker-2026-10-04.md:131`: the `livekit` container is reaped on purpose (`OOMKilled=false`, on-demand lane), not dead or hand-stopped; removing it breaks the Ask card.
- `docs/architecture/panel-anywhere.md:73`: Headscale PR #2083 is closed unmerged; the bundle lives on `feature/headscale-bundle`.
- `docs/PLANS.md` and `samantha-gap-register-2026-10-09.md` section 1: the night mind runs nightly in enforce through the window CLI; it is not off.
- `docs/PLANS.md`: `ZOE_COMPOSE_UI`, face ID, the enrol offer, Omi sync and the dual speaker model are live, not flag-dark; restraint, structural claims, the recall gate, the struggle detector and the delivery ledger are shadow, not done. Ledger lines calling quote-retire and the three person-half guards shadow are stale: they are enforce.
- `docs/research/memory-fidelity-audit-2026-10-05.md`: 31 writers is stale; at least 39.
- `AGENTS.md` (root): "Hermes is Zoe's default engineering and browser agent" contradicts the 2026-07-22 retirement decision and the live state.
- `labs/AGENTS.md`: the live brain and the live Telegram bot run from `labs/`.
- `docs/VISION.md`: "Pi (`zoe-core`) is the brain" describes the dormant lane.
- The ledger's 10-09 lines on the 12B window not installed and the dreaming timer enabled are stale: the window runs nightly, the dreaming timer is inactive.

## 12. Not verified, and the limits of this review

- the411's droplet size, CPU and transfer allowance (not in the repo); every the411 RAM figure above is relative to an unknown total.
- Idle RSS of LiveKit server and coturn on ARM or a small VM; the 560 MB figure is a repo estimate.
- Pi 5 video-encode CPU in kiosk Chromium; whether the Jabra's echo cancellation is hardware; iOS 26 PWA call behaviour; Element X ringing against a self-hosted LiveKit stack (no 2026 source); Tuwunel PR #555 and `lk-jwt-service` issue #240 status; Omi's BLE sync internals.
- Apple's Intercom support page and two hands-on articles could not be fetched; Intercom semantics come from 2020 coverage plus the 2026 announcement; HomeView voice-ID is described only as processed in the Secure Exclave, so do not assume it works at launch.
- The lane A auth scan is an AST pass over route decorators; "no `Depends`" is exact, "admits the guest" is by reading the handler, not by request.
- Nothing in this review was executed against the live brain, the Pi or a test suite; the box had under 1 GB free throughout.
