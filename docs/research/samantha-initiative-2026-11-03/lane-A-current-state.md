---
type: research
title: Samantha Initiative lane A - current state of Zoe, verified against the code and the box (2026-11-03)
date: 2026-11-03
status: lane report
description: Phase 1 current-state assessment. Every statement is tagged with how it was checked. Code head is origin/main b51c94713 (2026-11-03 evening); live reads were taken 2026-11-03 20:00-20:30 local from the running box with read-only tools only. No new architecture is proposed here; other lanes do that.
evidence: scripts/maintenance/zoe_ground_truth.sh output 2026-11-03; read-only /health and /props probes; /proc of the running zoe-data and llama-server processes (flag NAMES and mode values only); ~/.zoe-logs (marker counts only, no utterance text); systemd user journal (start counts); the code at b51c94713; the records listed in section 7.
---

# Lane A - current state (2026-11-03)

Tags used on every claim:
**[code]** verified in the code at this head, with path:line ·
**[live]** verified on the running box today ·
**[record]** taken from an earlier record, not re-verified today ·
**[contradicted]** an earlier record or doc says otherwise and is now wrong.

Scope note. Four parallel read-only code sweeps (brain path, memory, voice, operations) fed this report alongside direct
live reads. Section 7 lists exactly what was not re-checked.

## 0. What Zoe is today, in ten sentences

1. Zoe is a household companion that runs on a Jetson Orin (16 GB unified memory) with a Raspberry Pi wall panel as her main face and voice, plus Telegram and a web/touch UI [live].
2. Every turn enters one Python FastAPI process, zoe-data on :8000, which runs STT (Moonshine, in-process), a fast-tier and two-stage tool router, memory, identity, scheduling and about 443 HTTP routes in 60 router modules [code: `services/zoe-data/routers/*.py`, route decorators counted].
3. Conversation that is not a direct command goes to the Flue 2.x "Pi agent" sidecar on :3579 (`labs/flue-zoe-brain-2x`), which talks to the Gemma 4 E4B QAT + MTP rock on host-native llama-server :11434 with a single 8192-token slot [live: `ps` args `--parallel 1 --ctx-size 8192`; ground truth `ZOE_BRAIN_BACKEND=flue`].
4. Speech out is Kokoro on :10201, the only voice, with no fallback; when Kokoro is down the panel is silent [code/doc: `docs/CANONICAL.md` TTS row; live: Kokoro was stopped by a probe at 20:17 and back at 20:19 today].
5. Memory is layered: Postgres (people, relationships, chat history, consolidation state), a Chroma-backed MemPalace for raw-first recall, and a nightly 12B "night window" that digests, dreams and reviews pendant days [live: night-window log; ground truth key tables].
6. The memory core is strong on authority and forgetting, but at the conversation surface it is mostly silent: in the last 4.5 days the brain called `recall_memory` on 0 of 51 turns and continuity injection matched 0 of 91 times [live: `FLUE_TOOLS`, `SEAM_CONTINUITY` markers].
7. Identity is live: ResNet34 speaker model, face ID, voice sessions and enrolment offers are all on in the running process [live: flag modes].
8. Self-healing exists for restarts (crash-loop watch, health timer, prefix keeper, slot guard, the daemon's "one moment"); self-improvement does not run: the nightly self-evolution loop is design-only and the struggle detector is shadow [code + live].
9. The box is shared with the engineering fleet: development agents, Serena, Omnigent, a GitHub runner and Multica run on the same 16 GB, and Kokoro restarts track development days (24-51 a day) not household use (2 a day on quiet days) [live: journal].
10. Zoe is capable, carefully instrumented and safety-floored, but under-used by the household and over-used by her own harness; what she knows rarely reaches what she says.

## 1. The 18 Phase 1 answers

### 1.1 What Zoe is (product)
- A local-first household companion, North Star "Samantha from *Her*": warm, always there, knows you, private, real-time, self-improving [doc: `docs/VISION.md:6-14`].
- Surfaces: Pi wall panel (voice first, touch second), web/touch UI (`services/zoe-ui`), Telegram bot (`labs/flue-zoe-telegram-2x`, unit `flue-zoe-telegram.service`), Omi pendant ingest [live: units active; `OMI_STORED` 261 and `OMI_TRANSCRIBED` 98 log lines since 11-01].
- Unprompted speech is off by owner rule (`ZOE_PROACTIVE_SPOKEN=0`) [live] [doc: `docs/VISION.md` principle 10].

### 1.2 Architecture
- **Hub-and-spoke around one process.** zoe-data is a single uvicorn worker [live: one `uvicorn main:app` process, RSS 1.49 GB]. 332 top-level modules plus 60 routers, about 196k lines of Python [code: `wc -l services/zoe-data/*.py routers/*.py`]. Largest files: `routers/voice_tts.py` 7,635, `intent_router.py` 6,108, `zoe_agent.py` 4,560, `memory_service.py` 4,362, `routers/chat.py` 3,368, `main.py` 3,102, `zoe_flue_client.py` 3,076 [code].
- **Event-loop sensitivity is real today.** `EVENT_LOOP_LAG` fired 34 times since 11-01, up to 0.74 s, each meaning "every request, push socket and voice stream waited" [live] [code: `services/zoe-data/event_loop_lag.py:16`].
- **Sidecars:** Flue brain :3579 (Node, ~136 MB RSS), Flue Telegram (Node), Kokoro :10201 (Python 3.10 venv, 2.15-2.26 GB RSS), FunctionGemma router :11436 (llama.cpp, 0.65 GB RSS), Gemma brain :11434 (5.87 GB RSS) [live: `ps`].
- **Containers:** zoe-database (Postgres), zoe-auth, zoe-ui, homeassistant + homeassistant-mcp-bridge, zoe-multica-backend/web, zoe-omnigent, zoe-cloudflared, zoe-ytmusic-potoken, zoe-smb-drop, and a scratch harness container `zoe-harness-scratch-samantha-bar-*` up 9 days [live: `docker ps`]. LiveKit exited 2 weeks ago; Music Assistant (`zoe-music-assistant`) exited 4 weeks ago and is on-demand (`services/zoe-data/ma_ondemand.py:57`) with an idle reaper (`zoe-ma-idle-reap.timer`, log says "container already stopped" every 10 min) [live].
- **Doc drift:** `docs/CANONICAL.md` says the brain targets llama.cpp b11194; the running binary is `~/llama.cpp-b11982` [contradicted: CANONICAL llama-server bullet vs live `ps`]. The owner switch the 11-02 record listed as "open" has happened. The 12B night runner still uses the b11194 binary [live: night-window log 02:51:36].

### 1.3 Capabilities: fully working, partial, planned-only
**Fully working and live** [live unless noted]
- Voice conversation at the panel via Flue brain with streaming (`ZOE_FLUE_STREAM_ENABLED=1`).
- Two-stage router in front of the brain (`ZOE_ROUTER_HEAD=active`); expert mode active with writes allowed (`ZOE_EXPERT_MODE=active`, `ZOE_EXPERT_ALLOW_WRITES=1`).
- Home Assistant voice control (`ZOE_HA_VOICE_ENABLED=true`; HA container up 5 days).
- Speaker ID (ResNet34), face ID, voice sessions, enrolment offers (adults) all on.
- Night window: 5 of the last 8 scheduled runs `ok`, two aborted on the memory floor (10-29, 10-30), one failed to load the 12B (11-02); 11-03 loaded on the second attempt after #2244's compaction retry [live: `NIGHT_WINDOW_SUMMARY`].
- Alarms: `ZOE_ALARMS=on` in the running process [live] while code and registry default it off (`services/zoe-data/flag_registry.py:99`) and the 11-02 record called alarms "P1 dark" [contradicted: completeness-and-issues 2026-11-02 section 3].
- Change notices, music discovery, relationship graph, hybrid retrieval, idle consolidation, person dossier/merge/suggest, emotional recall and follow-up, contacts conversational: all on [live].

**Partial (built, running in shadow or not reaching the user)**
- Recall gate `ZOE_RECALL_GATE`: shadow by default (`services/zoe-data/recall_gate.py:1,58,64`); live lines show `served=0 ... mode=shadow` [live].
- Seam recall floor `ZOE_SEAM_RECALL_INJECT=true` live, but it is shape-gated: it fires only when the message matches a recall-question shape (`services/zoe-data/zoe_flue_client.py:734-786`, `_recall_question_shape` / `_recall_floor_shape`) [code]. The 11-02 claim "both injectors are trigger-gated" still holds at this head [record confirmed].
- Continuity injection: default ON (`zoe_flue_client.py:1292-1314,1417`) and live, yet `matched=False bullets=0` on 91 of 91 logged turns [live].
- Night mind: the window writes in `enforce`; zoe-data's readers are off because `ZOE_NIGHT_MIND` is unset in the running process [live]; `serve` mode exists in code (`services/zoe-data/night_mind.py:198-212`) but the registry's allowed list omits it (`flag_registry.py:528`) [code, minor drift]. On 11-03 the night mind job was `idle` ("2 members, all skipped as a quiet day / opted out / mode off") [live].
- Omi day review and Omi speaker tagging: `shadow` [live].
- Struggle detector: shadow; 5 `STRUGGLE` lines since 11-01 [live].
- Barge-in: `ZOE_VOICE_BARGE_IN=1` live; the duck-decide-resume path was described as never run at the panel [record, not re-verified].
- `set_timer` brain tool: the 11-02 record said it fails closed. That is now **[contradicted]**. `timer_create` registers a real Skybridge timer and returns "Your ... timer is set for ..." (`services/zoe-data/intent_router.py:4686-4705`, `skybridge_service.py:423-439,585-594`), which does not match the tool's canned-line regex, so the tool confirms real timers. Only its comment block (`zoe-tools.ts:512-524`) is stale [code].
- Off-box backup: not configured; the weekly restore drill refused to run on 11-01 because MemAvailable was below its floor, and `zoe-backup-verify.service` is in failed state [live].

**Planned or design-only**
- Nightly self-evolution loop (`docs/architecture/self-evolution-nightly-loop.md` header: "design only; nothing built") [code/doc].
- Person-centred memory and contacts redesign: design accepted 2026-11-03, not built (`docs/architecture/person-centred-memory.md` header) [doc].
- Router self-train: `ZOE_ROUTER_SELFTRAIN` unset [live]; dormant since July [record].
- Panel remote access / relay, LAN intercom, iPhone app, TV panel: design or flag-dark [record].

### 1.4 How the services communicate
- Panel daemon (`scripts/setup/zoe_voice_daemon.py`, 5,250 lines) streams audio to zoe-data over HTTP/WebSocket; the daemon build on the panel matches the checkout [live: ground truth "build ... == checkout"]; push channel has 1 subscriber [live].
- zoe-data → Flue brain :3579 with token auth (wire 2); Flue → llama-server :11434 OpenAI-compatible; Flue tools call back into zoe-data via `POST /api/system/intent-dispatch` [doc: `docs/CANONICAL.md` brain dispatch; code: tool header `zoe-tools.ts:22`].
- zoe-data → FunctionGemma :11436 (stage 2 GBNF decode) after the in-process SetFit/numpy stage 1 (`services/zoe-data/models/router_head_mlp.npz`) [doc: CANONICAL rocks block; live: router process args].
- zoe-data → Kokoro :10201 over HTTP; Moonshine runs in-process (`warm_moonshine`) [doc: CANONICAL].
- zoe-data → Postgres (asyncpg) in `zoe-database`; Chroma is embedded in-process (no container) [live: no chroma container].
- zoe-data → Home Assistant via the HA container and `homeassistant-mcp-bridge`; → Music Assistant via `music_service.py`, starting the container on demand [code: `ma_ondemand.py`].
- Telegram sidecar → zoe-data with the internal token [code: flag inventory readers `labs/flue-zoe-telegram-2x/src/brain.ts`].
- Omnigent container hosts cross-review workers; it is development tooling, not on the household path [live: container up 42 min].
- Multica: own containers + DB (258 issue rows), dispatch paused by kill switch, but 8 Multica autopilot jobs are registered in zoe-data's own scheduler and `ZOE_MULTICA=true` with kanban budgets is set in the household process [live]. The engineering harness still lives inside zoe-data [contradicts the cut-list intent of the 10-29 synthesis section 4, still open].

### 1.5 Model selection, loading, orchestration
- Brain lane is configuration precedence, not failover: flue > core > legacy, resolved per turn; `ZOE_BRAIN_FAILOVER` default off, so with :3579 down every brain turn gets the client's canned sentinel (`services/zoe-data/brain_dispatch.py:4,24,39`) [code]. Live lane: flue [live].
- Three lanes still in code: flue (live), core (`services/zoe-core` via `zoe_core_client.py`, 1,391 lines, dormant), legacy (`zoe_agent.py`, 4,560 lines) [code]. The **code default is still `core`** (`brain_dispatch.py:155-164`), so the live lane depends on env [code]. B1 is still open [record + code].
- The legacy and core lanes still leak into the live flue path. `routers/chat.py:346-349` imports `zoe_agent` helpers at module load. `main.py:1295-1302` unconditionally schedules the legacy `zoe_agent.warmup_kv_cache()` at boot, which fills the one slot with prompts Flue never sends; the prefix keeper repairs this about 38 s later. The LiveKit `_prewarm_brain` calls `zoe_core_client.prewarm` with no lane check, spawning an unused Pi worker (`voice_livekit.py:620-630`). The people/memory expert kind calls `zoe_core_client` directly whatever the backend (`expert_dispatch.py:762,794-798`), and it is reachable because writes are on live [code + live flags].
- Router: stage 1 SetFit MLP numpy export, stage 2 FunctionGemma-270M Q8_0 `functiongemma-270m-zoe-functok-r2` on CPU (`--n-gpu-layers 0`, ctx 1024) [live].
- Brain: Gemma 4 E4B QAT Q4_K_XL + MTP drafter (`--spec-draft-n-max 4 --spec-draft-p-min 0.6`), flash-attn, q8_0 KV, `--cache-ram 1024`, `--swa-full`, mmap+mlock, reasoning off, 1 slot [live].
- Night: the window stops the 4B, Kokoro, router and zoe-data, loads Gemma 12B QAT with unified memory and 34 GPU layers (decode 6.47 tok/s measured 11-03), runs digest → dreaming → night mind → Omi day review, then restores in order llama → Kokoro → router → zoe-data (about 9 minutes offline) [live: night-window log].
- Brain slot discipline: `brain_slot_guard.py` keeps the voice conversation resident and defers background brain calls (default on; `services/zoe-data/brain_slot_guard.py:1,27,45`); live waits up to 240 s for background jobs [live: `BRAIN_SLOT_GUARD waited_ms=240296`]. PR #2257 adds household-preempts-background [code head; not separately traced].
- Prefix keeper (#2245) re-warms the household prefix after displacement: 27 `PREFIX_KEEPER outcome=warmed` since 11-01 (`services/zoe-data/brain_prefix_keeper.py:1`) [live].

### 1.6 Memory: storage, retrieval, maintenance
- Stores: Postgres tables (`people` 32 rows, newest 10-04; `memory_consolidation_state` 438 rows; `chat_messages` 2,240 rows) [live: ground truth]; Chroma MemPalace drawers (raw-first) [doc: CANONICAL]; Flue brain's own transcript DB (`ZOE_BRAIN_DB`, `labs/flue-zoe-brain-2x/src/db.ts`) [code: flag inventory].
- Authority (user-stated > confirmed > inferred), forgotten-means-forever and quote retirement are live (`ZOE_QUOTE_RETIRE=enforce`, `QUOTE_RETIRE` 43 lines; `ZOE_HOLD_THE_FACT=enforce`) [live]. 237/237 authority holds [record].
- Retrieval reaches the prompt three ways: the shape-gated recall floor, continuity injection, and the brain's own `recall_memory` tool. Measured since 10-30: brain tool calls on 1 of 51 turns (a weather call); `recall_memory` on 0 of 51; continuity matched 0 of 91; recall gate served 0 [live]. The 10-29 finding "83% of real replies used zero memory sources" is, if anything, optimistic now [record superseded by live].
- History on the flue lane: zoe-data sends only the current message plus context blocks. The sidecar keeps durable session history and windows it to 8,192 minus a 1,536 reply reserve, dropping whole user turns oldest first and keeping at least 3 (`labs/flue-zoe-brain-2x/src/context-window.ts:1-125`). Voice sessions rotate after 5 minutes of silence (`routers/voice_tts.py:227,270-290`), so most panel turns start with little history [code].
- Context budget per Flue turn (110 turns): system prompt average 2,836 tokens, tools 752, history 164 [live: `FLUE_CONTEXT_BUDGET`]. History replay is a small fraction of the prompt; most of every turn is persona and tool schema.
- Maintenance: idle consolidation (9 runs since 11-01), index compaction nightly, dreaming nightly on the 12B, night mind writes nightly when there is input [live].
- **There is no pgvector.** No `vector(` type or `CREATE EXTENSION` in zoe-data or alembic; the only Postgres text index is a `tsvector` GIN on `ambient_memory` (`services/zoe-data/alembic/versions/0002_fts_ambient_memory.py:20-27`) [code]. The phrase "Postgres/pgvector" in the brief is [contradicted]. Durable memory vectors live in Chroma (`mempalace_drawers`, cosine HNSW, `memory_service.py:119,209-235`), embedded with Chroma's ONNX all-MiniLM-L6-v2 384-d (`memory_service.py:182-206`); fastembed bge-small serves only the semantic router and persona drift (`semantic_router.py:127,432`) [code]. The `mempalace` library is no longer imported at runtime (`memory_service.py:212`) [code].
- **Write funnel.** `MemoryService.ingest` (`memory_service.py:1418`) applies guest drop, off-record, PII scrub, 300 s tombstone, durable forgotten ledger, idempotency, authority resolution (protected conflicts become `disputed`), provenance and audit [code]. Authority ranks operator 6 > user_confirmed 5 > user_stated 4 > ... > model_from_transcript 0 (`memory_authority.py:93-123`) [code]. The generated writer map counts **73 write sites in 28 modules**, two of which bypass the funnel (`memory_digest.py`, `zoe_agent.py`) (`docs/knowledge/memory-writer-map.md:14,28-30`) [code]; the 10-18 count of 39 is superseded [contradicted].
- **Every-turn memory is narrow.** Only the identity line and the user-model card ride every turn (`zoe_flue_client.py:2727`; `labs/flue-zoe-brain-2x/src/user-model.ts:1-12`); the for-prompt packet's semantic search runs only when `memory_gate.message_needs_memory` matches a keyword list (`routers/memories.py:1006-1035`, `memory_gate.py:161-170`); continuity fires only on a first-person mood statement and only when no recall block was injected (`zoe_flue_client.py:2675-2680`) [code]. That explains the live 0/91.
- Person graph: built, but per the 11-02 design measurement "starved of data" (0 birthdays/dates/gifts, 66% of approved memories unlinked to a person) [record, from `person-centred-memory.md`; not re-measured].

### 1.7 STT and TTS
- STT: Moonshine v2 Medium in-process, streaming under speech on (`ZOE_STT_STREAM_UNDER_SPEECH=1`), smart-turn on [live]. Empty transcripts dominate the log: 637 `STT_EMPTY` vs 51 `STT_CAPTURE` since 11-01, with large bursts in probe hours (75 at 04:00, 60 at 05:00 on 11-02), so most empties are harness or false wakes; the household share cannot be separated from the log alone [live] (`routers/voice_tts.py:2973,2996`).
- Nightly voice gate failed on STT speed on 11-02 (856 ms vs 488 ms baseline, 1.75x); the trend file flips pass/fail/error across the last eight runs [live]. `zoe-voice-regression.service` is in failed state [live].
- TTS: Kokoro on CUDA, phrase cache of 400 hot phrases reloaded on start [live: journal]. Starts per day since 10-27: 40, 40, 51, 16, 2, 2, 34, 24 [live]. #2233 pins that deploys never restart Kokoro; the remaining restarts are probe-driven [code + live inference]. 59 CUDA/memory-related journal lines since 10-27 [live].
- No STT fallback in the live path; a Moonshine error is raised, not hidden (`routers/voice_tts.py:3075-3093`) [code]. Streaming STT under speech is default off in code (`flag_registry.py:811-815`) but on live [code + live].
- When Kokoro is down, a streamed panel turn plays only the follow-up beep; a blocking-turn failure speaks an espeak "can't reach my brain" line on the Pi (`scripts/setup/zoe_voice_daemon.py:3757-3778,3937-3939`) [code]. The daemon's "One moment" retry covers only 502/connection-refused, once, after 4 s (`zoe_voice_daemon.py:3785-3810`) [code].
- Barge-in is a hard stop; duck-decide-resume is built but `BARGE_DUCK_ENABLED` defaults false (`zoe_voice_daemon.py:498`) [code].
- Possible identity mismatch: the server accepts only ResNet34 claims (`services/zoe-data/speaker_models.py:79-86`), while the Pi daemon still defaults an empty `ZOE_SPEAKER_MODEL` to Resemblyzer and loads its encoder (`zoe_voice_daemon.py:804-836`); unless the panel's own env sets `resnet34`, daemon-side claims are rejected [code; panel env not read].
- LiveKit: the container exited two weeks ago, but the LiveKit agent task still starts on zoe-data boot (`main.py:2059-2065`) and its 1,759-line router is mounted [code + live].
- Leftover config: 11 `ZOE_WHISPER_*` and 2 `ZOE_WYOMING_PIPER_*` variables are still set in the live process though Whisper-primary and Piper are retired [live; doc: CANONICAL Retired list].

### 1.8 Conversational context
- Sessions are per panel (`voice-panel-<panel>-<id>`), per ask (`ask_<id>`), per Telegram chat [live: session prefixes].
- Prompt cache: `FLUE_PROMPT_CACHE` shows warm panel turns reusing about 4,195 cached tokens with 214 fresh; ask-card turns start cold (2,668 fresh, 0 cached) [live]. `PANEL_KV_MISS` fired 35 times across about 110 turns since 10-30 [live].
- Elision of stale blocks and progressive tool disclosure are in Flue (`ZOE_BRAIN_ELIDE_STALE_BLOCKS`, `ZOE_BRAIN_PROGRESSIVE_TOOLS` default true) [code: flag inventory readers `context-blocks.ts`, `tool-groups.ts`].

### 1.8b What the user hears on failure
- Flue sidecar unreachable, timed out (180 s) or empty: "Sorry, I had trouble reaching my brain just now..." on every turn, because failover is off (`zoe_flue_client.py:171`; `brain_dispatch.py:19-22`) [code]. Voice command timeout (20 s): a generic "Sorry, something went wrong" (`voice_tts.py:4536,5706-5709`) [code]. Router sidecar failure (1.5 s) falls back to the brain silently (`router_two_stage.py:24-25,390`) [code].
- Prefix cache: neither the sidecar nor zoe-data pins `id_slot` or sets `cache_prompt`; warmth relies on a byte-stable prefix plus `--cache-ram 1024` (`zoe_flue_client.py:2130-2133`) [code]. Household preemption (#2257) cancels and re-queues background helpers when a Flue turn starts, up to 10 times (`brain_slot_guard.py:110-148,234-276`); every non-keeper Flue turn counts as household, with no traffic-class filter [code].

### 1.9 External tools
- Brain tool surface: 22 always-registered tools plus `web_search` when enabled (`labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts:1266-1293`, 1,293 lines), with progressive disclosure keeping only get_time, recall_memory and activate_abilities in the core set (`tool-groups.ts:124-147`); every tool intent is in the dispatch allowlist (`routers/system.py:2504-2520`), so no tool-name drift at this head (the 11-02 "drifts from the handler map" is [contradicted] for names). Tools are all routed through intent-dispatch; writes gated by `ZOE_BRAIN_ALLOW_WRITES` (default false) [code]. Web search tool `ZOE_WEB_SEARCH_TOOL` default off [code].
- In practice the brain uses almost none of them: 1 tool call in 51 turns [live]. Commands are handled before the brain by fast tiers and the router, which is by design, but it means the brain's tool loop is close to unused.
- HA, MA, calendar, lists, reminders, timers/alarms are reached via intent handlers and Skybridge [code: `zoe-tools.ts` header; `skybridge_service.py`].
- zoe-data's MCP server advertises **74 tools** (calendar, lists, reminders, people, notes, journal, transactions, memory, panel control, CloakBrowser fetch/screenshot, web search/browse, Multica board, evolution proposals; `services/zoe-data/mcp_server.py:130-1180`), and refuses a call that names no user (`:45-52`) [code]. The legacy agent passes only 10 of them through (`zoe_agent.py:3071-3091`) [code].
- HA is reached through the MCP bridge with a per-call home-control refusal check (`smart_home_service.py:45-84`); MA through `music_service.py:112-130` with on-demand start; web search tries a cloud provider, then ddgs, then CloakBrowser (`web_search_provider.py:1-50`) [code].
- Omnigent is reachable at runtime only through `omnigent_issue_executor.py`, default off (`:13-14,104-105`) [code].

### 1.10 Background tasks
- APScheduler jobs registered live: 8 Multica autopilots, `music_discovery_weekly`, `music_history_observe` [live: ground truth]. `add_job` call sites: 6 in `main.py`, 1 in `multica_autopilot_sync.py` [code]. Many other loops run as asyncio tasks (proactive ledger sweep 437 log lines since 11-01, Omi transcription, idle consolidation) [live].
- systemd user timers live: night-window, health, crash-loop-watch, backup, backup-verify (failed), memory-export, voice-regression (failed), log-rotate, merge-queue, ma-idle-reap, serena-reaper, serena-pregate-restart, opensrc-refresh, multica-heartbeat, telegram watchdog [live]. Unit drift: 43 installed vs 39 tracked + 4 retired-pending, 11 templates drift [live: ground truth]. The router retrain timer is not installed [live: absent from list].
- Two Saturday-night retrain paths exist: the in-process `router_selftrain_weekly` job (`main.py:1487`, default off) and the `zoe-router-retrain` unit (shadow unless the struggle feed is on) [code].
- `multica_poll` (the engineering harness loop) and an unconditional `livekit_agent` task start inside zoe-data's lifespan (`main.py:1504,2055-2062`) [code].
- Night window: 02:50 start, about 9-30 minutes, household stack offline throughout [live].

### 1.11 Hardware allocation
- 15.3 GiB usable unified memory; 50 GB swapfile, 2 GB used; MemAvailable 2.96 GB at 20:18, 1.85 GB at 20:25 while a test lane ran [live].
- Household stack resident: llama 5.87 GB + Kokoro 2.15 GB + zoe-data 1.49 GB + router 0.65 GB = about 10.2 GB, none of it swapped (VmSwap 0 on all four) [live].
- Development stack resident at the same moment: two agent CLIs (~0.92 GB and ~0.40 GB), Serena 0.33 GB, Omnigent, codex daemon, GitHub runner, a pytest lane, plus the 9-day scratch harness container [live].
- Floors: voice replay floor 700/1500 MB, night window floor 1,200 MB, restore drill floor 1,100-2,000 MB [live: ground truth, logs]. Two of the last eight nights aborted on the 1,200 MB floor [live].
- GPU: the 4B runs all 99 layers on GPU; the router runs on CPU; Kokoro on CUDA [live].

### 1.12 Performance limits (measured)
- Brain call wall time on Flue turns since 10-30: p50 1.99 s, p90 3.82 s, max 8.5 s, n=51 [live: `FLUE_TOOLS ms=`]. This is the whole brain call, not TTFT.
- 10-19 measurement (organic voice): brain TTFT p50 1.1 s, p90 5.4 s, p95 6.4 s; STT p95 2.2 s; pre-brain p95 1.0 s; two-stage router p50 252 ms, p90 734 ms [record: lane-L-traffic.md:66-82]. End-to-end p90 about 6 s [record: synthesis 10-29].
- Cold first turn in the gate: 99% prefix cached, brain TTFT 420-437 ms vs baseline 497 ms [live: voice-regression.log].
- 12B decode 6.0-7.0 tok/s across the last week of nights [live].
- Harness share: 97% of brain turns and 96% of STT calls were harness traffic as of 10-19 [record]. Today's markers still cannot separate classes (`traffic=` tag not present in the app-log lines sampled) [live].

### 1.13 Architectural weaknesses (summary; ranked in section 4)
One process carries everything; three brain lanes; memory that does not reach the prompt; persona+tool schema crowd out history; the box doubles as the dev machine; flags and env accumulate; docs lag the box.

### 1.14 Improve / replace / consolidate / eliminate (pointers only, not a design)
- Eliminate: the dead env (45 of 178 live `ZOE_*` variables are read by no code, including 15 `ZOE_PI_*` Hermes-era hybrid flags, 11 Whisper, kanban budgets) [live vs `docs/knowledge/flag-inventory.json`]; the legacy and core brain lanes after a decision; the scratch harness container; stubs in `routers/stubs.py:16-39` [code].
- Consolidate: Multica autopilots and kanban budgets out of the household process; the five tool-schema lists [record].
- Improve: memory reaching the prompt; Kokoro stability under probes; restore drill and off-box backup.

### 1.15 Underutilised capabilities
See section 5.

### 1.16-1.18 Risks, unknowns, demonstrated vs intended
Sections 6, 7 and 2.

## 2. Intended vs demonstrated

| Area | Docs claim | Code at b51c94713 | Live 2026-11-03 | Verdict |
|---|---|---|---|---|
| Brain build | b11194 (`CANONICAL.md`) | unit template not re-read | b11982 running | [contradicted] doc stale |
| Brain lane | flue live, core default in code | three lanes, precedence not failover (`brain_dispatch.py:4-39`) | flue | matches |
| Router | two-stage active | numpy stage 1 + FunctionGemma | active, r2 Q8_0 on CPU | matches |
| Alarms | "P1 dark" (11-02 record), default off (`flag_registry.py:99`) | P1-P3 merged | `ZOE_ALARMS=on` | [contradicted] live ahead of doc |
| Recall reaches replies | "Samantha-grade memory" (`VISION.md`) | floor shape-gated (`zoe_flue_client.py:734`); gate shadow (`recall_gate.py:1`) | recall_memory 0/51, continuity 0/91, gate served 0 | not demonstrated |
| Night mind | ROADMAP: owner sets `serve` | `serve` handled (`night_mind.py:198`), registry omits it | unset in zoe-data; window writes, idle 11-03 | readers still off |
| Night window | nightly 12B | load retries (#2244) | 5 ok of 8; 2 floor aborts; 1 load fail | partial |
| Kokoro | only voice, stable across deploys (#2233) | deploy pin test | 24-51 starts on dev days, 2 on quiet days; down 20:17-20:19 today | partial |
| Voice gate | nightly regression | probe + baseline | nightly failed STT speed 11-02; unit failed | red, flaky |
| Backups | off-box + weekly drill (ROADMAP Now 3) | drill script | drill refused on memory; off-box not configured | not demonstrated |
| Self-evolution | nightly loop | design only | nothing runs | planned-only |
| Struggle detector | phase 1-3 | built | shadow, 5 hits in 2 days | partial |
| Music | MA live (`CANONICAL.md`) | on-demand start | container exited 4 weeks | idle; usage unverified |
| Engineering harness out of zoe-data | cut list (10-29) | Multica sync in `main.py` path | 8 autopilot jobs in zoe-data scheduler | not done |
| ROADMAP Now A1 (event loop) | open | `asyncio.to_thread(_sr.route…)` at `fast_tiers.py:635`, `voice_tts.py:3590` | lag warnings still fire (34 since 11-01) | code done, symptom remains |
| ROADMAP Now A2 (music auth) | open | `Depends(require_member_or_panel)` at `routers/music.py:352,379` | not probed | [contradicted] ROADMAP stale (dated 10-19) |
| PLANS live flags | `ZOE_ROUTER_SHADOW_TEXT=1` still | - | `ZOE_ROUTER_SHADOW_TEXT=0` | [contradicted] PLANS stale |
| Speaker model | ROADMAP Next 10 says `dual` switch | ResNet34 only (#2228 removed Resemblyzer) | `resnet34` | ROADMAP stale |

## 3. Strengths worth preserving

1. **The rocks and their lock-in.** A CI-pinned model contract and a single canonical page stop re-litigation (`docs/CANONICAL.md`, `test_canonical_invariants.py`) [code].
2. **Measured voice path.** Per-stage markers (`FLUE_PROMPT_CACHE`, `FLUE_CONTEXT_BUDGET`, `PANEL_KV_MISS`, `BRAIN_SLOT_GUARD`, `PREFIX_KEEPER`, `EVENT_LOOP_LAG`) log names and numbers, never text, which makes this lane's measurements possible [live].
3. **Brain slot discipline.** Slot guard + prefix keeper + household preemption protect the one slot; cold first turn 99% cached in the gate [live].
4. **Memory authority and forgetting.** Authority classes, hold-the-fact, quote retirement, forgotten-means-forever enforced [live flags; record 237/237].
5. **Safety floors that hold.** The night window refuses below its floor and restores in a fixed order with health polling; the voice stack never swaps (VmSwap 0) [live].
6. **Honest failure modes.** `set_timer` confirms only a real Skybridge timer and treats the canned line as unconfirmed (`zoe-tools.ts:541-575`); Kokoro down means silence, not a cloud voice; the write-claim guard wraps every brain turn (`brain_dispatch.py:953-971`) [code].
7. **Ground truth tooling.** `zoe_ground_truth.sh` reads what runs, and found two doc contradictions in this lane within a minute [live].

## 4. Weaknesses that block the Samantha direction (ranked)

1. **What Zoe knows does not reach what she says.** Recall tool 0/51, continuity 0/91, recall gate serving 0, night-mind readers off, history ~164 tokens of a ~3,750-token prompt [live]. This is the single largest gap between the vision and the box.
2. **The box is a development machine first.** Kokoro restarts, voice-gate flakiness, night aborts on memory, restore-drill refusals and MemAvailable dips all correlate with development activity [live]. The household's reliability is set by the fleet.
3. **Silence as a failure mode.** Kokoro is the only voice and is stopped by probes; there is no spoken degraded state when it is down [code + live]. #2249/#2232/#2234 mitigate; not verified over a full day.
4. **One process carries everything.** 196k lines, 443 routes, the voice path, memory, Multica autopilots and scheduling share one event loop that still stalls up to 0.74 s [live].
5. **Usage signal is thin.** About 51 brain turns in 4.5 days (46 panel, 5 ask), people table untouched since 10-04 [live]. Self-improvement has nothing to learn from, and the harness remains the heaviest user [record].
6. **Accumulated generations.** Three brain lanes, 842 inventoried flags plus 45 live env variables nothing reads, retired-system config still set, stubs and unused routes [code + live].
7. **Docs lag the box.** CANONICAL build, ROADMAP Now (dated 10-19), PLANS flag block and the 11-02 record's alarms status are each wrong in at least one line today [contradicted].

## 5. Underutilised

- **Brain tools:** 22 defined (23 with web search), 1 call in 51 turns [code + live].
- **`recall_memory` tool:** 0 calls [live].
- **Night mind output:** written nightly when there is input, read by nothing in zoe-data [live].
- **Recall gate, restraint, structural claims, delivery ledger, struggle detector:** shadow; their logs exist and are unread [live + record].
- **Router self-train:** dormant since July; retrain timer not installed [live + record].
- **Music Assistant + music discovery:** discovery flag on, weekly job registered, MA container stopped 4 weeks [live].
- **Routes with no caller:** 159 of 492 in the 11-02 sweep [record]; today's decorator count is 443, consistent with removals since (#2252) [code].
- **The 12B:** about 9 minutes of work per night out of a ~64-minute budget [live: `min=9.2/64.2`].
- **Omi pendant:** 261 recordings stored, 164 empty transcripts, 177 deferred since 11-01 [live]; day review in shadow.

## 6. Risks by class (no exposure detail)

- **Forget reach is incomplete and the brain store is uncounted.** The generated inventory lists 107 stores: 36 reached, 30 out of scope, **41 gaps**, including the STT log, the voice sample corpus, app logs, backups, and on member erase the chat tables and people graph (`docs/knowledge/forget-reach.md:81`; `services/zoe-data/forget_reach.py:93-115,191-193`) [code]. The Flue brain transcript DB (`labs/flue-zoe-brain-2x/src/db.ts:43`) is not in that inventory at all [code].
- **Privacy at the edges.** Raw transcript text is logged in plaintext by `STT_CAPTURE` (`routers/voice_tts.py:3037`) and audio is saved (`ZOE_VOICE_SAVE_AUDIO=1`); the brain's own transcript store is outside zoe-data's forget path [code + live; record for forget reach]. The flag inventory in the public tree carries a household locale value [code].
- **Public repository.** Household context keeps drifting into tracked files; the decision to go private is still open [record].
- **Data durability.** No off-box backup; the weekly restore drill is refusing to run on memory grounds, so backups are unverified [live].
- **Authentication surface.** The 10-19 private review's live network items were "unchanged" on 11-02 [record]; not re-checked here by design.
- **Root-equivalent service user.** No tracked unit has a `User=` line; the P0 de-root drop-ins exist but nothing installs them (`docs/architecture/p0-deroot.md:3-5`) [code].
- **Over-broad tools.** The MCP server includes a panel remote-command tool with no command allowlist in its handler (`mcp_server.py:2878-2934`), still advertised in the legacy agent prompt (`zoe_agent.py:218`) [code]. Internal trust accepts loopback unless the dispatch-token flag is set (`auth.py:373-453`) [code].
- **Retired-system residue on live paths.** After #2252, `routers/system.py` still has about 60 Hermes/OpenClaw mentions, including a proposal-review call that fails open (`system.py:1653-1675`) and contradictory `/delegate` errors (`:1147-1160`) [code].
- **Alerting.** Telegram alert token returned 401s from 10-27 [record]; not re-checked.
- **Reliability.** Single worker, single slot, single voice; memory floors routinely approached; two failed units today (voice regression, backup verify) [live].
- **Instrument trust.** Voice gate flips across consecutive runs; previous rounds found fail-open instruments [live + record].
- **Engineering in the household process.** Multica autopilots with execution budgets and `ZOE_PI_ALLOW_EXECUTION=true` set in zoe-data's environment (the latter read by no code at this head) [live].

## 7. What I could not verify, and why

- **Per-day household turn counts from Postgres.** A read-only `psql` count was refused by the session's permission policy; household volume above comes from log markers only.
- All four code sweeps (brain path, memory, voice, operations) reported and are folded into sections 1.5-1.10 and 6; their citations were read by the sweeps, not each re-opened by me. Route counts differ by method: 443 `@router.<verb>` decorators by my count, about 467 router decorators plus 15 `@app` routes by the operations sweep. Items that depended on the missing two are tagged [record] or omitted.
- **The panel's own env** (which speaker model the daemon uses) was not read; ssh to the panel was out of scope.
- **End-to-end voice latency today.** Only brain-call wall time is in today's logs; STT and first-sound percentiles are from the 10-19 record.
- **Traffic class.** No `traffic=` marker in sampled app-log lines, so harness vs household could not be split today.
- **Network exposure, tokens, de-root status:** out of scope for a public record; held in the private review.
- **Music usage.** The container being stopped for 4 weeks is verified; whether anyone asked for music in that period is not.

Records used as hypotheses: `docs/research/architecture-deep-dive-2026-10-18.md`, `architecture-deep-dive-2-2026-10-19.md` (lane L numbers), `stack-currency-and-field-2026-10-27.md`, `synthesis-deep-dive-2026-10-29.md`, `completeness-and-issues-2026-11-02.md`, `docs/CANONICAL.md`, `docs/VISION.md`, `docs/ROADMAP.md`, `docs/PLANS.md`, `docs/knowledge/recall-gate.md`, `docs/architecture/self-evolution-nightly-loop.md`, `docs/architecture/person-centred-memory.md`, `docs/architecture/samantha-evolution-plan.md`.
